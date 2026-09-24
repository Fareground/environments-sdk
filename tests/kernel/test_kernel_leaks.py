"""No leak: no agent is shown another agent's private values, and every template rendered for a reader goes through
one gate.

The leak scanner (``tests/_leaks.py``) plays the generated contracts with every agent reading everything it is offered.
The gate test scans the package's source: a template render — ``compile_template(...).render(...)``, or ``.render`` of a
name bound to ``compile_template(...)`` in the same function — belongs in the Information component (``information/``),
whose gate (``information/gate.py``) binds the reader every text is rendered for. Everywhere else renders through it.
"""
import ast
from collections import Counter
from pathlib import Path

import _leaks
import pytest
from _corpus import FUZZ_FAST, FUZZ_SLOW, clean_fuzz, clean_seed

import fg_env

PACKAGE = Path(fg_env.__file__).parent
#: Where agent-facing text is rendered: the Information component.
GATE = "information/"
#: The expression language itself, where templates are defined and compiled.
LANGUAGE = "expr/"


def _is_compile(node: ast.AST) -> bool:
    """``compile_template(...)``, or a conditional one of it (``compile_template(...) if spec.id else None``)."""
    if isinstance(node, ast.IfExp):
        return _is_compile(node.body) or _is_compile(node.orelse)
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "compile_template"


class _Renders(ast.NodeVisitor):
    """Template renders outside the gate, by enclosing function."""

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
            self.found[f"{self.where}::{'.'.join(self.scopes) or '<module>'}"] += 1
        self.generic_visit(node)


def _ungated_renders() -> Counter:
    found: Counter = Counter()
    for path in sorted(PACKAGE.rglob("*.py")):
        where = path.relative_to(PACKAGE).as_posix()
        if where.startswith((GATE, LANGUAGE)):
            continue
        scan = _Renders(where)
        scan.visit(ast.parse(path.read_text(), where))
        found += scan.found
    return found


def test_agent_text_has_one_gate():
    found = _ungated_renders()
    assert not found, f"templates rendered outside the gate (render through information/gate.py): {dict(found)}"


def test_the_gate_scan_finds_a_render_outside_the_gate():
    source = ("from x import compile_template, EVERYONE\n"
              "def shown(scope):\n"
              "    text = compile_template('{$a}', None)\n"
              "    return text.render(scope), compile_template('{$b}').render(scope.child(viewer=EVERYONE))\n")
    scan = _Renders("probe.py")
    scan.visit(ast.parse(source))
    assert scan.found == Counter({"probe.py::shown": 2})


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
