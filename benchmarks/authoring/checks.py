"""Fidelity checks per brief. Each reads only the outputs the brief's Deliverables section asks for (plus what agents
were shown, via the probe), so they hold whatever else the author names things.

``BRIEFS[slug] = (required outputs, [checks])``; a check raises ``AssertionError`` saying what is unfaithful.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, Iterable, List

from evaluate import Ctx, number_pattern

#: Extra random seeds for "this must happen at least once" and "this must hold in every run" checks.
MANY = range(1, 11)


def norm(value: Any) -> Any:
    """Text compared loosely: case, spaces, underscores and hyphens do not matter."""
    return re.sub(r"[\s_-]+", " ", value.strip().lower()) if isinstance(value, str) else value


def close(a: Any, b: Any, tol: float = 0.01) -> bool:
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs)


def var(xs: List[float]) -> float:
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def many(ctx: Ctx, participants: str = "random") -> List[Dict[str, Any]]:
    return [ctx.run(seed, participants).outputs for seed in MANY]


def line_leak(ctx: Ctx, viewers: Iterable[str], subject: str, words: str) -> None:
    """Fail if any viewer read a line naming ``subject`` together with a word matching ``words``."""
    probe, name = ctx.probe(), ctx.names()[subject]
    pattern = rf"(?im)^[^\n]*\b{re.escape(name)}\b[^\n]*\b(?:{words})\b[^\n]*$"
    for viewer in viewers:
        if viewer != subject:
            shown = probe.seen(viewer, pattern) or probe.looked(viewer, subject, rf"(?i)\b(?:{words})\b")
            assert shown is None, f"{viewer} was shown {subject}'s private information: …{shown}…"


# -- jury trial -----------------------------------------------------------------------------------------------------

def jury_verdict_follows_unanimity(ctx: Ctx) -> None:
    for out in many(ctx):
        votes = [norm(v) for v in out["juror_votes"].values()]
        assert len(votes) == 6, f"expected 6 jurors in juror_votes, got {len(votes)}"
        expected = votes[0] if votes[0] in ("guilty", "not guilty") and len(set(votes)) == 1 else "hung"
        assert norm(out["verdict"]) == expected, f"votes {votes} but verdict {out['verdict']!r}"


def jury_exhibits_admitted_or_excluded(ctx: Ctx) -> None:
    for out in many(ctx):
        admitted, excluded = set(map(norm, out["admitted_exhibits"])), set(map(norm, out["excluded_exhibits"]))
        assert not admitted & excluded, f"exhibits both admitted and excluded: {admitted & excluded}"
        assert len(admitted | excluded) <= 6, f"more than the 6 exhibits: {admitted | excluded}"
        assert len(excluded) <= int(out["objections_sustained"]), \
            f"{len(excluded)} excluded exhibits but only {out['objections_sustained']} sustained objections"


def jury_objections_can_block_exhibits(ctx: Ctx) -> None:
    outs = many(ctx)
    assert any(out["excluded_exhibits"] for out in outs), "no exhibit was ever excluded in 10 random trials"
    assert any(out["admitted_exhibits"] for out in outs), "no exhibit was ever admitted in 10 random trials"


# -- beer game ------------------------------------------------------------------------------------------------------

def beer_demand_is_random_and_in_range(ctx: Ctx) -> None:
    demands = [out["customer_demand"] for out in ctx.outs]
    for demand in demands:
        assert len(demand) == 20, f"customer_demand has {len(demand)} weeks, expected 20"
        assert all(float(d) == int(d) and 0 <= d <= 8 for d in demand), f"demand outside 0..8: {demand}"
    assert len({tuple(d) for d in demands}) > 1, "customer demand is identical on every seed"


def beer_demand_is_exogenous(ctx: Ctx) -> None:
    for seed in (1, 2):
        random_play, idle = ctx.run(seed).outputs, ctx.run(seed, "idle").outputs
        assert random_play["customer_demand"] == idle["customer_demand"], \
            f"seed {seed}: customer demand changes with what the players do"


def beer_costs_add_up(ctx: Ctx) -> None:
    for out in ctx.outs:
        costs = out["costs_by_role"]
        assert {norm(k) for k in costs} == {"retailer", "wholesaler", "distributor", "factory"}, f"roles: {list(costs)}"
        assert all(c >= 0 for c in costs.values()), f"negative cost: {costs}"
        assert close(sum(costs.values()), out["total_team_cost"]), \
            f"total_team_cost {out['total_team_cost']} != sum of role costs {sum(costs.values())}"


def beer_idle_team_pays_backlog(ctx: Ctx) -> None:
    out = ctx.run(1, "idle").outputs
    assert out["total_team_cost"] > 0, "a team that never orders still pays nothing"
    assert all(float(o) == 0 for o in out["factory_orders"]), f"idle factory produced: {out['factory_orders']}"


def beer_bullwhip_matches_its_definition(ctx: Ctx) -> None:
    for out in ctx.outs:
        demand, orders = out["customer_demand"], out["factory_orders"]
        assert len(orders) == len(demand), f"{len(orders)} factory orders vs {len(demand)} demand weeks"
        if var(demand) > 0:
            expected = var(orders) / var(demand)
            assert close(out["bullwhip_ratio"], expected, 0.02), \
                f"bullwhip_ratio {out['bullwhip_ratio']} but var(orders)/var(demand) = {expected:.4f}"


# -- treaty ---------------------------------------------------------------------------------------------------------

def treaty_needs_three_ratifications(ctx: Ctx) -> None:
    nations = {a["id"] for a in ctx.agents()}
    for out in many(ctx):
        ratified = out["ratified_by"]
        assert len(set(ratified)) == len(ratified) and set(ratified) <= nations, f"ratified_by {ratified}"
        if out["in_force"]:
            assert len(ratified) >= 3, f"in force with only {ratified}"
            assert out["agreed_cut"] is not None, "in force without an agreed_cut"
        else:
            assert out["agreed_cut"] is None, f"no treaty but agreed_cut {out['agreed_cut']}"


def treaty_respects_red_lines(ctx: Ctx) -> None:
    for out in many(ctx):
        lines = out["red_lines"]
        assert len(lines) == 4 and all(10 <= v <= 60 for v in lines.values()), f"red_lines {lines}"
        for nation in out["ratified_by"] if out["in_force"] else []:
            assert out["agreed_cut"] <= lines[nation], f"{nation} ratified {out['agreed_cut']} above its red line"
    assert len({tuple(sorted(o["red_lines"].values())) for o in ctx.outs}) > 1, "red lines are the same every game"


def treaty_red_lines_stay_secret(ctx: Ctx) -> None:
    probe = ctx.probe()
    lines = probe.result.outputs["red_lines"]
    names = ctx.names()
    for owner, value in lines.items():
        for viewer in lines:
            if viewer != owner and value != lines[viewer]:
                pattern = rf"(?im)^[^\n]*\b{re.escape(names[owner])}\b[^\n]*{number_pattern(value)}[^\n]*$"
                shown = probe.seen(viewer, pattern) or probe.looked(viewer, owner, number_pattern(value))
                assert shown is None, f"{viewer} was shown {owner}'s red line {value}: …{shown}…"


# -- emergency department -------------------------------------------------------------------------------------------

def ed_patients_are_conserved(ctx: Ctx) -> None:
    for out in many(ctx):
        arrived = out["patients_arrived"]
        accounted = (out["patients_treated"] + out["patients_left_without_being_seen"]
                     + out["patients_in_department_at_end"])
        assert arrived == accounted, f"{arrived} arrived but treated+left+in department = {accounted}"
        assert sum(out["arrivals_by_hour"]) == arrived, f"arrivals_by_hour sums to {sum(out['arrivals_by_hour'])}"
        assert len(out["arrivals_by_hour"]) == 12, f"{len(out['arrivals_by_hour'])} hours, expected 12"


def ed_arrivals_are_exogenous(ctx: Ctx) -> None:
    for seed in (1, 2):
        assert ctx.run(seed).outputs["arrivals_by_hour"] == ctx.run(seed, "idle").outputs["arrivals_by_hour"], \
            f"seed {seed}: arrivals change with what the staff do"
    assert len({tuple(o["arrivals_by_hour"]) for o in ctx.outs}) > 1, "arrivals are identical on every seed"
    assert 30 <= mean(o["patients_arrived"] for o in many(ctx)) <= 90, "about 5 patients an hour over 12 hours"


def ed_idle_staff_treat_nobody(ctx: Ctx) -> None:
    out = ctx.run(1, "idle").outputs
    assert out["patients_treated"] == 0, f"{out['patients_treated']} treated while the staff did nothing"
    assert out["mean_wait_hours"] is None, f"mean_wait_hours {out['mean_wait_hours']} though nobody was seen"
    assert out["patients_left_without_being_seen"] > 0, "nobody walked out after a night with no doctors"


# -- sealed tender --------------------------------------------------------------------------------------------------

def tender_lowest_valid_bid_wins(ctx: Ctx) -> None:
    for out in many(ctx):
        valid = {firm: b for firm, b in out["bids"].items() if b is not None and b <= out["ceiling"]}
        assert close(out["ceiling"], 900000), f"ceiling {out['ceiling']}"
        if out["winner"] is None:
            assert out["flagged"] or not valid, f"valid bids {valid} but no winner"
            continue
        assert out["winner"] in valid, f"winner {out['winner']} has no valid bid ({out['bids']})"
        assert close(out["winning_price"], valid[out["winner"]]), "winning_price is not the winner's bid"
        if not out["flagged"]:
            assert close(valid[out["winner"]], min(valid.values())), f"winner is not the lowest valid bid: {valid}"


def tender_bids_stay_sealed(ctx: Ctx) -> None:
    probe = ctx.probe()
    out = probe.result.outputs
    for firm, bid in out["bids"].items():
        if bid is None or firm == out["winner"] or (float(bid).is_integer() and abs(bid) < 1000):
            continue  # the winning price is public; a small whole number is too common to trace
        for rival in out["bids"]:
            if rival != firm:
                shown = probe.seen(rival, number_pattern(bid))
                assert shown is None, f"{rival} was shown {firm}'s sealed bid {bid}: …{shown}…"


def tender_collusion_channel_is_private(ctx: Ctx) -> None:
    probe = ctx.probe()
    colluders = set(probe.result.outputs["colluders"])
    assert len(colluders) == 2, f"colluders {colluders}"
    tokens = [str(v) for c in colluders for call in probe.calls[c] for v in call["args"].values()
              if isinstance(v, str) and v.startswith("zq")]
    outsiders = [a["id"] for a in ctx.agents() if a["id"] not in colluders]
    for viewer in outsiders:
        for token in tokens:
            shown = probe.seen(viewer, re.escape(token))
            assert shown is None, f"{viewer} read a colluders' message: …{shown}…"


# -- corner shop ----------------------------------------------------------------------------------------------------

PRODUCTS = {"beans": (6, 10), "mugs": (4, 9), "filters": (1, 3)}


def shop_stock_is_conserved(ctx: Ctx) -> None:
    for out in many(ctx):
        for p in PRODUCTS:
            supply = 10 + out["units_ordered"][p]
            accounted = out["units_sold"][p] + out["units_on_hand_end"][p] + out["units_on_order_end"][p]
            assert supply == accounted, f"{p}: 10 + ordered = {supply} but sold+on hand+on order = {accounted}"
            demand = sum(out["demand_by_week"][p])
            assert demand == out["units_sold"][p] + out["lost_sales"][p], f"{p}: demand {demand} != sold + lost"


def shop_cash_is_conserved(ctx: Ctx) -> None:
    for out in many(ctx):
        expected = 500 + sum(price * out["units_sold"][p] - cost * out["units_ordered"][p]
                             for p, (cost, price) in PRODUCTS.items())
        assert close(out["cash_end"], expected), f"cash_end {out['cash_end']} but 500 + sales - purchases = {expected}"
        assert out["cash_end"] >= 0, f"cash went negative: {out['cash_end']}"


def shop_demand_is_exogenous(ctx: Ctx) -> None:
    for seed in (1, 2):
        random_play, idle = ctx.run(seed).outputs, ctx.run(seed, "idle").outputs
        assert random_play["demand_by_week"] == idle["demand_by_week"], f"seed {seed}: demand follows the orders"
        assert all(len(w) == 12 for w in idle["demand_by_week"].values()), "demand_by_week is not 12 weeks long"
        assert all(v == 0 for v in idle["units_ordered"].values()), "an owner who does nothing still ordered"


# -- auction house --------------------------------------------------------------------------------------------------

def auction_lots_are_well_formed(ctx: Ctx) -> None:
    for out in many(ctx):
        lots = out["lots"]
        assert sorted(int(lot["lot"]) for lot in lots) == [1, 2, 3, 4, 5, 6], f"lots {[lot['lot'] for lot in lots]}"
        formats = {int(lot["lot"]): norm(lot["format"]) for lot in lots}
        assert all(formats[i] == ("english" if i <= 3 else "sealed") for i in formats), f"formats {formats}"
        for lot in lots:
            assert (lot["winner"] is None) == (lot["price"] is None), f"lot {lot['lot']}: winner/price mismatch"


def auction_money_is_conserved(ctx: Ctx) -> None:
    for out in many(ctx):
        paid = sum(lot["price"] for lot in out["lots"] if lot["price"] is not None)
        assert close(out["house_revenue"], paid), f"house_revenue {out['house_revenue']} but lots paid {paid}"
        assert close(out["cash_start_total"], 1800), f"cash_start_total {out['cash_start_total']}, expected 6 × 300"
        assert close(out["cash_start_total"] - out["cash_end_total"], out["house_revenue"]), \
            "collectors' cash fell by a different amount than the house received"


def auction_english_lots_sell(ctx: Ctx) -> None:
    sold = [lot for out in many(ctx) for lot in out["lots"] if int(lot["lot"]) <= 3 and lot["winner"] is not None]
    assert sold, "no open-auction lot sold in 10 random games"
    assert all(lot["price"] >= 5 for lot in sold), "an open-auction lot sold below the $5 minimum raise"


# -- town hall ------------------------------------------------------------------------------------------------------

PROPOSALS = ("park", "garage", "library")


def town_tallies_match_ballots(ctx: Ctx) -> None:
    for out in many(ctx):
        assert len(out["ballots"]) == 9, f"{len(out['ballots'])} residents in ballots, expected 9"
        for p in PROPOSALS:
            counted = Counter(norm(b.get(p)) for b in out["ballots"].values())
            tally = out["tallies"][p]
            for choice in ("yes", "no", "abstain"):
                assert tally[choice] == counted[choice], f"{p}: tally {tally} vs ballots {dict(counted)}"


def town_results_follow_rules(ctx: Ctx) -> None:
    for out in many(ctx):
        for p in PROPOSALS:
            t = out["tallies"][p]
            expected = "no quorum" if t["yes"] + t["no"] < 5 else "passed" if t["yes"] > t["no"] else "failed"
            assert norm(out["results"][p]) == expected, f"{p}: tally {t} gives {expected!r}, got {out['results'][p]!r}"


def town_ballots_are_secret(ctx: Ctx) -> None:
    residents = [a["id"] for a in ctx.agents()]
    for resident in residents:
        line_leak(ctx, residents, resident, r"(?:park|garage|library)\W{1,5}(?:yes|no|abstain)")


# -- werewolf -------------------------------------------------------------------------------------------------------

def wolf_roles_are_dealt(ctx: Ctx) -> None:
    for out in ctx.outs:
        roles = Counter(norm(r) for r in out["roles"].values())
        assert roles == Counter({"villager": 4, "werewolf": 2, "seer": 1}), f"roles dealt: {dict(roles)}"
    assert len({tuple(sorted(o["roles"].items())) for o in ctx.outs}) > 1, "roles are the same every game"


def wolf_outcome_is_consistent(ctx: Ctx) -> None:
    for out in many(ctx):
        players, gone, alive = set(out["roles"]), out["eliminated"], set(out["alive_end"])
        assert len(set(gone)) == len(gone), f"someone was eliminated twice: {gone}"
        assert set(gone) | alive == players and not set(gone) & alive, f"eliminated {gone} vs alive {alive}"
        wolves = sum(norm(out["roles"][p]) == "werewolf" for p in alive)
        if norm(out["winner"]) == "village":
            assert wolves == 0, "village won with werewolves alive"
        elif norm(out["winner"]) == "werewolves":
            assert wolves >= len(alive) - wolves, "werewolves won while outnumbered"
        else:
            assert 0 < wolves < len(alive) - wolves, f"no winner, yet {wolves} wolves among {len(alive)} alive"


def wolf_roles_stay_hidden(ctx: Ctx) -> None:
    roles = ctx.probe().result.outputs["roles"]
    villagers = [p for p, r in roles.items() if norm(r) == "villager"]
    for wolf in [p for p, r in roles.items() if norm(r) == "werewolf"]:
        line_leak(ctx, villagers, wolf, "werewolf|werewolves|wolf")


# -- labor negotiation ----------------------------------------------------------------------------------------------

def labor_strike_costs_add_up(ctx: Ctx) -> None:
    for result in [ctx.run(seed) for seed in MANY]:
        out = result.outputs
        rounds = out["strike_rounds"]
        assert 0 <= rounds <= max(0, result.rounds - 3), f"{rounds} strike rounds in a {result.rounds}-round talk"
        assert close(out["company_losses"], 200000 * rounds), f"company_losses {out['company_losses']} for {rounds}"
        assert close(out["worker_lost_wages"], 50000 * rounds), f"worker_lost_wages {out['worker_lost_wages']}"


def labor_deal_needs_majority(ctx: Ctx) -> None:
    for out in many(ctx):
        votes = [norm(v) for v in out["ratification_votes"].values()]
        assert len(votes) == 5, f"{len(votes)} members in ratification_votes, expected 5"
        if out["agreed"]:
            assert out["wage_increase"] is not None, "agreed without a wage_increase"
            assert votes.count("yes") > votes.count("no"), f"ratified with votes {votes}"
        else:
            assert out["wage_increase"] is None, f"no deal but wage_increase {out['wage_increase']}"


def labor_strike_happens_sometimes(ctx: Ctx) -> None:
    assert any(out["strike_rounds"] > 0 for out in many(ctx)), "no strike in 10 random negotiations"


# -- classroom ------------------------------------------------------------------------------------------------------

def class_scores_are_valid(ctx: Ctx) -> None:
    for out in many(ctx):
        scores = out["quiz_scores"]
        assert len(scores) == 5, f"{len(scores)} students, expected 5"
        for student, weekly in scores.items():
            assert len(weekly) == 6, f"{student} has {len(weekly)} quiz scores, expected 6"
            assert all(0 <= s <= 10 for s in weekly), f"{student}: scores outside 0..10: {weekly}"


def class_grades_are_averages(ctx: Ctx) -> None:
    for out in many(ctx):
        for student, weekly in out["quiz_scores"].items():
            assert close(out["final_grades"][student], mean(weekly)), f"{student}: grade is not the quiz average"
        assert close(out["class_average"], mean(out["final_grades"].values())), "class_average is not the mean grade"


def class_scores_are_private(ctx: Ctx) -> None:
    students = list(ctx.probe().result.outputs["quiz_scores"])
    for student in students:
        line_leak(ctx, students, student, "score|scored|quiz|grade")


# -- sports league --------------------------------------------------------------------------------------------------

def league_schedule_is_double_round_robin(ctx: Ctx) -> None:
    for out in ctx.outs:
        matches = out["matches"]
        pairs = Counter((m["home"], m["away"]) for m in matches)
        teams = {m["home"] for m in matches} | {m["away"] for m in matches}
        assert len(teams) == 6 and len(matches) == 30, f"{len(teams)} teams, {len(matches)} matches"
        assert all(n == 1 for n in pairs.values()) and len(pairs) == 30, "a home/away pairing is missing or repeated"
        for day in {m["matchday"] for m in matches}:
            playing = [t for m in matches if m["matchday"] == day for t in (m["home"], m["away"])]
            assert len(playing) == len(set(playing)) == 6, f"matchday {day}: {playing}"


def league_table_is_computed_correctly(ctx: Ctx) -> None:
    for out in many(ctx):
        table: Dict[Any, Dict[str, int]] = {}
        for m in out["matches"]:
            for team, gf, ga in ((m["home"], m["home_goals"], m["away_goals"]),
                                 (m["away"], m["away_goals"], m["home_goals"])):
                row = table.setdefault(team, Counter())
                row["played"] += 1
                row["goals_for"] += gf
                row["goals_against"] += ga
                row["won" if gf > ga else "drawn" if gf == ga else "lost"] += 1
                row["points"] += 3 if gf > ga else 1 if gf == ga else 0
        for row in out["standings"]:
            for key, value in table[row["team"]].items():
                assert row[key] == value, f"{row['team']}: {key} {row[key]} but the matches give {value}"


def league_table_is_ranked(ctx: Ctx) -> None:
    for out in many(ctx):
        rows = out["standings"]
        names = ctx.names()
        keys = [(-r["points"], -(r["goals_for"] - r["goals_against"]), -r["goals_for"],
                 names.get(r["team"], r["team"])) for r in rows]
        assert keys == sorted(keys), f"table not in rank order: {[r['team'] for r in rows]}"
        assert out["champion"] == rows[0]["team"], f"champion {out['champion']} is not top of the table"


BRIEFS = {
    "jury_trial": (["verdict", "juror_votes", "admitted_exhibits", "excluded_exhibits", "objections_sustained"],
                   [jury_verdict_follows_unanimity, jury_exhibits_admitted_or_excluded,
                    jury_objections_can_block_exhibits]),
    "beer_game": (["customer_demand", "factory_orders", "costs_by_role", "total_team_cost", "bullwhip_ratio"],
                  [beer_demand_is_random_and_in_range, beer_demand_is_exogenous, beer_costs_add_up,
                   beer_idle_team_pays_backlog, beer_bullwhip_matches_its_definition]),
    "treaty": (["in_force", "ratified_by", "agreed_cut", "red_lines"],
               [treaty_needs_three_ratifications, treaty_respects_red_lines, treaty_red_lines_stay_secret]),
    "ed_triage": (["arrivals_by_hour", "patients_arrived", "patients_treated", "patients_left_without_being_seen",
                   "patients_in_department_at_end", "mean_wait_hours"],
                  [ed_patients_are_conserved, ed_arrivals_are_exogenous, ed_idle_staff_treat_nobody]),
    "sealed_tender": (["winner", "winning_price", "bids", "flagged", "colluders", "ceiling"],
                      [tender_lowest_valid_bid_wins, tender_bids_stay_sealed, tender_collusion_channel_is_private]),
    "weekly_inventory": (["cash_end", "units_ordered", "units_sold", "units_on_hand_end", "units_on_order_end",
                          "lost_sales", "demand_by_week"],
                         [shop_stock_is_conserved, shop_cash_is_conserved, shop_demand_is_exogenous]),
    "auction_house": (["lots", "house_revenue", "cash_start_total", "cash_end_total"],
                      [auction_lots_are_well_formed, auction_money_is_conserved, auction_english_lots_sell]),
    "town_hall": (["results", "tallies", "ballots"],
                  [town_tallies_match_ballots, town_results_follow_rules, town_ballots_are_secret]),
    "werewolf": (["winner", "roles", "eliminated", "alive_end"],
                 [wolf_roles_are_dealt, wolf_outcome_is_consistent, wolf_roles_stay_hidden]),
    "labor_negotiation": (["agreed", "wage_increase", "strike_rounds", "company_losses", "worker_lost_wages",
                           "ratification_votes"],
                          [labor_strike_costs_add_up, labor_deal_needs_majority, labor_strike_happens_sometimes]),
    "classroom": (["quiz_scores", "final_grades", "class_average"],
                  [class_scores_are_valid, class_grades_are_averages, class_scores_are_private]),
    "sports_league": (["matches", "standings", "champion"],
                      [league_schedule_is_double_round_robin, league_table_is_computed_correctly,
                       league_table_is_ranked]),
}
