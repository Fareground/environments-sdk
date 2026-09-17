"""United Nations domain module — country delegations debate and vote a resolution.

Runs a multilateral diplomatic session on top of the generic action engine:
  - Two bodies: the GENERAL ASSEMBLY (one country, one vote, decided by a
    simple or two-thirds majority) or the SECURITY COUNCIL (fifteen members
    where the five permanent members hold a VETO).
  - Each delegate represents one country; optional extra context and an
    optional instructed position can be supplied per delegation.
  - Phased session: convening -> debate (N rounds) -> voting -> result.

Custom actions:
  - `deliver_statement`: params `stance` ('for'|'against'|'undecided') and
    `text`. Available only to a delegation recognized on the floor.
  - `cast_vote`: param `vote` ('yes'|'no'|'abstain'). In the Security
    Council a 'no' from a permanent member is a veto.

Events emitted (consumed by UNAssemblyViz + UI):
  - `un_setup`     { ...snapshot }
  - `un_phase`     { phase, round, ...snapshot }
  - `un_statement` { speaker, country, stance, text, ...snapshot }
  - `un_vote`      { delegate, country, vote, ...snapshot }
  - `un_result`    { passed, vetoed, veto_by, tally, ...snapshot }  (terminal)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)

BODY_LABEL: Dict[str, str] = {
    "security_council": "United Nations Security Council",
    "general_assembly": "United Nations General Assembly",
}

DEFAULT_COUNTRIES: List[Dict[str, Any]] = [
    {"name": "United States", "context": "", "stance": "free", "permanent": True},
    {"name": "Russia", "context": "", "stance": "free", "permanent": True},
    {"name": "China", "context": "", "stance": "free", "permanent": True},
    {"name": "United Kingdom", "context": "", "stance": "free", "permanent": True},
    {"name": "France", "context": "", "stance": "free", "permanent": True},
]


class UnitedNationsModule(DomainModule):
    """A configurable UN chamber — General Assembly or Security Council."""

    def __init__(self, name: str = "united_nations",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._body: str = str(p.get("body", "security_council"))
        self._voting_threshold: str = str(p.get("voting_threshold", "simple_majority"))
        self._debate_rounds: int = max(1, int(p.get("debate_rounds", 2) or 2))
        self._speakers_per_round: int = max(2, int(p.get("speakers_per_round", 5) or 5))
        self._resolution: str = str(p.get("resolution") or "").strip() or (
            "A draft resolution is before the chamber for debate and a vote."
        )
        countries = p.get("countries")
        if not isinstance(countries, list) or not countries:
            countries = DEFAULT_COUNTRIES
        self._countries: List[Dict[str, Any]] = [
            {
                "name": str(c.get("name") or f"Country {i + 1}"),
                # Optional extra framing. Empty for real countries — the
                # model already knows them.
                "context": str(c.get("context") or c.get("profile") or "").strip(),
                "stance": str(c.get("stance") or "free").lower(),
                "permanent": bool(c.get("permanent", False)),
            }
            for i, c in enumerate(countries)
        ]

        # Runtime state.
        self._members: List[str] = []
        self._country_of: Dict[str, str] = {}
        self._context_of: Dict[str, str] = {}
        self._stance_of: Dict[str, str] = {}
        self._permanent_of: Dict[str, bool] = {}
        self._president_id: Optional[str] = None
        self._phase: str = "convening"
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

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return "Country delegations debate and vote on a UN resolution."

    @property
    def custom_actions(self) -> List[str]:
        return ["deliver_statement", "cast_vote"]

    @property
    def required_properties(self) -> List[str]:
        return ["country"]

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
        for c in self._countries:
            self._context_of[c["name"]] = c["context"]
            self._stance_of[c["name"]] = c["stance"]
        # Assign one country to each delegate (wrap if more delegates than
        # configured countries).
        for i, ent in enumerate(agents):
            c = self._countries[i % len(self._countries)]
            self._country_of[ent.id] = c["name"]
            self._permanent_of[ent.id] = bool(c["permanent"])
            if hasattr(ent, "properties"):
                ent.properties["country"] = c["name"]
                ent.properties["voted"] = ""
                ent.properties["is_permanent"] = (
                    bool(c["permanent"]) and self._body == "security_council"
                )
                ent.properties["is_president"] = False
        # The presidency rotates — seat the first delegation in the chair.
        self._president_id = self._members[0]
        pres = agents[0]
        if hasattr(pres, "properties"):
            pres.properties["is_president"] = True
        self._initialized = True
        logger.info("UN seated: body=%s, %d delegations", self._body, len(self._members))

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
        self._speaking_queue = self._build_queue(1)
        self._queue_pos = 0
        return [
            {
                "type": "un_setup",
                **self._snapshot(state),
                "narrative": (
                    f"The {BODY_LABEL.get(self._body, 'chamber')} is convened. "
                    f"The resolution is tabled — {self._debate_rounds} round(s) "
                    f"of debate will follow, then a vote."
                ),
            },
            {
                "type": "un_phase",
                "phase": "debate",
                "round": 1,
                **self._snapshot(state),
                "narrative": "Debate begins — round 1.",
            },
        ]

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        out = [a for a in valid_actions if a not in self.custom_actions]
        if self._terminal or self._phase in ("convening", "result"):
            return out if entity_id not in self._members else []
        if entity_id not in self._members:
            return out
        if self._phase == "debate":
            if (self._queue_pos < len(self._speaking_queue)
                    and self._speaking_queue[self._queue_pos] == entity_id):
                out.append("deliver_statement")
        elif self._phase == "voting":
            if entity_id not in self._votes:
                out.append("cast_vote")
        return out

    def _build_queue(self, round_num: int) -> List[str]:
        """A rotating window of delegations takes the floor each round."""
        n = len(self._members)
        if n == 0:
            return []
        k = min(self._speakers_per_round, n)
        start = ((round_num - 1) * k) % n
        return [self._members[(start + i) % n] for i in range(k)]

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
        if action_name == "deliver_statement":
            if self._phase != "debate":
                return "The chamber is not in debate"
            if (self._queue_pos >= len(self._speaking_queue)
                    or self._speaking_queue[self._queue_pos] != actor_id):
                return "You do not have the floor"
        if action_name == "cast_vote":
            if self._phase != "voting":
                return "The chamber is not voting"
            if actor_id in self._votes:
                return "Your delegation has already voted"
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions or self._terminal:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        if action_name == "deliver_statement":
            return self._handle_statement(actor_id, params, raw, state)
        if action_name == "cast_vote":
            return self._handle_vote(actor_id, params, raw, state)
        return []

    def _handle_statement(self, actor_id: str, params: Dict[str, Any],
                          raw: Dict[str, Any], state: Any) -> List[Dict[str, Any]]:
        if (self._queue_pos >= len(self._speaking_queue)
                or self._speaking_queue[self._queue_pos] != actor_id):
            return []
        stance = str(params.get("stance", raw.get("stance", "undecided"))).lower().strip()
        if stance not in ("for", "against", "undecided"):
            stance = "undecided"
        text = str(params.get("text", raw.get("text", ""))).strip()
        country = self._country_of.get(actor_id, "?")
        self._transcript.append({
            "round": self._round, "speaker_id": actor_id, "country": country,
            "stance": stance, "text": text,
        })
        stance_word = {"for": "speaks FOR", "against": "speaks AGAINST",
                       "undecided": "addresses the chamber on"}[stance]
        narrative = f"[Round {self._round}] {country} {stance_word} the resolution."
        self._log.append(narrative)
        events: List[Dict[str, Any]] = [{
            "type": "un_statement",
            "speaker": actor_id, "country": country, "stance": stance,
            "round": self._round, "text": text,
            **self._snapshot(state),
            "narrative": narrative,
        }]

        self._queue_pos += 1
        if self._queue_pos >= len(self._speaking_queue):
            if self._round < self._debate_rounds:
                self._round += 1
                self._speaking_queue = self._build_queue(self._round)
                self._queue_pos = 0
                events.append({
                    "type": "un_phase", "phase": "debate", "round": self._round,
                    **self._snapshot(state),
                    "narrative": f"Debate continues — round {self._round}.",
                })
            else:
                self._phase = "voting"
                events.append({
                    "type": "un_phase", "phase": "voting", "round": self._round,
                    **self._snapshot(state),
                    "narrative": ("Debate is closed. The chamber proceeds to "
                                  "the vote — each delegation now votes."),
                })
        return events

    def _handle_vote(self, actor_id: str, params: Dict[str, Any],
                     raw: Dict[str, Any], state: Any) -> List[Dict[str, Any]]:
        if actor_id in self._votes:
            return []
        vote = str(params.get("vote", raw.get("vote", "abstain"))).lower().strip()
        if vote not in ("yes", "no", "abstain"):
            vote = "abstain"
        self._votes[actor_id] = vote
        ent = state.entities.get(actor_id) if hasattr(state, "entities") else None
        if ent is not None and hasattr(ent, "properties"):
            ent.properties["voted"] = vote
        country = self._country_of.get(actor_id, "?")
        is_p = self._permanent_of.get(actor_id, False) and self._body == "security_council"
        veto_note = " — a permanent member's VETO" if (is_p and vote == "no") else ""
        narrative = f"{country} votes {vote.upper()}{veto_note}."
        self._log.append(narrative)
        events: List[Dict[str, Any]] = [{
            "type": "un_vote", "delegate": actor_id, "country": country,
            "vote": vote,
            **self._snapshot(state),
            "narrative": narrative,
        }]
        if len(self._votes) >= len(self._members):
            events.append(self._finish_vote(state))
        return events

    def _votes_needed(self) -> int:
        """Affirmative votes required to carry the resolution."""
        n = len(self._members)
        if self._body == "security_council":
            return max(1, round(n * 9.0 / 15.0))
        if self._voting_threshold == "two_thirds":
            return 0  # GA two-thirds is computed against votes cast, not a fixed count
        return 0

    def _finish_vote(self, state: Any) -> Dict[str, Any]:
        yes = sum(1 for v in self._votes.values() if v == "yes")
        no = sum(1 for v in self._votes.values() if v == "no")
        abstain = sum(1 for v in self._votes.values() if v == "abstain")

        veto_by: List[str] = []
        if self._body == "security_council":
            for mid, v in self._votes.items():
                if v == "no" and self._permanent_of.get(mid, False):
                    veto_by.append(self._country_of.get(mid, "?"))
            needed = self._votes_needed()
            passed = (yes >= needed) and not veto_by
        else:
            base = yes + no
            if self._voting_threshold == "two_thirds":
                passed = base > 0 and (yes / base) >= (2.0 / 3.0)
            else:
                passed = yes > no
            needed = 0

        self._result = {
            "passed": passed,
            "vetoed": bool(veto_by),
            "veto_by": veto_by,
            "tally": {"yes": yes, "no": no, "abstain": abstain},
            "needed": needed,
        }
        self._phase = "result"
        self._terminal = True
        if veto_by:
            verdict = f"is VETOED by {', '.join(veto_by)} and FAILS"
        elif passed:
            verdict = "is ADOPTED"
        else:
            verdict = "FAILS to carry"
        narrative = (
            f"The vote is complete. The resolution {verdict} — "
            f"In favour {yes}, Against {no}, Abstentions {abstain}."
        )
        self._log.append(narrative)
        return {
            "event_type": "un_result", "type": "un_result",
            "passed": passed, "vetoed": bool(veto_by), "veto_by": veto_by,
            "tally": {"yes": yes, "no": no, "abstain": abstain},
            **self._snapshot(state),
            "narrative": narrative,
        }

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in self._members:
            return {"role": "observer"}
        country = self._country_of.get(entity_id, "?")
        is_perm = self._permanent_of.get(entity_id, False) and self._body == "security_council"
        is_president = entity_id == self._president_id
        stance = self._stance_of.get(country, "free")
        my_turn_speak = (
            self._phase == "debate"
            and self._queue_pos < len(self._speaking_queue)
            and self._speaking_queue[self._queue_pos] == entity_id
        )
        my_turn_vote = self._phase == "voting" and entity_id not in self._votes

        instructed = {
            "support": "Your capital has instructed you to SUPPORT the "
                       "resolution (vote yes).",
            "oppose": "Your capital has instructed you to OPPOSE the "
                      "resolution (vote no).",
            "free": "Your capital has given you discretion — vote in the "
                    "national interest.",
        }.get(stance, "Vote in the national interest.")

        if self._body == "security_council":
            rules = (
                f"SECURITY COUNCIL: the resolution needs {self._votes_needed()} "
                f"affirmative votes out of {len(self._members)} AND no veto. "
                "A 'no' vote from any permanent member is a VETO that defeats "
                "the resolution outright; an abstention by a permanent member "
                "is NOT a veto."
            )
        elif self._voting_threshold == "two_thirds":
            rules = ("GENERAL ASSEMBLY: the resolution carries on a two-thirds "
                     "majority of the delegations voting yes or no.")
        else:
            rules = ("GENERAL ASSEMBLY: the resolution carries on a simple "
                     "majority — more yes votes than no.")

        if my_turn_speak:
            instructions = (
                "YOU HAVE THE FLOOR. Deliver your delegation's statement now: "
                "call `deliver_statement` with `stance` ('for', 'against' or "
                "'undecided') and `text`. Speak for your nation's interests "
                "and alignment; engage with prior speakers."
            )
        elif my_turn_vote:
            veto_line = (
                " As a PERMANENT MEMBER, a 'no' vote from you is a veto that "
                "defeats the resolution regardless of the count."
                if is_perm else ""
            )
            instructions = (
                "THE VOTE IS UNDER WAY. Cast your delegation's vote now: call "
                "`cast_vote` with `vote` set to 'yes', 'no' or 'abstain'. "
                "Weigh the debate, your instructions and the national "
                "interest." + veto_line
            )
        elif self._phase == "debate":
            instructions = ("Debate is under way. Listen to the statements — "
                            "your delegation will be recognized or will vote "
                            "in due course.")
        elif self._phase == "voting":
            instructions = "The vote is proceeding. Await the result."
        else:
            instructions = "The session has concluded."

        data: Dict[str, Any] = {
            "instructions": instructions,
            "is_your_turn": my_turn_speak or my_turn_vote,
            "phase": self._phase,
            "debate_round": self._round,
            "total_debate_rounds": self._debate_rounds,
            "body": BODY_LABEL.get(self._body, self._body),
            "resolution": self._resolution,
            "your_country": country,
            "your_instructions": instructed,
            "you_hold_veto": is_perm,
            "your_role": "President of the session" if is_president else "Delegate",
            "voting_rules": rules,
            "chamber": [
                {"country": c["name"],
                 "permanent_member": (c["permanent"] and self._body == "security_council"),
                 **({"context": c["context"]} if c["context"] else {})}
                for c in self._countries
            ],
            "debate_transcript": self._render_transcript(),
            "recent_log": list(self._log[-12:]),
        }
        # Optional extra framing for this delegation, if the user supplied it.
        own_context = self._context_of.get(country, "")
        if own_context:
            data["your_delegation_context"] = own_context
        if self._phase in ("voting", "result"):
            data["vote_so_far"] = {
                "yes": sum(1 for v in self._votes.values() if v == "yes"),
                "no": sum(1 for v in self._votes.values() if v == "no"),
                "abstain": sum(1 for v in self._votes.values() if v == "abstain"),
                "not_yet_voted": len(self._members) - len(self._votes),
            }
        if self._result is not None:
            data["result"] = dict(self._result)
        return data

    def _render_transcript(self) -> str:
        """Multi-line transcript. Each statement is wrapped in
        <UNTRUSTED-SPEECH> — text is authored by other delegations and
        is a prompt-injection vector. See IMMUTABLE RULES in agent
        system prompt."""
        if not self._transcript:
            return "(no statements yet — the debate is just beginning)"
        lines: List[str] = []
        current_round = None
        for sp in self._transcript:
            if sp["round"] != current_round:
                current_round = sp["round"]
                lines.append(f"--- Debate Round {current_round} ---")
            tag = {"for": "FOR", "against": "AGAINST",
                   "undecided": "UNDECIDED"}.get(sp["stance"], "—")
            country = sp.get("country", "?")
            lines.append(f"[{tag}] {country}:")
            lines.append(
                f'  <UNTRUSTED-SPEECH from="{country}" round="{sp["round"]}">'
            )
            lines.append(f"  {sp['text']}")
            lines.append("  </UNTRUSTED-SPEECH>")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Snapshot for the visualization
    # ------------------------------------------------------------------ #

    def _snapshot(self, state: Any) -> Dict[str, Any]:
        members = [
            {
                "id": m,
                "name": self._name_of(state, m),
                "country": self._country_of.get(m, "?"),
                "voted": self._votes.get(m, ""),
                "is_president": m == self._president_id,
                "permanent": (self._permanent_of.get(m, False)
                              and self._body == "security_council"),
            }
            for m in self._members
        ]
        countries = [
            {"name": c["name"], "context": c["context"], "stance": c["stance"],
             "permanent": c["permanent"] and self._body == "security_council"}
            for c in self._countries
        ]
        title = self._resolution.split("—")[0].split(".")[0].strip()
        if len(title) > 80:
            title = title[:77] + "…"
        snap: Dict[str, Any] = {
            "body": self._body,
            "resolution": self._resolution,
            "resolution_title": title or "Draft Resolution",
            "phase": self._phase,
            "round": self._round,
            "debate_rounds": self._debate_rounds,
            "votes_needed": self._votes_needed(),
            "speaker_name": self._name_of(state, self._president_id) if self._president_id else None,
            "countries": countries,
            "members": members,
            "transcript": [
                {"round": s["round"], "country": s["country"],
                 "stance": s["stance"], "text": s["text"]}
                for s in self._transcript[-24:]
            ],
        }
        if self._result is not None:
            snap["tally"] = dict(self._result["tally"])
            snap["passed"] = self._result["passed"]
            snap["vetoed"] = self._result["vetoed"]
            snap["veto_by"] = list(self._result["veto_by"])
        else:
            snap["tally"] = {
                "yes": sum(1 for v in self._votes.values() if v == "yes"),
                "no": sum(1 for v in self._votes.values() if v == "no"),
                "abstain": sum(1 for v in self._votes.values() if v == "abstain"),
            }
            snap["passed"] = None
            snap["vetoed"] = False
            snap["veto_by"] = []
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
            "country_of": dict(self._country_of),
            "permanent_of": dict(self._permanent_of),
            "president_id": self._president_id,
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
    def from_dict(cls, data: dict) -> "UnitedNationsModule":
        mod = cls(name=data.get("name", "united_nations"), params=data.get("params", {}))
        s = data.get("state", {})
        mod._members = list(s.get("members") or [])
        mod._country_of = dict(s.get("country_of") or {})
        mod._permanent_of = dict(s.get("permanent_of") or {})
        mod._president_id = s.get("president_id")
        mod._phase = s.get("phase", "convening")
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
        for c in mod._countries:
            mod._context_of[c["name"]] = c["context"]
            mod._stance_of[c["name"]] = c["stance"]
        return mod
