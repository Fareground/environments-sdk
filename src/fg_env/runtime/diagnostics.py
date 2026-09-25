"""Run diagnostics: likely logic problems a run revealed, in plain words, each with a fix.

A contract can pass every check and still not do what its author meant: a rule that fails for some choice an agent can
make, a tool offered when none of its choices can work, sealed choices that overwrite each other, an agent type that
never has anything to do, a stage that can never run, a measure that stays empty because nothing ever sets what it
reads, a coded policy rule whose call is refused every time it is tried, a host's answers that were the contract's
fallback stand-ins because no host was bound. What the run counted is read from the folds of its facts — its
statistics and its diagnosis (:mod:`fg_env.runtime.facts`, :mod:`fg_env.runtime.diagnosis`) — and the findings are
reported on ``RunResult.diagnostics``, in ``result.summary()`` and as warnings from ``fg_env.check``. Each is
reported only on evidence that random play cannot explain away, so a clean contract raises none. Turns an LLM
participant forfeited to a failing model provider, agents that never acted or too many of whose turns failed (for a
model participant, a small share), and a run its budget cut short are reported too: such a run does not show how its
agents play (any failed turns of a model participant, or turns out of time, are reported with their rate).
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..contract.base import TAPE

if TYPE_CHECKING:
    from .env import Env

__all__ = ["diagnose", "DEGRADING", "MIN_CALLS", "REFUSED_SHARE", "MIN_ROUNDS", "ALWAYS_FAULTED", "FAILED_SHARE",
           "MODEL_FAILED_SHARE"]

#: In a run with model participants (which report their usage), an action called this often and mostly refused is
#: reported; random agents choose blindly, so their refusals say nothing about the tools. An action a model or a coded
#: policy chose this often, and never got through, never ran at all.
MIN_CALLS = 4
REFUSED_SHARE = 0.5
#: Rounds of evidence needed before a metric that never changes, or an agent type that never can act, is reported.
MIN_ROUNDS = 2
#: An action that failed this often as it applied, and never once took effect, is broken for every choice, not just
#: some.
ALWAYS_FAULTED = 2
#: Findings that mean the run does not show what the environment is for: an action that can never happen, or that
#: never did (every call refused, so nothing it feeds ran), agents that never acted or too many of whose turns failed,
#: agents that never had an action to take, a run in which no agent ever had a turn with one, turns lost to a failing provider, an output that raised an error, a run
#: its budget cut short, host answers that were the contract's stand-ins or that it could not use.
#: ``RunResult.degraded`` lists them, and such a run is not ``ok``.
DEGRADING = frozenset({"action_always_faulted", "action_never_succeeded", "agents_never_acted", "agents_often_failed",
                       "agents_never_able_to_act", "nobody_played", "turns_forfeited", "output_failed", "budget_cut",
                       "host_fallback", "host_unusable"})
#: An agent more than this share of whose turns failed (``Stats.failed_turns``) does not show how it plays.
FAILED_SHARE = 0.5
#: The same for a model participant, held to a much lower share: every failed turn of a model is a move it never made
#: (a coded or random agent's misses may be blind choices, so it is held to :data:`FAILED_SHARE`).
MODEL_FAILED_SHARE = 0.1
#: Agents named in one finding; the rest are counted.
_LISTED = 5

_RECORD_READ = re.compile(r"\$records\(\s*([A-Za-z_]\w*)")
_WORLD_READ = re.compile(r"\$world\.([A-Za-z_]\w*)")
_PROP_READ = re.compile(r"(?:\$it|\$actor|\))\.([A-Za-z_]\w*)")
_ASSIGNED = re.compile(r"[.$]([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:[-+*/]=|(?<![<>!=])=(?!=))")
#: Roots whose value moves on its own: a condition reading one can hold later even if nothing else changes.
_MOVING = re.compile(
    r"\$(round|clock|time|stage|outputs|series|chance|random|randint|choice|shuffle|pending|pattern)\b")
_BUILT_IN_FIELDS = {"id", "name", "type", "alive", "at"}
#: Sections whose effects and settings can write properties, post to records or name a winner.
_RULE_SECTIONS = ("actions", "stages", "events", "defs", "end")


def diagnose(env: Env, outputs: dict[str, Any], issues: Sequence[dict[str, Any]] = ()) -> list[dict[str, str]]:
    """Every likely logic problem the run so far shows, as ``{code, path, message, fix}``; ``issues`` are the outputs
    that could not be worked out (``RunResult.output_issues``)."""
    rules = _Rules(env)
    failed = [issue for issue in issues if issue["path"].startswith("outputs.")]
    return [*(_finding("output_failed", issue["path"], issue["message"],
                       issue.get("fix") or "fix the expression, or guard the case it fails in") for issue in failed),
            *_budget_cut(env), *_unreported_usage(env), *_forfeits(env), *_nobody_played(env), *_never_acted(env),
            *_out_of_steps(env),
            *_arm_inputs(env),
            *_host_fallbacks(env), *_host_unusable(env), *_faults(env), *_faulted_types(env), *_actions(env),
            *_policy_rules(env),
            *_overwrites(env), *_idle_agents(env), *_stages(env, rules),
            *_stuck_measures(env, outputs, rules, {issue["path"] for issue in failed})]


def _budget_cut(env: Env) -> list[dict[str, str]]:
    budget = env.budget
    if budget is None or budget.exhausted is None:
        return []
    return [_finding("budget_cut", "budget",
                     f"{budget.message(env).rstrip('.')}, so the outputs are those of a run cut short, not of the "
                     "environment played to its end",
                     "give the run a larger budget, or fewer `rounds`, when the outputs are to count")]


def _unreported_usage(env: Env) -> list[dict[str, str]]:
    calls = env.state.stats.unreported_usage
    if not calls:
        return []
    return [_finding("usage_unreported", "participants",
                     f"{calls} model call(s) of the participants or hosts came back without token usage (or not as "
                     "whole numbers), so their cost is an estimate (the prompt's size) in the run's token counts and "
                     "budget",
                     "use a provider or proxy that reports usage when the token counts or a token budget must be "
                     "exact")]


def _forfeits(env: Env) -> list[dict[str, str]]:
    stats = sorted(env.state.agent_stats.items())
    failed = {agent: s.forfeits - s.too_long for agent, s in stats if s.forfeits > s.too_long}
    long = {agent: s.too_long for agent, s in stats if s.too_long}
    out = []
    if failed:
        out.append(_finding("turns_forfeited", "participants",
                            f"{sum(failed.values())} turn(s) were forfeited because the model provider still failed "
                            f"after every retry ({', '.join(f'{agent} {count}' for agent, count in failed.items())}); "
                            "those agents did nothing in them, so this run does not show how they play",
                            "rerun when the provider is healthy, or give the participant more `retries`"))
    if long:
        out.append(_finding("turns_forfeited", "participants",
                            f"{sum(long.values())} turn(s) were forfeited because the prompt is longer than the "
                            f"model's context ({', '.join(f'{agent} {count}' for agent, count in long.items())}), "
                            "which no retry can change; those agents did nothing in them, so this run does not show "
                            "how they play",
                            "shorten what an agent reads each turn (its brief, views and news: fg-env preview shows "
                            "it), or use a model with a larger context"))
    return out


def _nobody_played(env: Env) -> list[dict[str, str]]:
    """A finished run of a contract with agents and actions in which no agent ever had a turn: whatever it measured,
    no agent's choice shaped it. (Turns that never offered an action: ``agents_never_able_to_act``.)"""
    contract = env.contract
    if not env.finished or not contract.actions or not contract.agent_types() or env.state.stats.actions:
        return []
    if any(stats.wakes for stats in env.state.agent_stats.values()):
        return []
    return [_finding("nobody_played", "stages",
                     f"no agent had a single turn in {env.world.round} round(s): every stage was skipped (its `when` "
                     "or `who`), the run ended before any stage (an `end` that holds at the start), or no entity is an "
                     "agent; this run does not show how agents play",
                     "check each stage's `when` and `who`, and each `end` condition, against the state at the start; "
                     "fg-env preview shows what the first agent to play is offered")]


def _out_of_steps(env: Env) -> list[dict[str, str]]:
    cut = {agent: stats.out_of_steps for agent, stats in sorted(env.state.agent_stats.items()) if stats.out_of_steps}
    if not cut:
        return []
    return [_finding("out_of_steps", "participants",
                     f"{sum(cut.values())} turn(s) ended because the model used all the participant's `max_steps` "
                     f"calls ({', '.join(f'{agent} {count}' for agent, count in list(cut.items())[:_LISTED])}"
                     f"{' …' if len(cut) > _LISTED else ''}); the model kept calling tools without finishing its turn",
                     "read those turns (load with exposures=True, then result.exposures): tools that keep refusing or "
                     "reads that never settle the choice need clearer tools and brief; else give the participant "
                     "more `max_steps`")]


def _never_acted(env: Env) -> list[dict[str, str]]:
    """Agents whose attempts all went wrong — tools that were invalid or refused, model replies refused, cut off, with
    no tool call or out of steps — so none ever became an action, and model-driven agents (or ones whose turns ran out
    of time) too many or some of whose turns failed that way. A model that passes (`end_turn`) where passing is allowed
    made a move: it is not reported. A coded agent's misses are its author's code and may be blind (random play): it
    is reported only when no agent acted at all. (Refusals from a rule that failed are the contract's:
    `action_always_faulted` reports those; an agent type that never had an action, `agents_never_able_to_act`.)"""
    if not env.finished:
        return []
    never_able = {kind for kind, entry in sorted(env.state.diagnosis.agents.items()) if not entry["able"]}
    nobody_acted = not env.state.stats.actions
    never, failing, some = [], [], []
    for agent, stats in sorted(env.state.agent_stats.items()):
        entity = env.world.entities.get(agent)
        if not stats.wakes or (entity is not None and entity.entity_type in never_able):
            continue
        went_wrong = stats.went_wrong(faults=False)  # a refusal a failing rule caused is the contract's
        watched = stats.llm_calls or stats.timeouts  # a model's misses, and turns out of time, are never blind choices
        share = MODEL_FAILED_SHARE if stats.llm_calls else FAILED_SHARE
        if not stats.actions and went_wrong and (stats.llm_calls or nobody_acted):
            never.append((agent, stats))
        elif watched and stats.failed_turns > share * stats.wakes:
            failing.append((agent, stats))
        elif watched and stats.failed_turns:
            some.append((agent, stats))
    out = []
    if never:
        out.append(_finding("agents_never_acted", "participants",
                            f"{_named(never)} took no action in any of {sum(s.wakes for _, s in never)} turn(s): "
                            f"{_attempts(never)}; this run does not show how they play",
                            "read what the agents were shown and did (load with exposures=True, then "
                            "result.exposures): a model that only replies in text, calls tools that do not exist or is "
                            "always refused needs clearer tools and brief" + _out_of_time(never)))
    if failing:
        listed = ", ".join(f"{agent} {s.failed_turns} of {s.wakes}" for agent, s in failing[:_LISTED])
        out.append(_finding("agents_often_failed", "participants",
                            f"too many turns of {_named(failing)} failed: they ended with no action though one was "
                            "available, after invalid or refused calls, a model refusal, a reply cut off or with no "
                            f"tool call, or the model calls used up, or out of time, or a model reply was refused or "
                            f"cut off ({listed}"
                            f"); {_attempts(failing)}; this run does not show how they play",
                            "read what those agents were shown and did (load with exposures=True, then "
                            "result.exposures); for replies cut off, give the participant more `max_tokens`; for model "
                            "calls used up, more `max_steps` or clearer tools; for replies with no tool call, a brief "
                            "and tools that make the choice clear" + _out_of_time(failing)))
    if some:
        failed, wakes = sum(s.failed_turns for _, s in some), sum(s.wakes for _, s in some)
        out.append(_finding("some_turns_failed", "participants",
                            f"{failed} of {wakes} turns ({failed / wakes:.0%}) of {_named(some)} failed "
                            f"({', '.join(f'{agent} {s.failed_turns} of {s.wakes}' for agent, s in some[:_LISTED])}): "
                            f"{_attempts(some)}",
                            "read those turns (load with exposures=True, then result.exposures)" + _out_of_time(some)))
    return out


def _out_of_time(agents: list[tuple[str, Any]]) -> str:
    """The fix for turns out of time, when some of ``agents``' were."""
    if not any(stats.timeouts for _, stats in agents):
        return ""
    return "; turns out of time need a longer `time_limit` or a faster participant"


def _named(agents: list[tuple[str, Any]]) -> str:
    names = [agent for agent, _ in agents[:_LISTED]]
    more = len(agents) - len(names)
    return ", ".join(names) + (f" and {more} more" if more else "")


def _attempts(agents: list[tuple[str, Any]]) -> str:
    def total(name: str) -> int:
        return sum(getattr(stats, name) for _, stats in agents)

    tried = [f"{total('llm_calls')} model call(s)", f"{total('invalid_calls')} invalid tool call(s) (unknown tools or "
             "bad arguments)", f"{total('rejected_actions') - total('faulted_actions')} refused by the rules"]
    tried += [f"{total(name)} {label}" for name, label in (("refusals", "model refusal(s)"),
                                                            ("truncated", "reply(ies) cut off at the output limit"),
                                                            ("out_of_steps", "turn(s) out of model calls"),
                                                            ("no_tool_replies",
                                                             "turn(s) the model answered in text only"),
                                                            ("timeouts", "turn(s) out of time"))
              if total(name)]
    return ", ".join(tried)


def _arm_inputs(env: Env) -> list[dict[str, str]]:
    from ..experiments.arm_inputs import arm_input_overrides, override_message

    return [_finding("arm_input_overridden", f"arms.{env.arm}.inputs.{name}",
                     override_message(str(env.arm), name, arm_value, given),
                     "leave that input out when running the arm (the caller's inputs win over an arm's), or change the "
                     "arm")
            for name, arm_value, given in arm_input_overrides(env.contract, env.arm, env.inputs)]


def _host_fallbacks(env: Env) -> list[dict[str, str]]:
    tape = env.world.props.get(TAPE)
    counts: dict[tuple[str, str], int] = {}
    for entry in tape.values() if isinstance(tape, dict) else ():
        if isinstance(entry, dict) and entry.get("fallback"):
            key = (str(entry.get("site")), str(entry.get("service")))
            counts[key] = counts.get(key, 0) + 1
    return [_finding("host_fallback", site,
                     f"{count} answer(s) meant for the host '{service}' were the contract's fallback because no host "
                     "was bound: whatever depends on them is a stand-in, not the host's answer",
                     f"bind the host for real answers (fg_env.host.load(..., hosts={{'{service}': ...}})), or replay "
                     "a recorded tape")
            for (site, service), count in counts.items()]


def _host_unusable(env: Env) -> list[dict[str, str]]:
    """Requests a host gave no usable answer to, also when asked again, by site. An answer outside the protocol, or
    unusable answers to more than a small share of a site's requests (the share a model participant's turns may fail),
    degrade the run (`host_unusable`): its judged texts or attempts were decided by nobody. A few declines or failures
    are reported (`host_sometimes_unusable`): a host declining content participants wrote is part of the game."""
    tape = env.world.props.get(TAPE)
    asked: dict[str, int] = {}
    found: dict[str, list[Any]] = {}  # site → [unusable, any outside the protocol, any unavailable, the latest reason]
    for entry in tape.values() if isinstance(tape, dict) else ():
        if not isinstance(entry, dict):
            continue
        site = str(entry.get("site"))
        asked[site] = asked.get(site, 0) + 1
        if entry.get("unusable"):
            counts = found.setdefault(site, [0, False, False, ""])
            counts[0] += 1
            counts[1] = counts[1] or bool(entry.get("outside"))
            counts[2] = counts[2] or bool(entry.get("unavailable"))
            counts[3] = str(entry["unusable"])
    out = []
    for site, (count, outside, unavailable, reason) in found.items():
        degrading = outside or count > MODEL_FAILED_SHARE * asked[site]
        fixes = (["if its provider was down or limiting its rate, rerun when it is healthy or give the host adapter "
                  "more `retries`"] if unavailable else [])
        fixes += ["if it answers outside the protocol, give it a model that follows it, or clearer instructions; if "
                  "it declines content participants wrote, that is part of the game"]
        out.append(_finding("host_unusable" if degrading else "host_sometimes_unusable", site,
                            f"{count} of {asked[site]} request(s) got no usable answer, so each was refused (a judged "
                            f"text left unscored, a game master's attempt refused); the latest: {reason}",
                            "; ".join(fixes)))
    return out


def _finding(code: str, path: str, message: str, fix: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message, "fix": fix}


def _most_common(reasons: dict[str, list[Any]]) -> str:
    count, text = min(reasons.values(), key=lambda entry: (-entry[0], entry[1]))  # ties: the same one every run
    return f"{text.rstrip('.')} ({count}×)"


def _faults(env: Env) -> list[dict[str, str]]:
    out = []
    for path, (count, error) in sorted(env.state.diagnosis.faults.items()):
        if path.startswith("invariants["):
            out.append(_finding("action_broke_invariant", path,
                                f"agents' actions broke it {count} time(s); each was refused and undone: {error}",
                                "refuse such actions before they apply: a `when` requirement (with a `why`) or "
                                "parameter bounds on the action tell agents the rule up front; the invariant stays as "
                                "the backstop"))
        else:
            out.append(_finding("action_rule_failed", path,
                                f"failed {count} time(s) while an agent's action applied, so each such action was "
                                f"refused and undone: {error}",
                                "make the rule work for every choice agents can make: bound the parameter it reads "
                                "(min, max, where) or add a `when` requirement with a `why`, so a choice it cannot "
                                "handle is refused with a reason"))
    return out


def _faulted_types(env: Env) -> list[dict[str, str]]:
    """Agent types most of whose attempts were refused because a rule failed as they applied — a coded population whose
    actions fail (another type's use of the same action may still work, so the action alone does not show it). An
    action that never once applied is reported as such instead (`_actions`), which names the same cause."""
    if any(entry["faulted"] >= ALWAYS_FAULTED and not entry["applied"]
           for entry in env.state.diagnosis.actions.values()):
        return []
    kinds: dict[str, list[Any]] = {}
    for agent, stats in env.state.agent_stats.items():
        entity = env.world.entities.get(agent)
        if entity is not None and stats.wakes:
            kinds.setdefault(entity.entity_type, []).append(stats)
    out = []
    for kind, members in sorted(kinds.items()):
        faulted = sum(stats.faulted_actions for stats in members)
        tried = faulted + sum(stats.actions for stats in members)
        if faulted >= ALWAYS_FAULTED and faulted > FAILED_SHARE * tried:
            out.append(_finding("action_always_faulted", f"types.{kind}",
                                f"{faulted} of the {tried} attempts by {kind} agents were refused because a rule "
                                "failed or an invariant broke as it applied, so this run does not show how they play",
                                "fix the rule that failed (reported with its path and error beside this finding)"))
    return out


def _actions(env: Env) -> list[dict[str, str]]:
    out = []
    for name, entry in sorted(env.state.diagnosis.actions.items()):
        if entry["faulted"] >= ALWAYS_FAULTED and not entry["applied"]:
            out.append(_finding("action_always_faulted", f"actions.{name}",
                                f"never happened: all {entry['faulted']} attempt(s) were refused because a rule failed "
                                "or an invariant broke as it applied, so the rule is broken for every choice agents "
                                "made, not just some",
                                "fix the rule that failed or the invariant it broke (reported with its path and "
                                "error beside this finding); until then no agent can take this action"))
        if entry["chosen"] >= MIN_CALLS and entry["chosen_refused"] == entry["chosen"] and not entry["applied"] \
                and not entry["faulted"] and not entry["unusable"]:
            out.append(_finding("action_never_succeeded", f"actions.{name}",
                                f"never happened: all {entry['chosen']} call(s) a model or coded policy made were "
                                "refused, so what it does (and any mechanism it feeds) never ran in this run; most "
                                f"often: {_most_common(entry['reasons'])}",
                                "make the tool offer only choices that can work: bound or list its parameters (min, "
                                "max, values, where), put a requirement that depends on the state in `when` with a "
                                "`why`, and give a coded policy's `with` arguments the tool accepts"))
        if entry["unusable"]:
            out.append(_finding("action_offered_but_unusable", f"actions.{name}",
                                f"was offered {entry['unusable']} time(s) when none of its choices could succeed; "
                                f"refused with: {_most_common(entry['stuck'])}",
                                "hide it while it cannot work: put the requirement in `when` over $actor, or bound its "
                                "parameters (min, max, where) so the tool only offers choices that can succeed"))
        elif (env.state.stats.llm_calls and entry["calls"] >= MIN_CALLS
              and entry["refused"] >= REFUSED_SHARE * entry["calls"] and entry["reasons"]):
            out.append(_finding("action_mostly_refused", f"actions.{name}",
                                f"refused {entry['refused']} of {entry['calls']} calls; most often: "
                                f"{_most_common(entry['reasons'])}",
                                "make the tool say what is allowed: tighten its parameters (min, max, values, where) "
                                "and describe the rule in its description"))
    return out


def _policy_at(env: Env, path: str) -> Any:
    """The policy a rule path (``types.<type>.policies.<name>.rules[i]``) is in."""
    _, owner, _, name = path.split(".")[:4]
    return env.contract.types[owner].policies[name]


def _policy_rules(env: Env) -> list[dict[str, str]]:
    out = []
    for path, (acted, refused, refusal) in sorted(env.state.diagnosis.policy_rules.items()):
        if not acted:
            out.append(_finding("policy_rule_never_acted", path,
                                f"was tried {refused} time(s) and refused every time: {refusal}",
                                "fix its `with` so the arguments are valid, or its `when` so it is tried only when "
                                "they are"))
        elif refused and _policy_at(env, path).repeat:
            out.append(_finding("policy_repeat_refused", path,
                                f"acted {acted} time(s) and was refused {refused} time(s), most recently: {refusal}; "
                                "the `repeat` policy then moved to its next rule, and its turn ended when no rule "
                                "acted",
                                "give the rule a `when` that holds only while its call can succeed, so the policy "
                                "stops on purpose"))
    return out


def _overwrites(env: Env) -> list[dict[str, str]]:
    return [_finding("sealed_choices_overwrite", f"stages.{stage}",
                     f"sealed choices overwrote each other {count} time(s): {example}",
                     "give each agent its own value (a prop on $actor, or a map keyed by $actor.id) and combine them "
                     f"in an event on `stage.{stage}.end`, or make the stage sequential")
            for stage, (count, example) in sorted(env.state.diagnosis.overwrites.items())] + [
        _finding("loop_overwrites", path, f"an `each` loop overwrote one value {count} time(s): {example}",
                 "collect the values instead (a list with +=, or a map keyed by $it.id) and choose one after the loop "
                 "($mode, $best)")
        for path, (count, example) in sorted(env.state.diagnosis.loop_overwrites.items())]


def _idle_agents(env: Env) -> list[dict[str, str]]:
    """Agent types that never had an action they could take: over :data:`MIN_ROUNDS` rounds, or in a whole finished
    run in which no agent ever did (nothing any agent chose shaped it)."""
    out = []
    read = env.state.diagnosis.agents
    nobody = env.finished and not env.state.stats.actions and not any(entry["able"] for entry in read.values())
    for kind, entry in sorted(read.items()):
        if entry["wakes"] and not entry["able"] and (entry["rounds"] >= MIN_ROUNDS or nobody):
            out.append(_finding("agents_never_able_to_act", f"types.{kind}",
                                f"no {kind} had an action it could take in any of its {entry['wakes']} turn(s) over "
                                f"{entry['rounds']} rounds; most often: {_most_common(entry['reasons'])}",
                                "check the requirements (`when`) and parameter bounds of its actions, and the stage's "
                                "`who`, against the state when it is woken"))
    return out


def _stages(env: Env, rules: _Rules) -> list[dict[str, str]]:
    out = []
    for stage in env.contract.stage_list():
        reached, ran, woke, capped = env.state.diagnosis.stages.get(stage.name, [0, 0, 0, 0])
        if capped and stage.until:
            times = f"all {ran} time(s)" if capped == ran else f"{capped} of the {ran} time(s)"
            out.append(_finding("stage_until_capped", f"stages.{stage.name}.until",
                                f"did not hold in {times} the stage ran: it played every pass it allows and stopped "
                                f"there with `{stage.until}` still false, so what `until` waits for (an agreement, a "
                                "settled state) had not happened",
                                "make an action or event set what `until` reads, allow more `passes`, or set `passes` "
                                "to the number of passes the stage should always play"))
        if reached and not ran and stage.when:
            cause = rules.frozen(stage.when)
            if cause:
                out.append(_finding("stage_never_runs", f"stages.{stage.name}",
                                    f"never ran and cannot: its `when` is false and {cause}",
                                    f"set what `when` reads in an action or event, or fix `when`: {stage.when}"))
        elif ran and not woke and stage.who:
            cause = rules.frozen(stage.who)
            if cause:
                out.append(_finding("stage_wakes_nobody", f"stages.{stage.name}",
                                    f"ran {ran} time(s) but woke no agent, and cannot: {cause}",
                                    f"set what `who` reads in an action or event, or fix `who`: {stage.who}"))
    return out


def _stuck_measures(env: Env, outputs: dict[str, Any], rules: _Rules, failed: set[str]) -> list[dict[str, str]]:
    out = []
    if env.finished and env.status != "failed":
        for name, spec in env.contract.outputs.items():
            empty = name in outputs and outputs[name] is None and f"outputs.{name}" not in failed
            cause = rules.cause(spec.expr) if empty else None
            if cause:
                out.append(_finding("output_empty", f"outputs.{name}",
                                    f"is empty (null) at the end of the run: {cause}",
                                    "set what it reads in an action or event, or read what the rules do change"))
    for name, spec in env.contract.series_outputs().items():
        series = env.world.series.get(name, [])
        if len(series) < MIN_ROUNDS or _changes(series):
            continue
        cause = rules.cause(spec.sampled or "")
        if cause:
            shown = "null" if series[0] is None else json.dumps(series[0], default=str)
            out.append(_finding("metric_never_changes", f"outputs.{name}",
                                f"stayed {shown} for all {len(series)} rounds: {cause}",
                                "set what it reads in an action or event, or read what the rules do change"))
    return out


def _changes(series: list[Any]) -> bool:
    """Whether a metric's values ever differ, as their JSON would (every result of a stepped run asks): plain values
    are compared directly, anything else by its JSON text."""
    first = series[0]
    kind = type(first)
    if first is None or kind in (str, int, bool) or (kind is float and math.isfinite(first)):
        return any(type(value) is not kind or value != first for value in series)
    text = json.dumps(first, sort_keys=True, default=str)
    return any(json.dumps(value, sort_keys=True, default=str) != text for value in series[1:])


class _Rules:
    """What the contract's rules can write — found by reading them once, on first need."""

    def __init__(self, env: Env):
        self.env = env
        self._scanned: tuple[set[str], bool] | None = None

    def cause(self, expr: str) -> str | None:
        """Why ``expr`` cannot change, when everything it reads is frozen: records nothing posts to, properties no
        rule writes (and nothing wrote in this run), a winner no ending gives. None when anything it reads can
        change, or it reads nothing these can tell."""
        if "$pattern" in expr:  # a pattern follows time, chance or a memory input on its own
            return None
        env, world, written = self.env, self.env.world, self.env.state.diagnosis.written
        names, winner = self._scan()
        frozen: list[str] = []
        for record in _RECORD_READ.findall(expr):
            if record not in world.records_store:
                continue
            if world.records_store[record] or record in names:
                return None
            frozen.append(f"record `{record}`, which nothing posts to")
        declared = {prop for kind in env.contract.types for prop in env.contract.props_of(kind)}
        reads = [(f"`$world.{prop}`", prop) for prop in _WORLD_READ.findall(expr) if prop in env.contract.world]
        reads += [(f"`{prop}`", prop) for prop in _PROP_READ.findall(expr) if prop in declared
                  and prop not in _BUILT_IN_FIELDS]
        for shown, prop in reads:
            if prop in written or prop in names:
                return None
            frozen.append(f"{shown}, which no rule changes")
        if "$result.winner" in expr:
            if winner:
                return None
            frozen.append("`$result.winner`, which no `end` condition or effect gives")
        return "it reads only " + "; ".join(dict.fromkeys(frozen)) if frozen else None

    def frozen(self, expr: str) -> str | None:
        """:meth:`cause` for a condition that reads nothing that moves on its own (rounds, time, chance)."""
        return None if _MOVING.search(expr) else self.cause(expr)

    def _scan(self) -> tuple[set[str], bool]:
        """Every name the rules could write or post to (plus every name a mechanism owns), and whether any rule
        gives a winner."""
        if self._scanned is None:
            contract = self.env.contract
            names: set[str] = set()
            winner = [False]
            data = contract.model_dump(by_alias=True, warnings=False)  # reading only: loose values are fine here
            for section in _RULE_SECTIONS:
                _walk(data.get(section), names, winner)
            for spec in data.get("types", {}).values():
                _walk(spec.get("policies"), names, winner)
            names |= _mechanism_owned(contract)
            self._scanned = (names, winner[0])
        return self._scanned


def _walk(value: Any, names: set[str], winner: list[bool]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "props" and isinstance(item, dict):
                names.update(item)
            if key == "winner" and item:
                winner[0] = True
            _walk(item, names, winner)
    elif isinstance(value, list):
        for item in value:
            _walk(item, names, winner)
    elif isinstance(value, str):
        names.update(_ASSIGNED.findall(value))
        names.add(value.strip())  # a bare name as a setting: {"transfer": "cash"}, {"post": "chat"}


def _mechanism_owned(contract: Any) -> set[str]:
    """Properties and records a mechanism declared: its own code writes them."""
    source = contract._source
    if not contract.mechanisms or not isinstance(source, dict):
        return set()
    written_by_author = set(source.get("world") or {}) | set(source.get("records") or {})
    for spec in (source.get("types") or {}).values():
        if isinstance(spec, dict):
            written_by_author |= set(spec.get("props") or {})
    declared = set(contract.world) | set(contract.records)
    declared |= {prop for kind in contract.types for prop in contract.props_of(kind)}
    return declared - written_by_author
