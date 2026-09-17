"""Parliament domain module — a legislative chamber debates and votes on a bill.

Drives a full legislative session on top of the generic action engine:
  - A chamber seated with party-affiliated legislators.
  - A Speaker / presiding officer chosen per the configured mode.
  - Phased session: first reading -> debate (N rounds) -> division -> result.
  - Floor control by the configured order of debate.
  - A vote tallied against the configured passing threshold.

Custom actions:
  - `deliver_speech`: params `stance` ('for'|'against'|'undecided') and `text`.
    Only available to the legislator currently recognized on the floor.
  - `cast_vote`: param `vote` ('yea'|'nay'|'abstain'). Available in the
    division phase to every eligible legislator.

Events emitted (consumed by ParliamentViz + UI):
  - `parliament_setup`  { ...snapshot }            — chamber seated, bill read
  - `parliament_phase`  { phase, round, ...snapshot }
  - `parliament_speech` { speaker, party, stance, text, ...snapshot }
  - `parliament_vote`   { member, vote, ...snapshot }
  - `parliament_result` { passed, tally, ...snapshot }   (terminal)

Configuration (module params, set from the template / run config):
  speaker_mode, speaking_order, voting_threshold, debate_rounds,
  bill, parties (list of {name, ideology, seats, stance}).
"""
from __future__ import annotations

import logging
import random
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)

# Voting thresholds → minimum yea share of (yea + nay).
THRESHOLD_RATIO: Dict[str, float] = {
    "simple_majority": 0.5,      # strictly more yea than nay
    "three_fifths": 0.6,
    "two_thirds": 2.0 / 3.0,
}
THRESHOLD_LABEL: Dict[str, str] = {
    "simple_majority": "a simple majority — more yea votes than nay",
    "three_fifths": "three-fifths (60%) of the votes cast",
    "two_thirds": "two-thirds (66.7%) of the votes cast",
}


DEFAULT_PARTIES: List[Dict[str, Any]] = [
    {"name": "Progressive Alliance",
     "ideology": "Left-wing: ambitious reform, public investment, regulation.",
     "seats": 4, "stance": "support"},
    {"name": "Conservative Union",
     "ideology": "Right-wing: low taxes, minimal regulation, tradition.",
     "seats": 4, "stance": "oppose"},
]


class ParliamentModule(DomainModule):
    """A configurable legislative chamber that debates and votes on a bill."""

    def __init__(self, name: str = "parliament",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._speaker_mode: str = str(p.get("speaker_mode", "neutral_arbiter"))
        self._speaking_order: str = str(p.get("speaking_order", "party_rotation"))
        self._voting_threshold: str = str(p.get("voting_threshold", "simple_majority"))
        self._debate_rounds: int = max(1, int(p.get("debate_rounds", 2) or 2))
        self._bill: str = str(p.get("bill") or "").strip() or (
            "A bill is before the chamber for debate and a vote."
        )
        parties = p.get("parties")
        if not isinstance(parties, list) or not parties:
            parties = DEFAULT_PARTIES
        self._parties: List[Dict[str, Any]] = [
            {
                "name": str(pt.get("name") or f"Party {i + 1}"),
                "ideology": str(pt.get("ideology") or "Unspecified platform."),
                "seats": max(0, int(pt.get("seats", 1) or 0)),
                "stance": str(pt.get("stance") or "free").lower(),
            }
            for i, pt in enumerate(parties)
        ]

        # Runtime state.
        self._members: List[str] = []          # entity ids, seat order
        self._party_of: Dict[str, str] = {}
        self._ideology_of_party: Dict[str, str] = {}
        self._stance_of_party: Dict[str, str] = {}
        self._seniority_of: Dict[str, float] = {}
        self._speaker_id: Optional[str] = None
        self._phase: str = "first_reading"
        self._round: int = 1
        self._speaking_queue: List[str] = []
        self._queue_pos: int = 0
        self._transcript: List[Dict[str, Any]] = []
        self._votes: Dict[str, str] = {}
        self._result: Optional[Dict[str, Any]] = None
        self._terminal: bool = False
        self._initialized: bool = False
        self._setup_emitted: bool = False
        self._log: List[str] = []
        self._rng = random.Random()

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return "A legislative chamber debates and votes on a bill."

    @property
    def custom_actions(self) -> List[str]:
        return ["deliver_speech", "cast_vote"]

    @property
    def required_properties(self) -> List[str]:
        return ["party"]

    # ------------------------------------------------------------------ #
    # Seating
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if len(agents) < 2:
            return
        self._members = [a.id for a in agents]
        for a in agents:
            self._seniority_of[a.id] = float(
                getattr(a, "properties", {}).get("seniority", 0.5) or 0.5
            )

        # Assign each member to a party in proportion to configured seats.
        assignment = self._allocate_parties(len(self._members))
        for ent, party_name in zip(agents, assignment):
            self._party_of[ent.id] = party_name
            if hasattr(ent, "properties"):
                ent.properties["party"] = party_name
                ent.properties["voted"] = ""
                ent.properties["is_speaker"] = False
        for pt in self._parties:
            self._ideology_of_party[pt["name"]] = pt["ideology"]
            self._stance_of_party[pt["name"]] = pt["stance"]

        # Designate the Speaker / presiding officer.
        self._designate_speaker(agents)

        self._initialized = True
        logger.info("Parliament seated: %d members, %d parties",
                    len(self._members), len(self._parties))

    def _allocate_parties(self, n: int) -> List[str]:
        """Largest-remainder allocation of n seats across the configured parties."""
        parties = self._parties
        total = sum(pt["seats"] for pt in parties)
        if total <= 0:
            return [parties[i % len(parties)]["name"] for i in range(n)]
        raw = [(pt["name"], pt["seats"] / total * n) for pt in parties]
        alloc = [[name, int(x)] for name, x in raw]
        assigned = sum(a[1] for a in alloc)
        order = sorted(range(len(raw)), key=lambda i: raw[i][1] - int(raw[i][1]),
                       reverse=True)
        i = 0
        while assigned < n and order:
            alloc[order[i % len(order)]][1] += 1
            assigned += 1
            i += 1
        seq: List[str] = []
        for name, cnt in alloc:
            seq.extend([name] * cnt)
        j = 0
        while len(seq) < n:
            seq.append(parties[j % len(parties)]["name"])
            j += 1
        return seq[:n]

    def _designate_speaker(self, agents: List[Any]) -> None:
        if self._speaker_mode == "none" or not agents:
            self._speaker_id = None
            return
        if self._speaker_mode == "partisan_leader":
            # Speaker leads the largest party.
            counts: Dict[str, int] = {}
            for m in self._members:
                counts[self._party_of[m]] = counts.get(self._party_of[m], 0) + 1
            top_party = max(counts, key=lambda k: counts[k])
            speaker = next(m for m in self._members if self._party_of[m] == top_party)
        elif self._speaker_mode == "rotating_lot":
            speaker = self._rng.choice(self._members)
        else:  # neutral_arbiter
            speaker = self._members[0]
            # A neutral Speaker renounces party allegiance and does not vote.
            self._party_of[speaker] = "Speaker's Chair"
        self._speaker_id = speaker
        ent = next((a for a in agents if a.id == speaker), None)
        if ent is not None and hasattr(ent, "properties"):
            ent.properties["is_speaker"] = True
            ent.properties["party"] = self._party_of[speaker]

    def _is_neutral_chair(self, member_id: str) -> bool:
        return (member_id == self._speaker_id
                and self._speaker_mode == "neutral_arbiter")

    def _eligible_voters(self) -> List[str]:
        return [m for m in self._members if not self._is_neutral_chair(m)]

    # ------------------------------------------------------------------ #
    # tick / floor control
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if not self._initialized or self._setup_emitted:
            return []
        self._setup_emitted = True
        self._phase = "debate"
        self._round = 1
        self._speaking_queue = self._build_speaking_queue(1)
        self._queue_pos = 0
        events = [{
            "type": "parliament_setup",
            **self._snapshot(state),
            "narrative": (
                f"The chamber is seated. First reading of the bill — "
                f"{self._debate_rounds} round(s) of debate will follow, "
                f"then a division."
            ),
        }, {
            "type": "parliament_phase",
            "phase": "debate",
            "round": 1,
            **self._snapshot(state),
            "narrative": "Debate begins — round 1.",
        }]
        return events

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        out = [a for a in valid_actions if a not in self.custom_actions]
        if self._terminal or self._phase in ("first_reading", "result"):
            return out if entity_id not in self._members else []
        if entity_id not in self._members:
            return out
        if self._phase == "debate":
            if (self._queue_pos < len(self._speaking_queue)
                    and self._speaking_queue[self._queue_pos] == entity_id):
                out.append("deliver_speech")
        elif self._phase == "division":
            if entity_id in self._eligible_voters() and entity_id not in self._votes:
                out.append("cast_vote")
        return out

    def _build_speaking_queue(self, round_num: int) -> List[str]:
        """One speaker per party for this round (rotating which member),
        ordered per the configured order of debate. A single-bloc chamber
        (e.g. Greek Ecclesia) yields a rotating window of speakers."""
        debating = [m for m in self._members if not self._is_neutral_chair(m)]
        if not debating:
            return []
        by_party: "OrderedDict[str, List[str]]" = OrderedDict()
        for m in debating:
            by_party.setdefault(self._party_of.get(m, "Independent"), []).append(m)

        if len(by_party) <= 1:
            bloc = debating
            k = min(4, len(bloc))
            start = ((round_num - 1) * k) % len(bloc)
            return [bloc[(start + i) % len(bloc)] for i in range(k)]

        order = self._order_parties(list(by_party.keys()), by_party, round_num)
        queue: List[str] = []
        for pname in order:
            members = by_party[pname]
            queue.append(members[(round_num - 1) % len(members)])
        return queue

    def _order_parties(self, names: List[str],
                       by_party: "OrderedDict[str, List[str]]",
                       round_num: int) -> List[str]:
        so = self._speaking_order
        if so == "speaker_recognized":
            return sorted(names, key=lambda p: -len(by_party[p]))
        if so == "seniority":
            return sorted(
                names,
                key=lambda p: -max(self._seniority_of.get(m, 0.5) for m in by_party[p]),
            )
        if so == "free_for_all":
            shuffled = list(names)
            self._rng.shuffle(shuffled)
            return shuffled
        # party_rotation (default) — rotate the starting party each round.
        k = (round_num - 1) % len(names)
        return names[k:] + names[:k]

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                        state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        if self._terminal:
            return "The session is over"
        actor_id = getattr(actor, "id", None)
        if action_name == "deliver_speech":
            if self._phase != "debate":
                return "The chamber is not in debate"
            if (self._queue_pos >= len(self._speaking_queue)
                    or self._speaking_queue[self._queue_pos] != actor_id):
                return "You do not have the floor"
        if action_name == "cast_vote":
            if self._phase != "division":
                return "The chamber is not dividing"
            if actor_id not in self._eligible_voters():
                return "You are not eligible to vote"
            if actor_id in self._votes:
                return "You have already voted"
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions or self._terminal:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        if action_name == "deliver_speech":
            return self._handle_speech(actor_id, params, raw, state)
        if action_name == "cast_vote":
            return self._handle_vote(actor_id, params, raw, state)
        return []

    def _handle_speech(self, actor_id: str, params: Dict[str, Any],
                       raw: Dict[str, Any], state: Any) -> List[Dict[str, Any]]:
        if (self._queue_pos >= len(self._speaking_queue)
                or self._speaking_queue[self._queue_pos] != actor_id):
            return []
        stance = str(params.get("stance", raw.get("stance", "undecided"))).lower().strip()
        if stance not in ("for", "against", "undecided"):
            stance = "undecided"
        text = str(params.get("text", raw.get("text", ""))).strip()
        name = self._name_of(state, actor_id)
        party = self._party_of.get(actor_id, "Independent")
        self._transcript.append({
            "round": self._round,
            "speaker_id": actor_id,
            "speaker_name": name,
            "party": party,
            "stance": stance,
            "text": text,
        })
        stance_word = {"for": "speaks FOR", "against": "speaks AGAINST",
                       "undecided": "addresses"}[stance]
        narrative = f"[Round {self._round}] {name} ({party}) {stance_word} the bill."
        self._log.append(narrative)
        events: List[Dict[str, Any]] = [{
            "type": "parliament_speech",
            "speaker": actor_id,
            "speaker_name": name,
            "party": party,
            "stance": stance,
            "round": self._round,
            "text": text,
            **self._snapshot(state),
            "narrative": narrative,
        }]

        self._queue_pos += 1
        if self._queue_pos >= len(self._speaking_queue):
            # Round complete.
            if self._round < self._debate_rounds:
                self._round += 1
                self._speaking_queue = self._build_speaking_queue(self._round)
                self._queue_pos = 0
                events.append({
                    "type": "parliament_phase",
                    "phase": "debate",
                    "round": self._round,
                    **self._snapshot(state),
                    "narrative": f"Debate continues — round {self._round}.",
                })
            else:
                self._phase = "division"
                events.append({
                    "type": "parliament_phase",
                    "phase": "division",
                    "round": self._round,
                    **self._snapshot(state),
                    "narrative": (
                        "Debate is closed. The chamber divides — every "
                        "eligible member now casts a vote."
                    ),
                })
        return events

    def _handle_vote(self, actor_id: str, params: Dict[str, Any],
                     raw: Dict[str, Any], state: Any) -> List[Dict[str, Any]]:
        if actor_id in self._votes or actor_id not in self._eligible_voters():
            return []
        vote = str(params.get("vote", raw.get("vote", "abstain"))).lower().strip()
        if vote not in ("yea", "nay", "abstain"):
            vote = "abstain"
        self._votes[actor_id] = vote
        ent = state.entities.get(actor_id) if hasattr(state, "entities") else None
        if ent is not None and hasattr(ent, "properties"):
            ent.properties["voted"] = vote
        name = self._name_of(state, actor_id)
        narrative = f"{name} votes {vote.upper()}."
        self._log.append(narrative)
        events: List[Dict[str, Any]] = [{
            "type": "parliament_vote",
            "member": actor_id,
            "member_name": name,
            "party": self._party_of.get(actor_id, "Independent"),
            "vote": vote,
            **self._snapshot(state),
            "narrative": narrative,
        }]

        if len(self._votes) >= len(self._eligible_voters()):
            events.append(self._finish_division(state))
        return events

    def _finish_division(self, state: Any) -> Dict[str, Any]:
        yea = sum(1 for v in self._votes.values() if v == "yea")
        nay = sum(1 for v in self._votes.values() if v == "nay")
        abstain = sum(1 for v in self._votes.values() if v == "abstain")
        base = yea + nay
        ratio = THRESHOLD_RATIO.get(self._voting_threshold, 0.5)
        if self._voting_threshold == "simple_majority":
            passed = yea > nay
        else:
            passed = base > 0 and (yea / base) >= ratio
        self._result = {
            "passed": passed,
            "tally": {"yea": yea, "nay": nay, "abstain": abstain},
        }
        self._phase = "result"
        self._terminal = True
        verdict = "PASSES" if passed else "FAILS"
        narrative = (
            f"Division complete. The bill {verdict} — "
            f"Ayes {yea}, Noes {nay}, Abstentions {abstain}. "
            f"(Threshold: {THRESHOLD_LABEL.get(self._voting_threshold, '')}.)"
        )
        self._log.append(narrative)
        return {
            "event_type": "parliament_result",
            "type": "parliament_result",
            "passed": passed,
            "tally": {"yea": yea, "nay": nay, "abstain": abstain},
            "threshold": self._voting_threshold,
            **self._snapshot(state),
            "narrative": narrative,
        }

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in self._members:
            return {"role": "observer"}
        party = self._party_of.get(entity_id, "Independent")
        is_chair = self._is_neutral_chair(entity_id)
        is_speaker = entity_id == self._speaker_id
        whip = self._stance_of_party.get(party, "free")
        my_turn_speak = (
            self._phase == "debate"
            and self._queue_pos < len(self._speaking_queue)
            and self._speaking_queue[self._queue_pos] == entity_id
        )
        my_turn_vote = (
            self._phase == "division"
            and entity_id in self._eligible_voters()
            and entity_id not in self._votes
        )

        whip_text = {
            "support": "Your party's whip is to VOTE FOR (yea) the bill.",
            "oppose": "Your party's whip is to VOTE AGAINST (nay) the bill.",
            "free": "Your party has declared a FREE VOTE — vote your conscience.",
        }.get(whip, "Your party has declared a free vote.")

        if is_chair:
            instructions = (
                "You are the SPEAKER and preside over the chamber. You remain "
                "strictly impartial: you do not debate and you do not vote. "
                "Observe the proceedings."
            )
        elif my_turn_speak:
            instructions = (
                "YOU HAVE THE FLOOR. Deliver your speech now: call "
                "`deliver_speech` with `stance` ('for', 'against' or "
                "'undecided') and `text` (your argument to the chamber). "
                "Argue from your party's platform and your own convictions; "
                "engage with what previous speakers have said."
            )
        elif my_turn_vote:
            instructions = (
                "THE CHAMBER IS DIVIDING. Cast your vote now: call "
                "`cast_vote` with `vote` set to 'yea' (for), 'nay' (against) "
                "or 'abstain'. Weigh the debate you have heard, your party's "
                "whip, your loyalty, and your conscience."
            )
        elif self._phase == "debate":
            instructions = ("Debate is under way. Listen to the speeches — "
                            "you will be recognized or will vote in due course.")
        elif self._phase == "division":
            instructions = "The division is proceeding. Await the result."
        else:
            instructions = "The session has concluded."

        data: Dict[str, Any] = {
            "instructions": instructions,
            "is_your_turn": my_turn_speak or my_turn_vote,
            "phase": self._phase,
            "debate_round": self._round,
            "total_debate_rounds": self._debate_rounds,
            "bill": self._bill,
            "your_party": party,
            "your_party_platform": self._ideology_of_party.get(party, ""),
            "your_party_whip": whip_text,
            "your_role": "Speaker (presiding)" if is_speaker else "Member",
            "rules_of_procedure": {
                "order_of_debate": self._speaking_order.replace("_", " "),
                "passing_threshold": THRESHOLD_LABEL.get(self._voting_threshold, ""),
                "speaker": self._name_of(state, self._speaker_id) if self._speaker_id else "none",
            },
            "chamber_composition": [
                {"party": pt["name"], "seats": self._seat_count(pt["name"]),
                 "platform": pt["ideology"], "declared_position": pt["stance"]}
                for pt in self._parties
            ],
            "debate_transcript": self._render_transcript(),
            "recent_log": list(self._log[-12:]),
        }
        if self._phase in ("division", "result"):
            data["division_so_far"] = {
                "yea": sum(1 for v in self._votes.values() if v == "yea"),
                "nay": sum(1 for v in self._votes.values() if v == "nay"),
                "abstain": sum(1 for v in self._votes.values() if v == "abstain"),
                "not_yet_voted": len(self._eligible_voters()) - len(self._votes),
            }
        if self._result is not None:
            data["result"] = dict(self._result)
        return data

    def _render_transcript(self) -> str:
        """Multi-line transcript of the debate so far, grouped by round.
        Each speech is wrapped in <UNTRUSTED-SPEECH> tags — the speech
        text is authored by other players and is a prompt-injection
        vector for the agents reading it. See the IMMUTABLE RULES
        section in the agent system prompt."""
        if not self._transcript:
            return "(no speeches yet — the debate is just beginning)"
        lines: List[str] = []
        current_round = None
        for sp in self._transcript:
            if sp["round"] != current_round:
                current_round = sp["round"]
                lines.append(f"--- Debate Round {current_round} ---")
            tag = {"for": "FOR", "against": "AGAINST",
                   "undecided": "UNDECIDED"}.get(sp["stance"], "—")
            speaker = sp.get("speaker_name", "Unknown")
            party = sp.get("party", "")
            lines.append(f"[{tag}] {speaker} ({party}):")
            lines.append(
                f'  <UNTRUSTED-SPEECH from="{speaker}" party="{party}" round="{sp["round"]}">'
            )
            lines.append(f"  {sp['text']}")
            lines.append("  </UNTRUSTED-SPEECH>")
        return "\n".join(lines)

    def _seat_count(self, party_name: str) -> int:
        return sum(1 for m in self._members
                   if self._party_of.get(m) == party_name)

    # ------------------------------------------------------------------ #
    # Snapshot for the visualization
    # ------------------------------------------------------------------ #

    def _snapshot(self, state: Any) -> Dict[str, Any]:
        members = [
            {
                "id": m,
                "name": self._name_of(state, m),
                "party": self._party_of.get(m, "Independent"),
                "voted": self._votes.get(m, ""),
                "is_speaker": m == self._speaker_id,
            }
            for m in self._members
        ]
        parties = [
            {
                "name": pt["name"],
                "ideology": pt["ideology"],
                "seats": self._seat_count(pt["name"]),
                "stance": pt["stance"],
            }
            for pt in self._parties
        ]
        bill_title = self._bill.split("—")[0].split(".")[0].strip()
        if len(bill_title) > 80:
            bill_title = bill_title[:77] + "…"
        snap: Dict[str, Any] = {
            "bill": self._bill,
            "bill_title": bill_title or "The Bill",
            "phase": self._phase,
            "round": self._round,
            "debate_rounds": self._debate_rounds,
            "voting_threshold": self._voting_threshold,
            "speaker_name": self._name_of(state, self._speaker_id) if self._speaker_id else None,
            "parties": parties,
            "members": members,
            "transcript": [
                {"round": s["round"], "speaker_name": s["speaker_name"],
                 "party": s["party"], "stance": s["stance"], "text": s["text"]}
                for s in self._transcript[-24:]
            ],
        }
        if self._result is not None:
            snap["tally"] = dict(self._result["tally"])
            snap["passed"] = self._result["passed"]
        else:
            snap["tally"] = {
                "yea": sum(1 for v in self._votes.values() if v == "yea"),
                "nay": sum(1 for v in self._votes.values() if v == "nay"),
                "abstain": sum(1 for v in self._votes.values() if v == "abstain"),
            }
            snap["passed"] = None
        return snap

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _name_of(self, state: Any, member_id: Optional[str]) -> str:
        if not member_id:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(member_id)
            if ent is not None:
                return getattr(ent, "name", member_id)
        return member_id

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "members": list(self._members),
            "party_of": dict(self._party_of),
            "seniority_of": dict(self._seniority_of),
            "speaker_id": self._speaker_id,
            "phase": self._phase,
            "round": self._round,
            "speaking_queue": list(self._speaking_queue),
            "queue_pos": self._queue_pos,
            "transcript": list(self._transcript),
            "votes": dict(self._votes),
            "result": dict(self._result) if self._result else None,
            "terminal": self._terminal,
            "initialized": self._initialized,
            "setup_emitted": self._setup_emitted,
            "log": list(self._log),
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "ParliamentModule":
        mod = cls(name=data.get("name", "parliament"), params=data.get("params", {}))
        s = data.get("state", {})
        mod._members = list(s.get("members") or [])
        mod._party_of = dict(s.get("party_of") or {})
        mod._seniority_of = dict(s.get("seniority_of") or {})
        mod._speaker_id = s.get("speaker_id")
        mod._phase = s.get("phase", "first_reading")
        mod._round = int(s.get("round") or 1)
        mod._speaking_queue = list(s.get("speaking_queue") or [])
        mod._queue_pos = int(s.get("queue_pos") or 0)
        mod._transcript = list(s.get("transcript") or [])
        mod._votes = dict(s.get("votes") or {})
        mod._result = dict(s["result"]) if s.get("result") else None
        mod._terminal = bool(s.get("terminal", False))
        mod._initialized = bool(s.get("initialized", False))
        mod._setup_emitted = bool(s.get("setup_emitted", False))
        mod._log = list(s.get("log") or [])
        for pt in mod._parties:
            mod._ideology_of_party[pt["name"]] = pt["ideology"]
            mod._stance_of_party[pt["name"]] = pt["stance"]
        return mod
