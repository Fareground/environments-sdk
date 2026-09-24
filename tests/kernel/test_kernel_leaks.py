"""No leak: no agent is shown another agent's private values, and every template rendered for agents goes through
one gate.

The leak scanner (``tests/_leaks.py``) plays the generated contracts with every agent reading everything it is offered.
The gate test scans the package's source: a template render — ``compile_template(...).render(...)``, or ``.render`` of a
name bound to ``compile_template(...)`` in the same function — belongs in the perception layer (today
``runtime/perception.py``; the rebuild's ``Information``), or must bind ``viewer=EVERYONE`` so what it shows is what
every agent may see. The renders elsewhere today are listed below; the list may only shrink.
"""
import ast
from collections import Counter
from pathlib import Path

import _leaks
import pytest
from _corpus import FUZZ_FAST, FUZZ_SLOW, clean_fuzz, clean_seed

import fg_env

PACKAGE = Path(fg_env.__file__).parent
#: Where agent-facing text is rendered (the rebuild's Information layer).
GATE = {"runtime/perception.py"}
#: The expression language itself, where templates are defined and compiled.
LANGUAGE = "expr/"

# TODO(step 7): route each of these through Information.render (or bind viewer=EVERYONE where every agent may read the
# text), deleting its entry; the list must be empty once step 7 lands.
UNGATED = Counter({
    "actions/book.py::ActionBook._render": 1,  # an action's outcome (viewer: the actor) and announcement (everyone)
    "actions/book.py::ActionBook._unmet": 1,  # a `when` requirement's `why`, told to the actor
    "actions/validation.py::ActionValidation._validate": 1,  # a parameter's `invalid` text, told to the actor
    "effects/runner.py::EffectRunner.text": 1,  # effect texts: posts, news, a create/remove event's `say`
    "mechanisms/host_personas.py::_render": 1,  # a persona template, rendered for a host at build
    "mechanisms/procedure_stack.py::_describe": 1,  # a procedure item's `show`
    "mechanisms/status.py::_say": 1,  # a status mechanism's news line
    "runtime/checks.py::RunChecks._check_end": 1,  # an end condition's `say`
    "runtime/turn.py::Turn.invalid": 1,  # a stage `valid` rule's `why`, told to the acting agent
    "runtime/turn_tools.py::HostWake._commit": 1,  # an in-turn host tool's outcome (viewer: the actor)
    "world/build.py::_generate": 2,  # generated entity ids and names (every agent may read them)
    "world/build.py::build_world": 1,  # per-entity briefs (viewer: the entity)
})


def _binds_everyone(node: ast.AST) -> bool:
    return any(isinstance(sub, ast.keyword) and sub.arg == "viewer" and isinstance(sub.value, ast.Name)
               and sub.value.id == "EVERYONE" for sub in ast.walk(node))


def _is_compile(node: ast.AST) -> bool:
    """``compile_template(...)``, or a conditional one of it (``compile_template(...) if spec.id else None``)."""
    if isinstance(node, ast.IfExp):
        return _is_compile(node.body) or _is_compile(node.orelse)
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "compile_template"


class _Renders(ast.NodeVisitor):
    """Template renders outside the gate that do not bind ``viewer=EVERYONE``, by enclosing function."""

    def __init__(self, where: str):
        self.where, self.scopes, self.found = where, [], Counter()
        self.compiled: list[set[str]] = [set()]

    def _function(self, node):
        self.scopes.append(node.name)
        self.compiled.append({target.id for sub in ast.walk(node) if isinstance(sub, ast.Assign)
                              and _is_compile(sub.value) for target in sub.targets if isinstance(target, ast.Name)})
        self.generic_visit(node)
        self.compiled.pop()
        self.scopes.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def visit_ClassDef(self, node):
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "render" and (
                _is_compile(func.value) or (isinstance(func.value, ast.Name) and func.value.id in self.compiled[-1])):
            if not any(_binds_everyone(arg) for arg in [*node.args, *node.keywords]):
                self.found[f"{self.where}::{'.'.join(self.scopes) or '<module>'}"] += 1
        self.generic_visit(node)


def _ungated_renders() -> Counter:
    found: Counter = Counter()
    for path in sorted(PACKAGE.rglob("*.py")):
        where = path.relative_to(PACKAGE).as_posix()
        if where in GATE or where.startswith(LANGUAGE):
            continue
        scan = _Renders(where)
        scan.visit(ast.parse(path.read_text(), where))
        found += scan.found
    return found


def test_agent_text_has_one_gate():
    found = _ungated_renders()
    added = found - UNGATED
    assert not added, f"templates rendered outside the perception layer without viewer=EVERYONE: {dict(added)}"
    gone = UNGATED - found
    assert not gone, f"routed through the gate or removed: delete from UNGATED {dict(gone)}"


def test_the_gate_scan_finds_a_render_outside_the_gate():
    source = ("from x import compile_template, EVERYONE\n"
              "def shown(scope):\n"
              "    text = compile_template('{$a}', None)\n"
              "    return text.render(scope), compile_template('{$b}').render(scope.child(viewer=EVERYONE))\n")
    scan = _Renders("probe.py")
    scan.visit(ast.parse(source))
    assert scan.found == Counter({"probe.py::shown": 1})


def _no_leak(seed):
    scanner = _leaks.scan(clean_fuzz(clean_seed(seed)), seed=seed)
    assert scanner.result.status in ("completed", "ended"), scanner.result.error
    assert scanner.leaks == []


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_no_generated_contract_shows_an_agent_another_agents_private_values(fuzz):
    _no_leak(fuzz)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_no_generated_contract_of_many_shows_an_agent_another_agents_private_values(fuzz):
    _no_leak(fuzz)
