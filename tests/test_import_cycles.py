"""No two modules of the package import each other at load time, directly or through others, and load-time imports
only point down the package's layers.

A module-level import cycle makes load order matter: whichever module is imported first sees the other half-built.
Imports inside functions (deferred to first use) and under ``TYPE_CHECKING`` do not run at load and are allowed.
"""
import ast
from pathlib import Path

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
            targets = _targets(name, path, node, modules)
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
    ("world", "physics", "assets", "effects", "actions", "mechanisms", "host", "stdlib", "patterns"),
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
