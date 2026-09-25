"""Every python and bash sample in the README and the docs pages runs as written, first time, and every cookbook
recipe checks clean and gives the known answer the cookbook states.

A page's samples run in order in one fresh folder, the python ones sharing one namespace, as a reader following the
page would run them. The start page's contract is saved as ``lake.json`` first, as the page tells its reader to. LLM
clients are stand-ins that end each turn. A sample that cannot run here (it installs a package, needs an API key or
names a placeholder) is marked on the line before it with ``<!-- not run: why -->``.
"""
import inspect
import json
import re
import runpy
import shlex
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.authoring.scaffold import RECIPES, new

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs" / "sdk").glob("*.md"))]
#: CPU seconds one sample may take (CPU, not wall time, so a busy machine does not fail it): a first-time reader
#: should not wait on a documented example.
SLOW = 10


def _samples(page: Path):
    """``(language, code)`` of each python and bash block on the page that is not marked as not run."""
    lines = page.read_text(encoding="utf-8").splitlines()
    start = None
    for number, line in enumerate(lines):
        if not line.startswith("```"):
            continue
        if start is None:
            start = number
            continue
        lang, marked = lines[start][3:].strip(), start > 0 and lines[start - 1].startswith("<!-- not run")
        if lang in ("python", "bash") and not marked:
            yield lang, "\n".join(lines[start + 1:number]) + "\n"
        start = None


def _start_page_contract() -> str:
    return re.search(r"```json\n(.*?)```", fg_env.guide("authoring"), re.S)[1]


class _StandInLLM:
    """An Anthropic or OpenAI client whose model answers every turn with text alone, ending it."""

    def __init__(self, *args, **kwargs):
        self.messages = self
        self.chat = NS(completions=self)

    def create(self, **request):
        usage = NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                   prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None)
        message = NS(content="done", tool_calls=None)
        return NS(content=[NS(type="text", text="done")], usage=usage,
                  choices=[NS(message=message, finish_reason="stop")])


def _bash(code: str) -> None:
    for line in code.replace("\\\n", " ").splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        if words[0] == "fg-env":
            assert main(words[1:]) == 0, line
        elif words[:1] == ["python"] and len(words) == 2:
            runpy.run_path(words[1], run_name="__main__")
        else:
            raise AssertionError(f"cannot run `{line}` here: mark its block <!-- not run: why -->")


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_sample_on_the_page_runs(page, tmp_path, monkeypatch, capsys):
    samples = list(_samples(page))
    if not samples:
        pytest.skip("no runnable samples")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "anthropic", NS(Anthropic=_StandInLLM))
    monkeypatch.setitem(sys.modules, "openai", NS(OpenAI=_StandInLLM))
    monkeypatch.setenv("SIMULATION_MODEL", "stand-in-model")
    (tmp_path / "lake.json").write_text(_start_page_contract(), encoding="utf-8")
    namespace: dict = {"__name__": "__main__"}
    for number, (lang, code) in enumerate(samples, 1):
        started = time.process_time()
        try:
            exec(compile(code, f"{page.name} sample {number}", "exec"), namespace) if lang == "python" else _bash(code)
        except BaseException as exc:
            raise AssertionError(f"{page.name} sample {number} ({lang}) failed:\n{code}") from exc
        took = time.process_time() - started
        assert took < SLOW, f"{page.name} sample {number} took {took:.1f} CPU seconds:\n{code}"


def test_a_model_docstring_reads_unindented_in_the_guide_on_every_python():
    # Python 3.13 strips docstring indentation itself; 3.11 and 3.12 keep it, which Markdown shows as code.
    from fg_env.contract import InputSpec

    assert inspect.cleandoc(InputSpec.__doc__) in fg_env.guide("inputs")


@pytest.mark.parametrize("name", list(RECIPES))
def test_every_cookbook_recipe_checks_clean_and_gives_its_known_answer(name):
    contract = new(name)
    assert [str(issue) for issue in fg_env.check(contract)] == []
    assert fg_env.run(contract, seed=1).ok  # random agents
    spec = RECIPES[name]
    result = fg_env.run(contract, spec.policy, seed=1)
    assert result.ok, result.summary()
    if spec.answer is not None:
        assert {key: result.outputs[key] for key in spec.answer} == spec.answer
        assert json.dumps(spec.answer[next(iter(spec.answer))]) in fg_env.guide(f"cookbook.{name}")


def test_the_cookbook_shows_every_recipe_as_the_file_new_writes(tmp_path):
    page = fg_env.guide("cookbook")
    for name in RECIPES:
        path = tmp_path / f"{name}.json"
        assert main(["new", name, str(path)]) == 0
        written = json.loads(path.read_text())
        shown = json.loads(fg_env.guide(f"cookbook.{name}").split("```json\n")[1].split("```")[0])
        assert written == {**shown, "name": written["name"]} and f"## {name}" in page


def _contracts():
    """Each JSON block on the pages that is a whole contract (it declares `types`) or uses a mechanism, and is not
    marked as not run: a reader pastes one as it is."""
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        for number, match in enumerate(re.finditer(r"```json\n(.*?)```", text, re.S), 1):
            before = text[:match.start()].rstrip("\n").rsplit("\n", 1)[-1]
            if before.startswith("<!-- not run"):
                continue
            block = match[1]
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue  # a fragment written for reading, not pasting
            if isinstance(data, dict) and ("types" in data or "mechanisms" in data):
                yield pytest.param(data, id=f"{page.stem}-{number}")


@pytest.mark.parametrize("contract", list(_contracts()))
def test_every_contract_on_a_page_checks_clean_and_plays_as_pasted(contract):
    """A contract or mechanism example on a docs page works when pasted as it is: it checks without errors and plays
    with random agents (audit 14 mech H1)."""
    errors = [str(issue) for issue in fg_env.check(contract, rounds=0) if issue.severity == "error"]
    assert errors == []
    result = fg_env.run(contract, "random", seed=1, rounds=3)  # its first rounds: enough to reach every rule once
    assert result.status != "failed", result.error
