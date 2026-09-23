"""An experiment table summarises list outputs by what they hold, not by their length."""
import fg_env


def contract():
    return {"name": "Two shops", "clock": {"rounds": 1},
            "types": {"shop": {"props": {"sales": 0}}},
            "entities": {"a": {"type": "shop", "name": "Alpha", "props": {"sales": 3}},
                         "b": {"type": "shop", "name": "Beta", "props": {"sales": 5}}},
            "events": [{"phase": "end", "do": "$entity(a).sales += $randint(0, 2)"}],
            "outputs": {"ranking": "$map($sort(shop, -$it.sales), [$it.name, $it.sales])",
                        "series": "[$entity(a).sales, $entity(b).sales]"}}


def test_a_ranking_is_summarised_per_label_and_a_series_per_position():
    result = fg_env.experiment(contract(), runs=4)
    runs = next(iter(result.arms.values())).runs
    alpha = sum(r.outputs["series"][0] for r in runs) / len(runs)
    lines = dict(line.split(": ", 1) for line in result.table().splitlines())
    assert f"Alpha {alpha:.4g}" in lines["ranking"] and "Beta 5" in lines["ranking"], lines
    assert f"[{alpha:.4g}, 5]" in lines["series"], lines
    assert "lists of" not in result.table()
