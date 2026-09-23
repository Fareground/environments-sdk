"""`groups.matching`: deferred acceptance gives a stable, proposer-optimal match that respects seats."""
import itertools
import random

import pytest

import fg_env


def _market(students, schools, seats=1, agents=(), **extra):
    """A matching contract over coded entities whose rankings are props ({id: [ids, best first]})."""
    return {
        "name": "Admissions",
        "clock": {"rounds": 1},
        "types": {"student": {"agent": "student" in agents}, "school": {"agent": "school" in agents, "props": {"capacity": 1}}},
        "entities": {**{s: {"type": "student", "props": {"admit_prefs": prefs}} for s, prefs in students.items()},
                     **{c: {"type": "school", "props": {"admit_prefs": prefs, "capacity": seats if isinstance(seats, int)
                                                        else seats[c]}} for c, prefs in schools.items()}},
        "mechanisms": {"admit": {"kind": "groups", "mode": "matching", "who": "student", "to": "school",
                                 "seats": "$it.capacity", **extra}},
    }


def _matched(env):
    return {e["id"]: e["props"]["admit_match"] for e in env.entities("student")}


def _run(contract, participants="idle"):
    env = fg_env.load(contract, seed=1)
    assert env.run(participants).error is None
    return env


# The textbook 3×3 instance where the two sides' optimal stable matchings differ.
MEN = {"m1": ["w1", "w2", "w3"], "m2": ["w2", "w3", "w1"], "m3": ["w3", "w1", "w2"]}
WOMEN = {"w1": ["m2", "m3", "m1"], "w2": ["m3", "m1", "m2"], "w3": ["m1", "m2", "m3"]}


def test_proposers_get_their_best_stable_partner():
    env = _run(_market(MEN, WOMEN))
    assert _matched(env) == {"m1": "w1", "m2": "w2", "m3": "w3"}
    assert env.result().outputs["admit_matched"] == 3


def test_swapping_sides_gives_the_other_sides_optimum():
    env = _run(_market(WOMEN, MEN))
    assert _matched(env) == {"w1": "m2", "w2": "m3", "w3": "m1"}


def test_unlisted_partners_are_unacceptable_and_seats_are_respected():
    students = {"a": ["x"], "b": ["x", "y"], "c": ["x", "y"], "d": ["y"]}
    schools = {"x": ["c", "b", "a"], "y": ["b", "d"]}
    env = _run(_market(students, schools, seats={"x": 2, "y": 1}))
    assert _matched(env) == {"a": "", "b": "x", "c": "x", "d": "y"}
    receivers = {e["id"]: e["props"]["admit_matches"] for e in env.entities("school")}
    assert receivers == {"x": ["c", "b"], "y": ["d"]}


def _blocking_pairs(students, schools, seats, match):
    held = {c: [s for s, m in match.items() if m == c] for c in schools}
    pairs = []
    for s, prefs in students.items():
        current = prefs.index(match[s]) if match[s] else len(prefs)
        for c in prefs[:current]:
            if s not in schools[c]:
                continue
            full = len(held[c]) >= seats[c]
            worst = max((schools[c].index(t) for t in held[c]), default=-1)
            if not full or schools[c].index(s) < worst:
                pairs.append((s, c))
    return pairs


def _random_instance(rng, n_students, n_schools):
    students = {f"s{i}": rng.sample([f"c{j}" for j in range(n_schools)], rng.randint(0, n_schools)) for i in range(n_students)}
    schools = {f"c{j}": rng.sample(list(students), rng.randint(0, n_students)) for j in range(n_schools)}
    seats = {c: rng.randint(0, 3) for c in schools}
    return students, schools, seats


@pytest.mark.parametrize("seed", range(25))
def test_random_instances_are_stable_and_within_seats(seed):
    rng = random.Random(seed)
    students, schools, seats = _random_instance(rng, rng.randint(1, 8), rng.randint(1, 5))
    match = _matched(_run(_market(students, schools, seats=seats)))
    assert not _blocking_pairs(students, schools, seats, match)
    for c, capacity in seats.items():
        assert sum(1 for m in match.values() if m == c) <= capacity
    for s, m in match.items():
        assert not m or (m in students[s] and s in schools[m])


def _stable_matchings(students, schools):
    """Every stable one-to-one matching, by brute force."""
    ids = list(students)
    options = [[""] + [c for c in students[s] if s in schools[c]] for s in ids]
    for choice in itertools.product(*options):
        taken = [c for c in choice if c]
        match = dict(zip(ids, choice))
        if len(taken) == len(set(taken)) and not _blocking_pairs(students, schools, dict.fromkeys(schools, 1), match):
            yield match


@pytest.mark.parametrize("seed", range(15))
def test_random_one_to_one_instances_are_proposer_optimal(seed):
    rng = random.Random(100 + seed)
    students, schools, _ = _random_instance(rng, 4, 4)
    match = _matched(_run(_market(students, schools)))
    for other in _stable_matchings(students, schools):
        for s, prefs in students.items():
            rank = {c: i for i, c in enumerate(prefs)}
            assert rank.get(match[s], len(prefs)) <= rank.get(other[s], len(prefs))


def _ranker(rankings, calls):
    def play(wake):
        tool = next(t.name for t in wake.tools if t.name.startswith("admit_rank"))
        calls.append(tool)
        wake.call(tool, {"ranking": rankings[wake.entity_id]})
        wake.end()
    return play


def test_agents_rank_with_the_generated_tool_and_the_stage_clears_once():
    contract = _market({s: [] for s in MEN}, {w: [] for w in WOMEN}, agents=("student", "school"))
    contract["clock"] = {"rounds": 2}
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]
    calls = []
    env = _run(contract, _ranker({**MEN, **WOMEN}, calls))
    assert _matched(env) == {"m1": "w1", "m2": "w2", "m3": "w3"}
    assert [e["props"]["admit_matches"] for e in env.entities("school")] == [["m1"], ["m2"], ["m3"]]
    assert sorted(calls) == ["admit_rank_school"] * 3 + ["admit_rank_student"] * 3


def test_a_declared_stage_matches_when_it_ends_and_outputs_compose():
    contract = _market({s: [] for s in MEN}, WOMEN, agents=("student",), stage="apply")
    contract["stages"] = [{"name": "apply", "turns": "simultaneous"}]
    contract["outputs"] = {"m1_school": "$entity(m1).admit_match"}
    calls = []
    env = _run(contract, _ranker(MEN, calls))
    assert calls == ["admit_rank"] * 3
    assert env.result().outputs["m1_school"] == "w1"


def test_misconfigured_matching_says_how_to_fix_it():
    contract = _market(MEN, WOMEN)
    contract["mechanisms"]["admit"]["to"] = "student"
    with pytest.raises(fg_env.ContractError) as caught:
        fg_env.load(contract)
    assert "different types" in str(caught.value)
    contract["mechanisms"]["admit"]["to"] = "college"
    with pytest.raises(fg_env.ContractError) as caught:
        fg_env.load(contract)
    assert "not a declared type" in str(caught.value)


def _applied(contract, applied):
    contract["types"]["student"]["props"] = {"applied": {"type": "list", "default": []}}
    for student, schools in applied.items():
        contract["entities"][student]["props"]["applied"] = schools
    return contract


def test_eligible_limits_the_rank_tools_and_an_ineligible_pair_never_matches():
    # m1 applied only to w2. Unrestricted, the textbook instance matches m1-w1, m2-w2, m3-w3.
    applied = {"m1": ["w2"], "m2": ["w1", "w2", "w3"], "m3": ["w1", "w2", "w3"]}
    eligible = "$receiver.id in $proposer.applied"
    agents = _applied(_market({s: [] for s in MEN}, WOMEN, agents=("student",), eligible=eligible), applied)
    env = fg_env.load(agents, seed=1)
    schema = env.actions.tool(env.world.entities["m1"], "admit_rank").input_schema["properties"]["ranking"]
    assert schema["items"]["enum"] == ["w2"]
    replies = {}

    def rank(wake):
        replies[wake.entity_id] = wake.call("admit_rank", {"ranking": MEN[wake.entity_id]})
        wake.end()

    assert env.run(rank).error is None
    assert not replies["m1"].ok and replies["m2"].ok
    coded = _applied(_market(MEN, WOMEN, eligible=eligible), applied)  # coded rankings are cut to eligible partners
    assert _matched(_run(coded)) == {"m1": "w2", "m2": "w3", "m3": "w1"}
