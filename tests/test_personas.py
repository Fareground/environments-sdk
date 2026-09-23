import fg_env


def _people():
    return [
        {"id": "a1", "household": "a", "region": "north", "weight": 2},
        {"id": "a2", "household": "a", "region": "north", "weight": 2},
        {"id": "b1", "household": "b", "region": "south", "weight": 1},
        {"id": "b2", "household": "b", "region": "south", "weight": 1},
        {"id": "c1", "household": "c", "region": "north", "weight": 3},
        {"id": "d1", "household": "d", "region": "north", "weight": 1},
    ]


def test_persona_sampling_is_reproducible_grouped_and_does_not_mutate_pool():
    pool = _people()
    first = fg_env.personas.sample_records(pool, size=3, seed=9, constraints={"region": "north"},
                                  group_by="household", source="test", source_version="1")
    second = fg_env.personas.sample_records(pool, size=3, seed=9, constraints={"region": "north"},
                                   group_by="household", source="test", source_version="1")
    assert first.provenance.sampled_ids == second.provenance.sampled_ids
    ids = list(first.provenance.sampled_ids)
    if "a1" in ids and "a2" in ids:
        assert abs(ids.index("a1") - ids.index("a2")) == 1
    first.records()[0]["changed"] = True
    assert all("changed" not in person for person in pool)
    assert first.provenance.constraints == {"region": "north"}
    assert first.provenance.source_version == "1"


def test_fixed_cohort_and_resampling_are_explicit():
    fixed = [{"id": "fixed", "household": "fixed", "region": "north"}]
    stable_a = fg_env.personas.sample_records(_people(), size=4, seed=7, run=0, resample=False, fixed=fixed)
    stable_b = fg_env.personas.sample_records(_people(), size=4, seed=7, run=99, resample=False, fixed=fixed)
    fresh = fg_env.personas.sample_records(_people(), size=4, seed=7, run=99, resample=True, fixed=fixed)
    assert stable_a.provenance.sampled_ids == stable_b.provenance.sampled_ids
    assert stable_a.provenance.sampled_ids[0] == "fixed"
    assert fresh.provenance.run == 99 and fresh.provenance.resampled is True


def test_role_or_model_assignment_uses_largest_remainder_without_mutation():
    pool = _people()[:5]
    assigned = fg_env.personas.assign_labels(pool, [("a", 0.6), ("b", 0.2), ("c", 0.2)],
                                    field="model", seed=1)
    assert sorted(person["model"] for person in assigned) == ["a", "a", "a", "b", "c"]
    assert all("model" not in person for person in pool)
