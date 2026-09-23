"""Every python and bash sample in the README and the hand-written guide pages runs as written, first time.

A page's samples run in order in one fresh folder, the python ones sharing one namespace, as a reader following the
page would run them. Pages that use ``inventory.json`` find the contract the quickstart saves. LLM clients are
stand-ins that end each turn. A sample that cannot run here (it installs a package, needs an API key or names a
placeholder) is marked on the line before it with ``<!-- not run: why -->``.
"""
import re
import runpy
import shlex
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from fg_env.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "README.md", *sorted(p for p in (ROOT / "docs" / "sdk").glob("*.md")
                                    if not p.name.startswith("reference"))]
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


def _quickstart_contract() -> str:
    text = (ROOT / "docs" / "sdk" / "getting-started.md").read_text(encoding="utf-8")
    return re.search(r"```json\n(.*?)```", text, re.S)[1]


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
    (tmp_path / "inventory.json").write_text(_quickstart_contract(), encoding="utf-8")
    namespace: dict = {"__name__": "__main__"}
    for number, (lang, code) in enumerate(samples, 1):
        started = time.process_time()
        try:
            exec(compile(code, f"{page.name} sample {number}", "exec"), namespace) if lang == "python" else _bash(code)
        except BaseException as exc:
            raise AssertionError(f"{page.name} sample {number} ({lang}) failed:\n{code}") from exc
        took = time.process_time() - started
        assert took < SLOW, f"{page.name} sample {number} took {took:.1f} CPU seconds:\n{code}"
