"""Judged contest domain module — generic verbal-duel + jury vote engine.

One engine, many scenarios. Two contestants take turns making arguments
(or verses, or pitches) over a configurable number of rounds. An
odd-sized panel of AI judges watches silently. After the contest rounds
finish, the panel switches to verdict mode: each judge casts one vote
for whichever contestant they believed won overall. Majority wins.

Scenarios (selectable via `scenario` param — flavor only, mechanics
identical):
  - 'debate'      formal argument about a motion
  - 'rap_battle'  freestyle bars / disses about a beef
  - 'mock_trial'  prosecution vs defense over a charge

Custom actions:
  - `make_argument(text="…")`   contestants, contest phase
  - `cast_vote(winner=<id>, reasoning="…")`   judges, verdict phase

Events emitted:
  - judged_contest_argument   contestant spoke this round
  - judged_contest_phase      phase transition (contest → verdict)
  - judged_contest_vote       judge cast a vote
  - judged_contest_verdict    final winner + tallied votes
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


SCENARIO_FLAVOR: Dict[str, Dict[str, str]] = {
    "debate": {
        "verb_self": "argue your position",
        "verb_obs": "presents an argument",
        "rubric": (
            "Reward logical structure, evidence, and rebuttals that actually "
            "engage the opponent's strongest point. Penalize fallacies, "
            "strawmen, and dodges."
        ),
    },
    "rap_battle": {
        "verb_self": "spit your bars",
        "verb_obs": "drops bars",
        "rubric": (
            "Reward rhyme density, wordplay, flow, on-topic disses, and "
            "punchlines that land. Penalize biting lines, weak flow, "
            "and bars that don't connect to the beef."
        ),
    },
    "mock_trial": {
        "verb_self": "deliver your side's argument",
        "verb_obs": "presents to the court",
        "rubric": (
            "Reward legal reasoning, citation of precedent (real or "
            "in-fiction), and clean cross-examination logic. Penalize "
            "emotional pleas with no grounding."
        ),
    },
    "roast_battle": {
        "verb_self": "land your roast",
        "verb_obs": "drops a roast",
        "rubric": (
            "Reward observational specificity, cutting wordplay, and "
            "jokes that genuinely PUNCH (not just insult). Penalize "
            "lazy attacks (height/looks generic stuff), recycled bits, "
            "and roasts that don't actually land. Best roasts are "
            "MEAN but FUNNY — both, or it doesn't score."
        ),
    },
    "apology_craft": {
        "verb_self": "draft your apology",
        "verb_obs": "delivers an apology",
        "rubric": (
            "Reward genuine ownership (no 'I'm sorry if you felt…'), "
            "concrete acknowledgment of harm caused, and a credible "
            "plan to prevent recurrence. Penalize deflection, "
            "self-pity, conditional language, and 'I'm sorry BUT…' "
            "constructions. The best apology is the one that would "
            "actually mend the relationship in real life."
        ),
    },
}


class JudgedContestModule(DomainModule):
    """Two-party debate / rap-battle / mock-trial / pitch-off + jury vote."""

    def __init__(self, name: str = "judged_contest",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._scenario: str = str(p.get("scenario") or "debate")
        self._topic: str = str(p.get("topic") or "(no topic supplied)")
        # Both knobs are forced odd. Jury size odd ⇒ tie-proof verdict.
        # Round count odd ⇒ one contestant always has the last word
        # (turn order is symmetric within a round, but the FIRST move of
        # round N+1 alternates, so an odd round count gives one side the
        # decisive closing statement).
        self._contest_rounds: int = int(p.get("contest_rounds") or 3)
        if self._contest_rounds % 2 == 0:
            self._contest_rounds += 1
        if self._contest_rounds < 1:
            self._contest_rounds = 1
        self._jury_size_param: int = int(p.get("jury_size") or 3)
        if self._jury_size_param % 2 == 0:
            self._jury_size_param += 1
        if self._jury_size_param < 1:
            self._jury_size_param = 1

        # Identified at first tick.
        self._contestants: List[str] = []
        self._judges: List[str] = []
        self._initialized = False

        # State machine:
        #   - phase 'speaking'  → contestants speak this round
        #   - phase 'voting'    → judges vote on the most recent exchange
        #   - phase 'done'      → contest over, overall winner declared
        # Each contest round = one speaking sub-phase + one voting sub-phase.
        self._phase: str = "speaking"
        self._current_round: int = 1            # 1-indexed round counter
        self._spoken_this_round: set = set()
        self._votes_this_round: Dict[str, str] = {}   # judge_id → contestant_id (this round only)
        self._transcript: List[Dict[str, Any]] = []   # [{round, speaker, text}]
        # Per-round verdicts: list of {"round": int, "winner": cid, "tally": {cid: votes}}
        self._round_results: List[Dict[str, Any]] = []
        # Overall champion — set when the final round's vote is tallied.
        self._winner: Optional[str] = None

    @property
    def description(self) -> str:
        return (
            "Two-party verbal contest decided by an odd-sized AI jury. "
            "Scenarios: debate, rap battle, mock trial, pitch off."
        )

    @property
    def custom_actions(self) -> List[str]:
        return ["make_argument", "cast_vote"]

    @property
    def required_properties(self) -> List[str]:
        return []

    # ------------------------------------------------------------------ #
    # Seat assignment
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        contestants: List[str] = []
        judges: List[str] = []
        for ent in agents:
            etype = getattr(ent, "entity_type", "") or ""
            if etype == "Contestant":
                contestants.append(ent.id)
            elif etype == "Judge":
                judges.append(ent.id)
        if len(contestants) != 2:
            raise ValueError(f"The contest requires exactly two contestants; found {len(contestants)}.")
        if len(judges) < self._jury_size_param:
            raise ValueError(f"The configured {self._jury_size_param}-person jury has only {len(judges)} seats.")
        self._contestants = contestants
        self._judges = judges[:self._jury_size_param]
        for ent in agents:
            if ent.id in judges:
                ent.properties["jury_active"] = ent.id in self._judges
            if self._scenario == "debate" and ent.id in contestants:
                ent.properties["debate_position"] = "affirmative" if ent.id == contestants[0] else "negative"
        self._initialized = True
        logger.info(
            "judged_contest: scenario=%s, topic=%r, contestants=%s, judges=%s (jury_size=%d), rounds=%d",
            self._scenario, self._topic[:60],
            [self._name_of(state, c) for c in self._contestants],
            [self._name_of(state, j) for j in self._judges],
            len(self._judges), self._contest_rounds,
        )

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_initialized = self._initialized
        self._seed_from_state(state)
        if not was_initialized:
            return [{"type": "judged_contest_seated", "judges": list(self._judges),
                     "contestants": [{"id": cid, **({"position": "affirmative" if i == 0 else "negative"} if self._scenario == "debate" else {})}
                                     for i, cid in enumerate(self._contestants)]}]
        return []

    # ------------------------------------------------------------------ #
    # Action filtering
    # ------------------------------------------------------------------ #

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        if not self._initialized or self._phase == "done":
            return []
        out = [a for a in valid_actions if a not in self.custom_actions]
        if entity_id in self._contestants:
            # Contestants only act when it's the speaking phase of a round
            # they haven't spoken in yet.
            if self._phase == "speaking" and entity_id not in self._spoken_this_round:
                out.append("make_argument")
            return out
        if entity_id in self._judges:
            # Judges only act in the voting phase, once per contest round.
            if self._phase == "voting" and entity_id not in self._votes_this_round:
                out.append("cast_vote")
            return out
        return out

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        actor_id = getattr(actor, "id", None)
        if action_name == "make_argument":
            if actor_id not in self._contestants:
                return "Only contestants can make_argument."
            if self._phase != "speaking":
                return "Speaking is closed for this round — the jury is voting."
            if actor_id in self._spoken_this_round:
                return "You already spoke this round — wait for the other contestant."
        if action_name == "cast_vote":
            if actor_id not in self._judges:
                return "Only judges can cast a vote."
            if self._phase != "voting":
                return "Voting hasn't opened yet — the contestants are still speaking."
            if actor_id in self._votes_this_round:
                return "You already voted in this round."
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        details = {**raw, **params}

        if action_name == "make_argument":
            return self._handle_argument(actor_id, details, state)
        if action_name == "cast_vote":
            return self._handle_vote(actor_id, details, state)
        return []

    # ------------------------------------------------------------------ #
    # Argument handling
    # ------------------------------------------------------------------ #

    def _handle_argument(self, actor_id: str, details: Dict[str, Any],
                         state: Any) -> List[Dict[str, Any]]:
        text = str(details.get("text") or "").strip()
        speaker_name = self._name_of(state, actor_id)
        if not text:
            return [{
                "type": "judged_contest_invalid",
                "player": actor_id,
                "reason": "empty_argument",
                "narrative": f"{speaker_name} stepped up but said nothing.",
            }]

        round_no = self._current_round
        entry = {"round": round_no, "speaker": actor_id, "speaker_name": speaker_name, "text": text}
        self._transcript.append(entry)
        self._spoken_this_round.add(actor_id)

        flavor = SCENARIO_FLAVOR.get(self._scenario, SCENARIO_FLAVOR["debate"])
        events: List[Dict[str, Any]] = [{
            "type": "judged_contest_argument",
            "speaker": actor_id,
            "speaker_name": speaker_name,
            "round": round_no,
            "text": text,
            "scenario": self._scenario,
            "narrative": f"Round {round_no} — {speaker_name} {flavor['verb_obs']}: \"{text[:200]}\"",
        }]

        # Both contestants spoke this round → open voting for this round.
        if len(self._spoken_this_round) >= len(self._contestants):
            self._phase = "voting"
            events.append({
                "type": "judged_contest_phase",
                "phase": "voting",
                "round": round_no,
                "narrative": (
                    f"Round {round_no} closed. The {len(self._judges)}-judge "
                    f"panel votes on this exchange."
                ),
            })
        return events

    # ------------------------------------------------------------------ #
    # Vote handling
    # ------------------------------------------------------------------ #

    def _handle_vote(self, actor_id: str, details: Dict[str, Any],
                     state: Any) -> List[Dict[str, Any]]:
        judge_name = self._name_of(state, actor_id)
        choice = str(details.get("winner") or "").strip()
        reasoning = str(details.get("reasoning") or "").strip()

        # Accept either entity_id or display name.
        contestant_id = self._resolve_contestant(choice, state)
        if contestant_id is None:
            options = ", ".join(
                f"{cid} ({self._name_of(state, cid)})" for cid in self._contestants
            )
            return [{
                "type": "judged_contest_invalid",
                "player": actor_id,
                "reason": "bad_winner",
                "attempted": choice,
                "narrative": (
                    f"{judge_name} cast a vote for unknown contestant "
                    f"{choice!r}. Valid options: {options}."
                ),
            }]

        self._votes_this_round[actor_id] = contestant_id
        # Mirror onto the judge entity for the UI.
        if hasattr(state, "entities"):
            ent = state.entities.get(actor_id)
            if ent is not None and hasattr(ent, "properties"):
                ent.properties["voted_for"] = contestant_id

        winner_name = self._name_of(state, contestant_id)
        round_no = self._current_round
        events: List[Dict[str, Any]] = [{
            "type": "judged_contest_vote",
            "judge": actor_id,
            "judge_name": judge_name,
            "voted_for": contestant_id,
            "voted_for_name": winner_name,
            "reasoning": reasoning,
            "round": round_no,
            "narrative": (
                f"R{round_no}: {judge_name} votes {winner_name}"
                + (f" — \"{reasoning[:160]}\"" if reasoning else ".")
            ),
        }]

        # If every judge has voted this round, tally + decide round winner.
        if len(self._votes_this_round) >= len(self._judges):
            round_tally: Dict[str, int] = {cid: 0 for cid in self._contestants}
            for vote in self._votes_this_round.values():
                round_tally[vote] = round_tally.get(vote, 0) + 1
            round_winner = max(round_tally, key=lambda k: round_tally[k])
            self._round_results.append({
                "round": round_no,
                "winner": round_winner,
                "tally": dict(round_tally),
                "votes": dict(self._votes_this_round),
            })

            round_winner_name = self._name_of(state, round_winner)
            # Aggregate score = number of rounds won so far per contestant.
            score = self._aggregate_score()
            score_view = {self._name_of(state, c): score.get(c, 0) for c in self._contestants}

            # Mirror the running totals onto contestant entities.
            if hasattr(state, "entities"):
                for cid in self._contestants:
                    ent = state.entities.get(cid)
                    if ent and hasattr(ent, "properties"):
                        ent.properties["votes_received"] = score.get(cid, 0)
            events.append({
                "type": "judged_contest_round_result",
                "round": round_no,
                "winner": round_winner,
                "winner_name": round_winner_name,
                "round_tally": dict(round_tally),
                "score": dict(score),
                "narrative": (
                    f"Round {round_no} → {round_winner_name} ("
                    + ", ".join(f"{n}: {v}" for n, v in score_view.items())
                    + ")"
                ),
            })

            # Final contest round? → declare the overall champion.
            if round_no >= self._contest_rounds:
                champion = max(score, key=lambda k: score.get(k, 0))
                self._winner = champion
                self._phase = "done"
                if hasattr(state, "entities"):
                    for cid in self._contestants:
                        ent = state.entities.get(cid)
                        if ent and hasattr(ent, "properties"):
                            ent.properties["is_winner"] = (cid == champion)
                champ_name = self._name_of(state, champion)
                champ_score = score.get(champion, 0)
                events.append({
                    # Surface event_type so the engine emits this as a
                    # first-class SimEvent the termination condition can match.
                    "event_type": "judged_contest_verdict",
                    "type": "judged_contest_verdict",
                    "winner": champion,
                    "winner_name": champ_name,
                    "score": dict(score),
                    "rounds": list(self._round_results),
                    "scenario": self._scenario,
                    "topic": self._topic,
                    "narrative": (
                        f"VERDICT — {champ_name} wins the "
                        f"{self._scenario.replace('_', ' ')} "
                        f"{champ_score}–{min(score.values())} on rounds."
                    ),
                })
            else:
                # More rounds to go — flip back to speaking phase, alternate
                # who goes first (next round, the OTHER contestant leads —
                # implemented by clearing spoken_this_round; the parallel
                # resolver will pick based on entity iteration, but the
                # `transcript` exposes who-spoke-first to all agents).
                self._current_round += 1
                self._phase = "speaking"
                self._spoken_this_round = set()
                self._votes_this_round = {}
                events.append({
                    "type": "judged_contest_phase",
                    "phase": "speaking",
                    "round": self._current_round,
                    "narrative": (
                        f"Round {self._current_round} of {self._contest_rounds} begins — "
                        f"contestants take the floor."
                    ),
                })
        return events

    def _aggregate_score(self) -> Dict[str, int]:
        """Number of rounds each contestant has won so far."""
        score: Dict[str, int] = {cid: 0 for cid in self._contestants}
        for r in self._round_results:
            w = r.get("winner")
            if w:
                score[w] = score.get(w, 0) + 1
        return score

    def _resolve_contestant(self, raw: str, state: Any) -> Optional[str]:
        """Accept entity_id, display name, or name fragment."""
        if not raw:
            return None
        lc = raw.lower().strip()
        # Exact id match
        if raw in self._contestants:
            return raw
        # Name match (case-insensitive)
        for cid in self._contestants:
            name = self._name_of(state, cid).lower()
            if lc == name or lc in name or name in lc:
                return cid
        return None

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if not self._initialized:
            return {}
        flavor = SCENARIO_FLAVOR.get(self._scenario, SCENARIO_FLAVOR["debate"])
        contestants_info = [
            {"id": cid, "name": self._name_of(state, cid), **({"position": "affirmative" if cid == self._contestants[0] else "negative"} if self._scenario == "debate" else {})} for cid in self._contestants
        ]
        # Transcript text is authored by competing contestants and is a
        # prompt-injection vector for the judge agents. Wrap each speech
        # in an <UNTRUSTED-SPEECH> tag so the consuming agent treats it
        # as data, not instructions (see the IMMUTABLE RULES section in
        # the agent system prompt).
        transcript_view = [
            {
                "round": e["round"],
                "speaker": e["speaker_name"],
                "text": (
                    f'<UNTRUSTED-SPEECH from="{e["speaker_name"]}" '
                    f'speaker_id="{e.get("speaker", "")}" round="{e["round"]}">\n'
                    f'{e["text"]}\n'
                    f'</UNTRUSTED-SPEECH>'
                ),
            }
            for e in self._transcript[-40:]
        ]
        score = self._aggregate_score()
        base: Dict[str, Any] = {
            "scenario": self._scenario,
            "topic": self._topic,
            "rubric": flavor["rubric"],
            "phase": self._phase,
            "current_round": self._current_round,
            "contest_rounds_total": self._contest_rounds,
            "contestants": contestants_info,
            "transcript": transcript_view,
            "jury_size": len(self._judges),
            "score": dict(score),
            "round_results": [
                {"round": r["round"], "winner": r["winner"], "tally": dict(r["tally"])}
                for r in self._round_results
            ],
        }
        if self._phase == "done":
            base["winner_id"] = self._winner
            base["winner_name"] = self._name_of(state, self._winner) if self._winner else None
            return base

        if entity_id in self._contestants:
            opponent_id = next((c for c in self._contestants if c != entity_id), None)
            is_your_turn = (
                self._phase == "speaking"
                and entity_id not in self._spoken_this_round
            )
            base["role"] = "contestant"
            base["your_id"] = entity_id
            base["opponent_id"] = opponent_id
            base["opponent_name"] = self._name_of(state, opponent_id) if opponent_id else None
            base["is_your_turn"] = is_your_turn
            if self._scenario == "debate":
                base["assigned_position"] = "affirmative" if entity_id == self._contestants[0] else "negative"
                base["opponent_position"] = "negative" if entity_id == self._contestants[0] else "affirmative"
            if is_your_turn:
                topic_hammer = (
                    f"TOPIC / BEEF: \"{self._topic}\". "
                    f"Every bar in your verse MUST connect to this topic — "
                    f"name it, twist it, flip it, but stay ON it. "
                    f"Generic brag-rap with no link to the topic LOSES rounds."
                    if self._scenario == "rap_battle"
                    else f"TOPIC: \"{self._topic}\". Address it directly."
                )
                base["instructions"] = (
                    f"{topic_hammer} "
                    f"You are a CONTESTANT in a {self._scenario.replace('_', ' ')}. "
                    f"Round {self._current_round} of {self._contest_rounds}. "
                    f"The jury votes after EACH round, so make THIS round count — "
                    f"don't save your best for later. Call `make_argument` with a "
                    f"`text` parameter to {flavor['verb_self']}."
                )
            elif self._phase == "speaking":
                base["instructions"] = "Your opponent is speaking — wait your turn."
            else:
                base["instructions"] = (
                    f"Round {self._current_round} voting in progress. "
                    f"Next round opens after the jury decides."
                )
            if self._scenario == "debate":
                position = base["assigned_position"]
                direction = "FOR" if position == "affirmative" else "AGAINST"
                base["instructions"] += (
                    f" Your fixed debate position is {position.upper()}: argue {direction} the motion. "
                    "Your custom strategy controls style, not your assigned side. "
                    "Do not adopt the opponent's position."
                )
            return base

        if entity_id in self._judges:
            base["role"] = "judge"
            base["your_id"] = entity_id
            base["has_voted_this_round"] = entity_id in self._votes_this_round
            if self._phase == "speaking":
                base["instructions"] = (
                    f"You are a JUDGE on a {len(self._judges)}-person panel. "
                    f"Round {self._current_round} of {self._contest_rounds}. "
                    f"Watch this round's exchange — you'll vote on it "
                    f"as soon as both contestants finish. Rubric: {flavor['rubric']}"
                )
            elif self._phase == "voting":
                base["instructions"] = (
                    f"Round {self._current_round} voting is OPEN. Cast ONE "
                    "vote via `cast_vote(winner=<contestant_id>, "
                    "reasoning='...')` for whoever you think won THIS round's "
                    "exchange — not the contest overall. The `winner` MUST "
                    f"match one of the contestant ids. Rubric: {flavor['rubric']}. "
                    f"Contestant ids: {[c['id'] for c in contestants_info]}."
                )
                base["legal_winners"] = [c["id"] for c in contestants_info]
            return base

        # Spectator
        base["role"] = "spectator"
        return base

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _name_of(self, state: Any, entity_id: Optional[str]) -> str:
        if not entity_id:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(entity_id)
            if ent is not None:
                return getattr(ent, "name", entity_id)
        return entity_id

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "scenario": self._scenario,
            "topic": self._topic,
            "contest_rounds": self._contest_rounds,
            "jury_size_param": self._jury_size_param,
            "contestants": list(self._contestants),
            "judges": list(self._judges),
            "phase": self._phase,
            "current_round": self._current_round,
            "spoken_this_round": list(self._spoken_this_round),
            "votes_this_round": dict(self._votes_this_round),
            "transcript": list(self._transcript),
            "round_results": list(self._round_results),
            "winner": self._winner,
            "initialized": self._initialized,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "JudgedContestModule":
        params = data.get("params") or {}
        mod = cls(name=data.get("name", "judged_contest"), params=params)
        s = data.get("state") or {}
        mod._scenario = s.get("scenario", "debate")
        mod._topic = s.get("topic", "")
        mod._contest_rounds = int(s.get("contest_rounds") or 3)
        mod._jury_size_param = int(s.get("jury_size_param") or 3)
        mod._contestants = list(s.get("contestants") or [])
        mod._judges = list(s.get("judges") or [])
        mod._phase = s.get("phase", "speaking")
        mod._current_round = int(s.get("current_round") or 1)
        mod._spoken_this_round = set(s.get("spoken_this_round") or [])
        mod._votes_this_round = dict(s.get("votes_this_round") or {})
        mod._transcript = list(s.get("transcript") or [])
        mod._round_results = list(s.get("round_results") or [])
        mod._winner = s.get("winner")
        mod._initialized = bool(s.get("initialized", False))
        return mod
