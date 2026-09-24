"""A list of distinct choices is bounded by its candidates, not by the cap on free lists: a ranking of every applicant
fits however many apply, and a list of free values keeps the cap."""
import fg_env

APPLICANTS = 1_500


def _ranking(**param):
    return {"name": "Rank", "clock": {"rounds": 1},
            "types": {"selector": {"agent": True, "props": {"prefs": {"type": "list", "default": []}}},
                      "applicant": {}},
            "entities": {"s": {"type": "selector"}},
            "population": [{"type": "applicant", "count": APPLICANTS}],
            "actions": {"rank": {"by": "selector", "params": {"order": {"type": "list", "of": "applicant", **param}},
                                 "do": ["$actor.prefs = $map($params.order, $it.id)"]}},
            "outputs": {"ranked": "$len($entity(s).prefs)"}}


def test_a_ranking_longer_than_the_free_list_cap_is_accepted():
    told, schemas = [], []

    def rank_all(wake):
        schemas.append(next(t for t in wake.tools if t.name == "rank").input_schema["properties"]["order"])
        told.append(wake.call("rank", {"order": [f"applicant_{i}" for i in range(1, APPLICANTS + 1)]}))

    result = fg_env.load(_ranking(), seed=1).run({"s": rank_all})
    assert told[0].ok, told[0].text
    assert result.outputs["ranked"] == APPLICANTS and "maxItems" not in schemas[0]


def test_a_ranking_keeps_its_own_max_items():
    env = fg_env.load(_ranking(max_items=3), seed=1)
    told = []
    env.run({"s": lambda wake: told.append(wake.call("rank", {"order": ["applicant_1", "applicant_2", "applicant_3",
                                                                        "applicant_4"]}))})
    assert not told[0].ok and "at most 3 item(s)" in told[0].text


def test_a_list_of_free_values_keeps_the_cap():
    free = {**_ranking(), "actions": {"say": {"by": "selector", "params": {
        "words": {"type": "list", "items": {"type": "text"}, "max_items": 5_000}}}}}
    [found] = [i for i in fg_env.check(free, rounds=0) if i.path == "actions.say.params.words.max_items"]
    assert "more than the limit of 1000 for a list of free values" in found.message
