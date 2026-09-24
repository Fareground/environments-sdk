"""Every way a contract is read rewrites its earlier forms the same way, and keeps a note of each rewrite: parse,
check, imports, expand, arm patches and fork patches."""
import json

import fg_env
from fg_env.api import contract_source

EARLIER = {"name": "Earlier", "clock": {"rounds": 2}, "world": {"n": 0},
           "types": {"p": {"agent": True, "props": {"x": 0}}}, "entities": {"a": {"type": "p"}},
           "actions": {"bump": {"by": "p", "do": "$actor.x += 1", "private": True}},
           "triggers": [{"when": "$world.n > 1", "do": "$world.n = 0"}],
           "metrics": {"xs": "$sum(p, $it.x)"},
           "arms": {"fast": {"patch": {"events": [{"phase": "end", "do": "$world.n += 2"}]}}}}


def _notes(contract):
    return contract._notes


def test_parse_keeps_a_note_of_every_rewrite_and_the_current_form_has_none():
    notes = _notes(fg_env.parse(EARLIER))
    assert any(note.startswith("triggers") for note in notes) and any(note.startswith("metrics") for note in notes)
    assert any(note.startswith("actions.bump.private") for note in notes)
    assert any(note.startswith("arms.fast.patch.") for note in notes)
    assert _notes(fg_env.parse(contract_source(fg_env.parse(EARLIER)))) == []


def test_expand_parse_and_check_read_the_same_current_form():
    expanded = fg_env.expand(EARLIER)
    assert expanded == contract_source(fg_env.parse(EARLIER)) == fg_env.expand(expanded)
    assert fg_env.expand(EARLIER, mechanisms=True) == fg_env.expand(expanded, mechanisms=True)
    assert [i.message for i in fg_env.check(EARLIER, rounds=0)] == [i.message for i in fg_env.check(expanded, rounds=0)]


def test_an_imported_file_in_an_earlier_form_is_rewritten_and_noted_with_its_place(tmp_path):
    (tmp_path / "part.json").write_text(json.dumps({"metrics": {"xs": "$sum(p, $it.x)"}}))
    main = {**{k: v for k, v in EARLIER.items() if k not in ("metrics", "triggers", "arms")},
            "imports": ["part.json"]}
    (tmp_path / "main.json").write_text(json.dumps(main))
    contract = fg_env.parse(tmp_path / "main.json")
    assert contract.outputs["xs"].series is True
    assert any(note.startswith("imports[0]") and "metrics" in note for note in _notes(contract))


def test_a_fork_patch_in_an_earlier_form_is_rewritten_and_noted():
    env = fg_env.load(contract_source(fg_env.parse(EARLIER)), seed=1)
    env.run("idle", rounds=1)
    forked = env.fork(patch={"metrics": {"twice": "$sum(p, $it.x) * 2"}})
    assert forked.contract.outputs["twice"].series is True
    assert any(note.startswith("metrics") for note in _notes(forked.contract))
