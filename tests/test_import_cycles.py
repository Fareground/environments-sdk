"""No two modules of the package import each other at load time, directly or through others, and load-time imports
only point down the package's layers.

A module-level import cycle makes load order matter: whichever module is imported first sees the other half-built.
Imports inside functions (deferred to first use) and under ``TYPE_CHECKING`` do not run at load and are allowed.
"""
import ast
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import fg_env

PACKAGE = Path(fg_env.__file__).parent


def _modules():
    modules = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = ["fg_env", *path.relative_to(PACKAGE).with_suffix("").parts]
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def _load_time_imports(tree):
    """Import statements that run when the module loads: at module level, including under ``if``/``try``."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If):
            if not (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"):
                pending.extend(node.body + node.orelse)
        elif isinstance(node, ast.Try):
            pending.extend(node.body + node.orelse + node.finalbody + [s for h in node.handlers for s in h.body])


def _targets(name, path, node, modules):
    """The package modules one import statement in module ``name`` loads."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    package = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    base = node.module or ""
    if node.level:
        base = ".".join(package[: len(package) - node.level + 1] + ([base] if base else []))
    return [f"{base}.{a.name}" if f"{base}.{a.name}" in modules else base for a in node.names]


def _graph(modules):
    graph = {}
    for name, path in modules.items():
        edges = set()
        for node in _load_time_imports(ast.parse(path.read_text())):
            # Loading a module first runs every package above it: importing `a.b.c` runs `a.b/__init__.py` too.
            targets = {".".join(t.split(".")[:end]) for t in _targets(name, path, node, modules)
                       for end in range(2, t.count(".") + 2)}
            # A module's own parent packages are loaded before it anyway: importing from them is not a cycle.
            edges.update(t for t in targets if t in modules and t != name and not name.startswith(t + "."))
        graph[name] = edges
    return graph


def _cycles(graph):
    """Strongly connected components with more than one module (Tarjan)."""
    index, low, stack, on_stack, found = {}, {}, [], set(), []

    def visit(node):
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for other in graph[node]:
            if other not in index:
                visit(other)
                low[node] = min(low[node], low[other])
            elif other in on_stack:
                low[node] = min(low[node], index[other])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for node in graph:
        if node not in index:
            visit(node)
    return found


def test_no_module_level_import_cycles():
    cycles = _cycles(_graph(_modules()))
    assert not cycles, (
        "modules that import each other at load time: "
        + "; ".join(" <-> ".join(cycle) for cycle in cycles)
        + ". Break the cycle: move what both need into a module neither imports, or import inside the function that "
          "uses it.")


#: The package's layers, lowest first. A module imports (at load time) only from its own layer or the ones below:
#: the contract knows nothing of the language, the language nothing of the world, the world nothing of runs, and
#: nothing in the engine loads the analysis and authoring tools built on it. ``fg_env/__init__.py`` and
#: ``__main__.py``, the public face, sit above them all.
LAYERS = (
    ("errors", "registry", "sampling"),
    ("contract",),
    ("expr",),
    ("world", "physics", "assets", "effects", "actions", "information", "mechanisms", "host", "stdlib", "patterns"),
    ("runtime", "participants", "copying", "checks", "api", "experiments"),
    ("analysis", "report", "trace", "describe", "tournament", "evaluate", "game", "authoring", "guides", "engines",
     "personas", "rl", "cli"),
)


def test_load_time_imports_only_point_down_the_layers():
    layer = {part: rank for rank, parts in enumerate(LAYERS) for part in parts}
    modules = _modules()
    tops = {name.split(".")[1] for name in modules if name.count(".") >= 1 and name != "fg_env.__main__"}
    assert tops == set(layer), f"place every part of the package in LAYERS (unplaced: {sorted(tops - set(layer))})"
    upward = []
    for name, path in modules.items():
        if name in ("fg_env", "fg_env.__main__"):
            continue
        part = name.split(".")[1]
        for node in _load_time_imports(ast.parse(path.read_text())):
            for target in _targets(name, path, node, modules):
                other = target.split(".")[1] if target.startswith("fg_env.") else None
                if other in layer and layer[other] > layer[part]:
                    upward.append(f"{name}:{node.lineno} imports {target}")
    assert not upward, (
        "imports that point up the layers: " + "; ".join(sorted(upward))
        + ". Move what the lower module needs down to its layer, or pass it in from above.")


#: Imports inside functions that point up the layers: each is a lower part reaching into a higher one, a dependency
#: the load-time rule cannot see. The list only shrinks: a new one fails, and so does one that is gone.
DEFERRED_UPWARD = frozenset({
    "fg_env.checks.actions -> fg_env.describe.walk",
    "fg_env.effects.chance -> fg_env.checks.roots",
    "fg_env.effects.runner -> fg_env.runtime.diagnosis",
    "fg_env.experiments.experiment -> fg_env.analysis.draws",
    "fg_env.expr.codegen -> fg_env.stdlib.core",
    "fg_env.host -> fg_env.runtime.hosted",  # the package's run-level calls, re-exported on first use
    "fg_env.host.hosts -> fg_env.runtime.facts",
    "fg_env.host.tape -> fg_env.copying.snapshot",
    "fg_env.mechanisms._common -> fg_env.checks",
    "fg_env.participants.builtin -> fg_env.game.algorithms.participants",
    "fg_env.participants.builtin -> fg_env.trace.rerun",
    "fg_env.patterns.check -> fg_env.describe.walk",
    "fg_env.registry -> fg_env.expr",
    "fg_env.registry -> fg_env.world.abort",
    "fg_env.runtime.returns -> fg_env.describe.claims",
    "fg_env.runtime.returns -> fg_env.describe.metadata",
})


def _deferred_imports(tree):
    """Import statements inside functions (run on first use, not at load)."""
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    found = {id(node): node for function in functions for node in ast.walk(function)
             if isinstance(node, (ast.Import, ast.ImportFrom))}
    return list(found.values())


def test_imports_inside_functions_point_up_the_layers_only_where_listed():
    layer = {part: rank for rank, parts in enumerate(LAYERS) for part in parts}
    modules = _modules()
    upward = set()
    for name, path in modules.items():
        if name in ("fg_env", "fg_env.__main__"):
            continue
        part = name.split(".")[1]
        for node in _deferred_imports(ast.parse(path.read_text())):
            for target in _targets(name, path, node, modules):
                other = target.split(".")[1] if target.startswith("fg_env.") and target.count(".") else None
                if other in layer and layer[other] > layer[part]:
                    upward.add(f"{name} -> {target}")
    assert not upward - DEFERRED_UPWARD, (
        f"new imports inside functions that point up the layers: {sorted(upward - DEFERRED_UPWARD)}. Move what the "
        "lower module needs down to its layer, or pass it in from above.")
    assert not DEFERRED_UPWARD - upward, (
        f"gone, so remove them from DEFERRED_UPWARD: {sorted(DEFERRED_UPWARD - upward)}")


@pytest.mark.slow
def test_every_module_imports_on_its_own():
    # `import fg_env` loads almost nothing, so a module leaning on another having been imported first breaks here.
    def imports(name):
        done = subprocess.run([sys.executable, "-c", f"import {name}"], capture_output=True, text=True)
        return None if done.returncode == 0 else f"{name}: {done.stderr.strip().splitlines()[-1]}"

    with ThreadPoolExecutor(8) as pool:
        broken = [problem for problem in pool.map(imports, sorted(_modules())) if problem]
    assert not broken, "modules that fail when imported first: " + "; ".join(broken)


#: Load-time imports from one package to another that lie on a cycle of packages (world → effects → world …): each
#: ties the packages' load order together. The list only shrinks: a new one fails, and so does one that is gone.
PACKAGE_CYCLE_EDGES = frozenset({
    "actions -> assets", "actions -> effects", "actions -> information", "actions -> world", "api -> checks",
    "api -> runtime", "assets -> host", "authoring -> guides", "checks -> participants", "checks -> runtime",
    "copying -> api", "copying -> checks", "copying -> participants", "copying -> runtime", "effects -> information",
    "effects -> patterns", "effects -> world", "guides -> authoring", "host -> assets", "host -> world",
    "information -> actions", "information -> assets", "information -> effects", "information -> world",
    "participants -> runtime", "patterns -> stdlib", "physics -> world", "runtime -> copying",
    "runtime -> participants", "stdlib -> assets", "stdlib -> world", "world -> assets", "world -> effects",
    "world -> information", "world -> patterns", "world -> physics", "world -> stdlib",
})


def test_package_cycles_only_shrink():
    """Package-level cycles (audit 9 arch M1): the mechanisms left the one they formed with the effects when the effect
    runner stopped importing them to register their ops."""
    graph, edges = {}, set()
    for name, targets in _graph(_modules()).items():
        if name.count(".") < 1:
            continue
        part = name.split(".")[1]
        graph.setdefault(part, set())
        for target in targets:
            other = target.split(".")[1] if target.count(".") >= 1 else None
            if other and other != part:
                graph[part].add(other)
                graph.setdefault(other, set())
                edges.add((part, other))
    components = [set(component) for component in _cycles(graph)]
    found = {f"{a} -> {b}" for a, b in edges if any(a in c and b in c for c in components)}
    new = sorted(found - PACKAGE_CYCLE_EDGES)
    assert not new, f"new imports that close a cycle of packages: {new}"
    assert not PACKAGE_CYCLE_EDGES - found, f"gone, so remove them: {sorted(PACKAGE_CYCLE_EDGES - found)}"
