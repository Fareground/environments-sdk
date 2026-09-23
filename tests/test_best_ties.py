"""$best gives one item however the ranking falls — a tie cannot turn a leader into a list and crash a view."""
import fg_env

TIED = {
    "name": "Tie",
    "types": {"player": {"agent": True, "props": {"cash": 0, "secret": 0}}},
    "entities": {"a": {"type": "player", "props": {"cash": 5}}, "b": {"type": "player", "props": {"cash": 5}}},
    "actions": {"wait": {"by": "player", "do": []}},
    "views": {"leader": {"show": "Leader: {$best(player, $it.cash).name}"}},
    "clock": {"rounds": 1},
    "outputs": {"leader": "$best(player, $it.cash).name", "tied": "$map($best(player, $it.cash, 'all'), $it.id)",
                "nobody": "$best(player, $it.cash, 'none')"},
}


def _play(wake):
    wake.update
    wake.call("wait")


def test_a_tied_best_is_still_one_item_and_all_lists_every_tied_item():
    shown = []
    result = fg_env.run(TIED, lambda wake: shown.append(wake.update) or wake.call("wait"), seed=3)
    assert result.ok, result.error
    assert result.outputs["leader"] in ("a", "b") and f"Leader: {result.outputs['leader']}" in shown[0]
    assert result.outputs["tied"] == ["a", "b"] and result.outputs["nobody"] is None
    assert fg_env.run(TIED, _play, seed=3).outputs == result.outputs  # the tie-break is seeded


def test_reading_a_field_of_a_list_names_the_fix_without_showing_the_items():
    contract = {**TIED, "outputs": {"leader": "$best(player, $it.cash, 'all').name"}}
    issues = fg_env.check(contract)
    message = next(i.message for i in issues if i.path == "outputs.leader")
    assert "cannot read '.name' of a list (2 items); pick one first" in message
    assert "secret" not in message and "Entity" not in message
