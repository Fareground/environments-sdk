"""No shipped contract shows an agent another agent's private values: every example, engine starter and template,
played with random agents that read everything they are offered (_leaks.py). Generated contracts are scanned in
test_fuzz_contracts.py."""
import pytest
from _leaks import EXAMPLES, SMALL, scan

import fg_env
from fg_env.authoring.scaffold import TEMPLATES

pytestmark = pytest.mark.slow  # statistical or engine-behaviour: `make test-fast` leaves it out

ROUNDS = 3


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_no_example_shows_an_agent_another_agents_private_values(path):
    scanner = scan(path, rounds=ROUNDS, inputs=SMALL.get(path.stem))
    assert scanner.result.status != "failed", scanner.result.error
    assert scanner.leaks == []


@pytest.mark.parametrize("engine", [spec.id for spec in fg_env.engines.list_engines()])
def test_no_engine_starter_shows_an_agent_another_agents_private_values(engine, tmp_path):
    path = fg_env.engines.clone(engine, tmp_path / f"{engine}.json")
    scanner = scan(path, rounds=ROUNDS, inputs=SMALL.get(engine))
    assert scanner.result.status != "failed", scanner.result.error
    assert scanner.leaks == []


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_no_template_shows_an_agent_another_agents_private_values(template):
    scanner = scan(fg_env.new(template), rounds=ROUNDS)
    assert scanner.result.status != "failed", scanner.result.error
    assert scanner.leaks == []


def test_the_scanner_finds_a_private_value_the_rules_copy_into_a_shared_record():
    contract = {
        "name": "Diary", "clock": {"rounds": 1},
        "types": {"p": {"agent": True, "props": {"pin": {"type": "text", "default": "", "private": True}}}},
        "entities": {"ann": {"type": "p", "props": {"pin": "zqkvtmwrh"}}, "bob": {"type": "p"}},
        "records": {"diary": {"fields": {"text": "text"}, "show": "{text}"}},
        # logic may copy (through a local, which the engine cannot follow into the post); the page is public
        "actions": {"write": {"by": "p", "do": ["$copy = $actor.pin", {"post": "diary", "text": "$copy"}]}},
        "stages": [{"name": "s", "order": "$it.id"}],
    }
    assert scan(contract).leaks == ["bob (round 1, turn) was shown ann.pin = 'zqkvtmwrh'"]
