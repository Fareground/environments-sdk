"""Dispute trial domain module — courtroom proceeding with attorneys,
judge, and the configured jury.

Lifecycle (single-pass through the phases):

  opening_statements   each attorney delivers an opening
  direct_examination   each attorney presents their case
                       (evidence + arguments). Configurable rounds.
  cross_examination    each attorney rebuts the other's case
                       (configurable rounds)
  closing_statements   each attorney delivers a closing
  jury_deliberation    every juror deliberates aloud (one turn each)
  jury_vote            every juror casts a closed-ballot vote
  verdict              tally → judge announces. World property
                       `verdict_recorded` flips to 1; the template's
                       termination condition fires and the simulation
                       ends.

Custom actions:
  - deliver_statement(represented_side=…, position=…, text="…")
                                   attorneys, statement-shaped phases
  - object(grounds=…, explanation=…) attorneys (rare — for flavor;
                                                 deterministic ruling)
  - deliberate(text="…")           jurors, deliberation phase
  - cast_vote(winner="plaintiff"|"defendant", reasoning="…")
                                   jurors, vote phase

Events emitted (consumed by the FE viz + verdict path):
  dispute_phase_start    phase transition narration
  dispute_statement      attorney spoke this turn
  dispute_objection      attorney raised an objection (auto-ruled)
  dispute_deliberation   juror shared their view
  dispute_vote           juror cast a vote (only the actor sees details
                         until the verdict event)
  dispute_verdict        final tally + winner. The dispute service
                         reads this and writes the verdict back to the
                         Dispute row.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


PHASES = [
    "opening_statements",
    "direct_examination",
    "cross_examination",
    "closing_statements",
    "jury_deliberation",
    "jury_vote",
    "verdict",
]

BALLOT_OUTCOMES = ("plaintiff", "defendant", "mixed", "none")
DISPOSITION_TYPES = (
    "party_win",
    "partial_relief",
    "conditional_order",
    "settlement_or_consent",
    "no_merits",
    "appellate_relief",
)


class DisputeTrialModule(DomainModule):
    """End-to-end courtroom proceeding."""

    def __init__(self, name: str = "dispute_trial",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        # How many turns each attorney gets in the back-and-forth
        # examination phases. 2 means each side delivers two segments
        # of direct (and two of cross). Smaller = faster trial, larger
        # = deeper.
        self._exam_rounds: int = max(1, int(p.get("exam_rounds") or 2))

        # Case context — sourced from the domain-module `config` block
        # (prepare_trial_world / runtime params can supply these). All
        # optional; perception degrades gracefully when absent.
        self._complaint: str = str(p.get("complaint") or "").strip()
        self._case_summary: str = str(
            p.get("case_summary") or p.get("premise") or ""
        ).strip()
        self._plaintiff_name: str = str(p.get("plaintiff_name") or "").strip()
        self._defendant_name: str = str(p.get("defendant_name") or "").strip()
        self._forum: str = str(p.get("forum") or "").strip()
        self._requested_remedies: Dict[str, str] = {
            "plaintiff": str(p.get("plaintiff_requested_remedy") or "").strip(),
            "defendant": str(p.get("defendant_requested_remedy") or "").strip(),
        }

        # Identified at first tick.
        self._attorneys: Dict[str, str] = {}  # entity_id → 'plaintiff' | 'defendant'
        self._attorney_evidence_ids: Dict[str, set] = {}
        self._judge_id: Optional[str] = None
        self._jurors: List[str] = []
        self._initialized = False

        # State machine. We walk through PHASES in order; the per-
        # phase round counter resets on transition.
        self._phase: str = PHASES[0]
        self._phase_idx: int = 0
        self._round_in_phase: int = 1
        self._spoken_this_phase: set = set()
        self._deliberated: set = set()
        self._votes: Dict[str, Dict[str, Any]] = {}  # juror_id → structured ballot
        self._verdict_emitted = False

        # Ordered record of everything said on the floor, for perception.
        # Each entry: {phase, round, kind, side, speaker, speaker_name,
        #              text, grounds?, ruling?}. `kind` is one of
        # 'statement', 'objection', 'deliberation'.
        self._transcript: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Module metadata
    # ------------------------------------------------------------------ #

    @property
    def description(self) -> str:
        return (
            "Formal two-party courtroom trial: plaintiff vs defendant "
            "with AI attorneys, judge, and majority-rule jury."
        )

    @property
    def custom_actions(self) -> List[str]:
        return ["deliver_statement", "object", "deliberate", "cast_vote"]

    @property
    def required_properties(self) -> List[str]:
        return []

    # ------------------------------------------------------------------ #
    # Seat assignment — run once on first tick
    # ------------------------------------------------------------------ #

    def _seed(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        for ent in agents:
            etype = getattr(ent, "entity_type", "") or ""
            if etype == "Attorney":
                props = ent.properties or {}
                side = str(props.get("side") or "").strip().lower()
                if side not in ("plaintiff", "defendant"):
                    raise RuntimeError(
                        f"Attorney {ent.id} has no valid immutable side binding."
                    )
                if side in self._attorneys.values():
                    raise RuntimeError(
                        f"Dispute trial has more than one attorney bound to {side}."
                    )
                required = ("client_name", "opposing_party_name", "requested_remedy")
                if any(not str(props.get(key) or "").strip() for key in required):
                    raise RuntimeError(
                        f"Attorney {ent.id} has an incomplete representation contract."
                    )
                self._attorneys[ent.id] = side
                evidence_ids = props.get("evidence_ids") or []
                if isinstance(evidence_ids, str):
                    try:
                        evidence_ids = json.loads(evidence_ids)
                    except (TypeError, ValueError):
                        evidence_ids = []
                self._attorney_evidence_ids[ent.id] = {
                    str(item) for item in evidence_ids if str(item).strip()
                }
                self._requested_remedies[side] = str(props["requested_remedy"]).strip()
                if side == "plaintiff" and not self._plaintiff_name:
                    self._plaintiff_name = str(props["client_name"]).strip()
                elif side == "defendant" and not self._defendant_name:
                    self._defendant_name = str(props["client_name"]).strip()
            elif etype == "Judge":
                self._judge_id = ent.id
            elif etype == "Juror":
                self._jurors.append(ent.id)

        if not self._attorneys or len(self._attorneys) < 2 or not self._jurors:
            logger.warning(
                "dispute_trial: bad population — attorneys=%s jurors=%d. "
                "Module will idle.",
                self._attorneys, len(self._jurors),
            )
            return
        # Every configured seat participates. Split panels produce an
        # explicit inconclusive disposition instead of dropping a juror.
        self._initialized = True
        logger.info(
            "dispute_trial: seated attorneys=%s judge=%s jurors=%d phase=%s",
            self._attorneys, self._judge_id, len(self._jurors), self._phase,
        )

    # ------------------------------------------------------------------ #
    # Tick — phase narration on transitions
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed(state)
        # Phase narration is emitted lazily via _advance_phase; nothing
        # else for us to do on a bare tick.
        return []

    # ------------------------------------------------------------------ #
    # Action filtering — gate by phase + role
    # ------------------------------------------------------------------ #

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                              state: Any) -> List[str]:
        if not self._initialized or self._phase == "verdict" or self._verdict_emitted:
            return [a for a in valid_actions if a not in self.custom_actions]
        out = [a for a in valid_actions if a not in self.custom_actions]
        phase = self._phase
        if entity_id in self._attorneys:
            # Attorneys speak in any of the four statement phases, once
            # per per-side turn (we use a per-phase "spoken" set; on
            # phase advance the set clears).
            if phase in ("opening_statements", "direct_examination",
                         "cross_examination", "closing_statements") \
               and entity_id not in self._spoken_this_phase:
                out.append("deliver_statement")
            # Objections are flavor — available during examination phases.
            if phase in ("direct_examination", "cross_examination"):
                out.append("object")
            return out
        if entity_id in self._jurors:
            if phase == "jury_deliberation" and entity_id not in self._deliberated:
                out.append("deliberate")
            if phase == "jury_vote" and entity_id not in self._votes:
                out.append("cast_vote")
            return out
        # Judge has no agent action — phase transitions narrate via
        # automatic events from the module itself.
        return out

    # ------------------------------------------------------------------ #
    # Action validation
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        actor_id = getattr(actor, "id", None)
        if action_name == "deliver_statement":
            if actor_id not in self._attorneys:
                return "Only attorneys can deliver_statement."
            if self._phase not in ("opening_statements", "direct_examination",
                                    "cross_examination", "closing_statements"):
                return f"deliver_statement not allowed in phase {self._phase}."
            if actor_id in self._spoken_this_phase:
                return "You already had your turn this phase — wait for the other side."
        elif action_name == "object":
            if actor_id not in self._attorneys:
                return "Only attorneys can object."
            if self._phase not in ("direct_examination", "cross_examination"):
                return f"Objections only allowed during examination (current phase: {self._phase})."
        elif action_name == "deliberate":
            if actor_id not in self._jurors:
                return "Only jurors can deliberate."
            if self._phase != "jury_deliberation":
                return f"Deliberation phase isn't active (current: {self._phase})."
            if actor_id in self._deliberated:
                return "You've already shared your view this deliberation."
        elif action_name == "cast_vote":
            if actor_id not in self._jurors:
                return "Only jurors can cast_vote."
            if self._phase != "jury_vote":
                return f"Voting phase isn't active (current: {self._phase})."
            if actor_id in self._votes:
                return "You've already voted."
        return None

    # ------------------------------------------------------------------ #
    # Post-resolution — drive the phase machine
    # ------------------------------------------------------------------ #

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                         result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        details = {**raw, **params}

        if action_name in ("deliver_statement", "object"):
            self._assert_advocate_binding(actor_id, details)

        if action_name == "deliver_statement":
            return self._on_statement(actor_id, details, state)
        if action_name == "object":
            return self._on_objection(actor_id, details, state)
        if action_name == "deliberate":
            return self._on_deliberation(actor_id, details, state)
        if action_name == "cast_vote":
            return self._on_vote(actor_id, details, state)
        return []

    def _assert_advocate_binding(
        self, actor_id: str, details: Dict[str, Any],
    ) -> None:
        """Fail closed when an advocate declares or pursues another side."""
        expected_side = self._attorneys.get(actor_id)
        declared_side = str(details.get("represented_side") or "").strip().lower()
        position = str(details.get("position") or "").strip().lower()
        if declared_side != expected_side:
            raise RuntimeError(
                f"Advocate side inversion: {actor_id} is bound to {expected_side!r} "
                f"but declared {declared_side or '(missing)'!r}."
            )
        if position != "advance_client_requested_remedy":
            raise RuntimeError(
                f"Advocate remedy inversion: {actor_id} did not affirm its "
                "client's requested remedy."
            )

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    def _on_statement(self, actor_id: str, details: Dict[str, Any],
                       state: Any) -> List[Dict[str, Any]]:
        text = str(details.get("text") or "").strip()
        side = self._attorneys.get(actor_id, "?")
        speaker = self._name_of(state, actor_id)
        claims, claim_flags = self._audit_claims(
            actor_id, details.get("claims")
        )
        events: List[Dict[str, Any]] = [{
            "type": "dispute_statement",
            "phase": self._phase,
            "speaker": actor_id,
            "speaker_name": speaker,
            "side": side,
            "text": text,
            "claims": claims,
            "claim_flags": claim_flags,
            "narrative": f"[{self._phase}] {speaker} ({side}): \"{text[:200]}\"",
        }]
        self._spoken_this_phase.add(actor_id)
        self._transcript.append({
            "phase": self._phase,
            "round": self._round_in_phase,
            "kind": "statement",
            "side": side,
            "speaker": actor_id,
            "speaker_name": speaker,
            "text": text,
            "claims": claims,
            "claim_flags": claim_flags,
        })

        # Phase advance when BOTH attorneys have spoken in this phase.
        # For the examination phases we loop `exam_rounds` times before
        # moving on.
        if len(self._spoken_this_phase) >= len(self._attorneys):
            if self._phase in ("direct_examination", "cross_examination") \
               and self._round_in_phase < self._exam_rounds:
                # Next round inside the same phase.
                self._round_in_phase += 1
                self._spoken_this_phase = set()
                events.append({
                    "type": "dispute_phase_round",
                    "phase": self._phase,
                    "round": self._round_in_phase,
                    "narrative": (
                        f"Round {self._round_in_phase} of {self._exam_rounds} "
                        f"in {self._phase.replace('_', ' ')}."
                    ),
                })
            else:
                events.extend(self._advance_phase(state))
        return events

    def _audit_claims(
        self, actor_id: str, raw_claims: Any,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        if isinstance(raw_claims, str):
            try:
                raw_claims = json.loads(raw_claims)
            except (TypeError, ValueError):
                raw_claims = []
        if not isinstance(raw_claims, list) or not raw_claims:
            return [], [{
                "code": "missing_claim_structure",
                "message": "Statement supplied no structured claim audit.",
            }]

        allowed_ids = self._attorney_evidence_ids.get(actor_id, set())
        claims: List[Dict[str, Any]] = []
        flags: List[Dict[str, str]] = []
        for index, item in enumerate(raw_claims, 1):
            if not isinstance(item, dict):
                flags.append({
                    "code": "malformed_claim",
                    "message": f"Claim {index} is not an object.",
                })
                continue
            text = str(item.get("text") or "").strip()
            kind = str(item.get("kind") or "").strip().lower()
            exhibit_ids = item.get("exhibit_ids") or []
            if isinstance(exhibit_ids, str):
                exhibit_ids = [exhibit_ids]
            exhibit_ids = [str(value) for value in exhibit_ids]
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = None
            claim = {
                "text": text,
                "kind": kind,
                "exhibit_ids": exhibit_ids,
                "confidence": confidence,
            }
            claims.append(claim)
            if not text or kind not in ("fact", "inference", "uncertainty"):
                flags.append({
                    "code": "malformed_claim",
                    "message": f"Claim {index} needs text and a valid kind.",
                })
            unknown = sorted(set(exhibit_ids) - allowed_ids)
            if unknown:
                flags.append({
                    "code": "unknown_exhibit",
                    "message": f"Claim {index} cites unavailable exhibit(s): {', '.join(unknown)}.",
                })
            if kind in ("fact", "inference") and not exhibit_ids:
                flags.append({
                    "code": "missing_provenance",
                    "message": f"Claim {index} has no exhibit citation.",
                })
            if kind in ("inference", "uncertainty") and (
                confidence is None or not 0.0 <= confidence <= 1.0
            ):
                flags.append({
                    "code": "missing_confidence",
                    "message": f"Claim {index} needs confidence from 0 to 1.",
                })
        return claims, flags

    def _on_objection(self, actor_id: str, details: Dict[str, Any],
                       state: Any) -> List[Dict[str, Any]]:
        grounds = str(details.get("grounds") or "").strip() or "objection"
        explanation = str(details.get("explanation") or "").strip()
        speaker = self._name_of(state, actor_id)
        # Trivial deterministic ruling: 'hearsay' and 'speculation' get
        # sustained, everything else overruled. Flavor only — doesn't
        # block the next turn.
        ruling = "sustained" if grounds.lower() in {"hearsay", "speculation"} else "overruled"
        self._transcript.append({
            "phase": self._phase,
            "round": self._round_in_phase,
            "kind": "objection",
            "side": self._attorneys.get(actor_id, "?"),
            "speaker": actor_id,
            "speaker_name": speaker,
            "text": explanation,
            "grounds": grounds,
            "ruling": ruling,
        })
        return [{
            "type": "dispute_objection",
            "speaker": actor_id,
            "speaker_name": speaker,
            "grounds": grounds,
            "explanation": explanation,
            "ruling": ruling,
            "narrative": (
                f"{speaker} objects ({grounds}). The judge rules {ruling}."
            ),
        }]

    def _on_deliberation(self, actor_id: str, details: Dict[str, Any],
                          state: Any) -> List[Dict[str, Any]]:
        text = str(details.get("text") or "").strip()
        juror = self._name_of(state, actor_id)
        self._deliberated.add(actor_id)
        self._transcript.append({
            "phase": self._phase,
            "round": self._round_in_phase,
            "kind": "deliberation",
            "side": None,
            "speaker": actor_id,
            "speaker_name": juror,
            "text": text,
        })
        events: List[Dict[str, Any]] = [{
            "type": "dispute_deliberation",
            "juror": actor_id,
            "juror_name": juror,
            "text": text,
            "narrative": f"{juror}: \"{text[:200]}\"",
        }]
        if len(self._deliberated) >= len(self._jurors):
            events.extend(self._advance_phase(state))
        return events

    def _on_vote(self, actor_id: str, details: Dict[str, Any],
                  state: Any) -> List[Dict[str, Any]]:
        choice = str(details.get("winner") or "").strip().lower()
        disposition_type = str(
            details.get("disposition_type") or "party_win"
        ).strip().lower()
        reasoning = str(details.get("reasoning") or "").strip()
        remedy = str(details.get("remedy") or "").strip()
        issue_findings = self._normalize_issue_findings(details.get("issue_findings"))

        invalid_reason = ""
        if choice not in BALLOT_OUTCOMES:
            invalid_reason = "bad_winner"
        elif disposition_type not in DISPOSITION_TYPES:
            invalid_reason = "bad_disposition_type"
        elif choice in ("plaintiff", "defendant") and disposition_type in (
            "settlement_or_consent", "no_merits"
        ):
            invalid_reason = "winner_contradicts_disposition"
        elif choice == "mixed" and disposition_type not in (
            "partial_relief", "conditional_order", "appellate_relief"
        ):
            invalid_reason = "mixed_requires_nonbinary_disposition"
        elif choice == "none" and disposition_type not in (
            "settlement_or_consent", "no_merits"
        ):
            invalid_reason = "no_winner_requires_nonmerits_disposition"
        elif choice in ("mixed", "none") and not issue_findings:
            invalid_reason = "nonbinary_requires_issue_findings"

        if invalid_reason:
            juror = self._name_of(state, actor_id)
            return [{
                "type": "dispute_invalid",
                "juror": actor_id,
                "juror_name": juror,
                "reason": invalid_reason,
                "attempted": details.get("winner"),
                "narrative": (
                    f"{juror} submitted an invalid disposition ballot "
                    f"({invalid_reason})."
                ),
            }]
        ballot = {
            "winner": choice,
            "disposition_type": disposition_type,
            "issue_findings": issue_findings,
            "remedy": remedy,
            "reasoning": reasoning,
        }
        self._votes[actor_id] = ballot
        # Mirror onto the juror entity.
        if hasattr(state, "entities"):
            ent = state.entities.get(actor_id)
            if ent is not None and hasattr(ent, "properties"):
                ent.properties["voted_for"] = choice

        juror = self._name_of(state, actor_id)
        events: List[Dict[str, Any]] = [{
            "type": "dispute_vote",
            "juror": actor_id,
            "juror_name": juror,
            "voted_for": choice,
            "disposition_type": disposition_type,
            "issue_findings": issue_findings,
            "remedy": remedy,
            # Reasoning is logged but visually private until the verdict
            # event publishes the tally. Frontend renders this only in
            # the post-trial transcript.
            "reasoning": reasoning,
            "narrative": f"{juror} cast a vote. (Closed ballot)",
        }]
        if len(self._votes) >= len(self._jurors):
            events.extend(self._advance_phase(state))
        return events

    @staticmethod
    def _normalize_issue_findings(value: Any) -> List[Dict[str, str]]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return []
        if not isinstance(value, list):
            return []
        findings: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue") or "").strip()
            finding = str(item.get("finding") or "").strip()
            if issue and finding:
                findings.append({"issue": issue, "finding": finding})
        return findings

    # ------------------------------------------------------------------ #
    # Phase advancement
    # ------------------------------------------------------------------ #

    def _advance_phase(self, state: Any) -> List[Dict[str, Any]]:
        prev = self._phase
        if self._phase_idx >= len(PHASES) - 1:
            # Already at verdict — emit the verdict event.
            return self._emit_verdict(state)
        self._phase_idx += 1
        self._phase = PHASES[self._phase_idx]
        self._round_in_phase = 1
        self._spoken_this_phase = set()
        events: List[Dict[str, Any]] = [{
            "type": "dispute_phase_start",
            "phase": self._phase,
            "previous": prev,
            "narrative": _phase_narration(self._phase),
        }]
        # If we just entered the verdict phase, emit the tally + flip
        # the termination flag right away — there's no agent action to
        # take in verdict, the judge just reads the result.
        if self._phase == "verdict":
            events.extend(self._emit_verdict(state))
        return events

    def _emit_verdict(self, state: Any) -> List[Dict[str, Any]]:
        if self._verdict_emitted:
            return []
        tally = {outcome: 0 for outcome in BALLOT_OUTCOMES}
        for ballot in self._votes.values():
            tally[ballot["winner"]] += 1
        top_outcome, top_count = max(tally.items(), key=lambda item: item[1])
        has_majority = top_count > len(self._votes) / 2
        panel_outcome = top_outcome if has_majority else "inconclusive"
        winner = (
            panel_outcome
            if panel_outcome in ("plaintiff", "defendant")
            else None
        )
        majority_ballots = (
            [
                ballot for ballot in self._votes.values()
                if ballot["winner"] == top_outcome
            ]
            if has_majority else []
        )
        disposition_counts: Dict[str, int] = {}
        for ballot in majority_ballots:
            dtype = ballot["disposition_type"]
            disposition_counts[dtype] = disposition_counts.get(dtype, 0) + 1
        disposition_type = (
            max(disposition_counts.items(), key=lambda item: item[1])[0]
            if disposition_counts else "inconclusive"
        )
        finding_counts: Dict[tuple, int] = {}
        for ballot in self._votes.values():
            if has_majority and ballot["winner"] != top_outcome:
                continue
            for finding in ballot["issue_findings"]:
                key = (finding["issue"], finding["finding"])
                finding_counts[key] = finding_counts.get(key, 0) + 1
        issue_findings = [
            {"issue": issue, "finding": finding, "votes": votes}
            for (issue, finding), votes in finding_counts.items()
        ]
        remedies = list(dict.fromkeys(
            ballot["remedy"]
            for ballot in majority_ballots
            if ballot["remedy"]
        ))
        # Flip the world-property the template watches for termination.
        if hasattr(state, "world_state"):
            ws = getattr(state, "world_state", None)
        else:
            ws = None
        try:
            # Most kernel WorldState exposes a dict-ish properties bag.
            props = getattr(state, "properties", None)
            if props is not None:
                props["verdict_recorded"] = 1
            else:
                setattr(state, "verdict_recorded", 1)
        except Exception:
            pass
        self._verdict_emitted = True
        return [{
            "type": "dispute_verdict",
            "winner": winner,
            "panel_outcome": panel_outcome,
            "disposition_type": disposition_type,
            "winner_name": (
                self._plaintiff_name if winner == "plaintiff"
                else self._defendant_name if winner == "defendant" else ""
            ),
            "parties": {
                "plaintiff": self._plaintiff_name,
                "defendant": self._defendant_name,
            },
            "tally": tally,
            "issue_findings": issue_findings,
            "remedies": remedies,
            "narrative": self._verdict_sentence(panel_outcome, tally),
        }]

    def _verdict_sentence(self, panel_outcome: str, tally: Dict[str, int]) -> str:
        """The verdict as the court would announce it — no internal labels."""
        jurors = sum(tally.values())
        if panel_outcome in ("plaintiff", "defendant"):
            name = self._plaintiff_name if panel_outcome == "plaintiff" else self._defendant_name
            return (f"The jury finds for {name} ({panel_outcome}), "
                    f"{tally[panel_outcome]} of {jurors} jurors.")
        if panel_outcome == "mixed":
            return (f"The jury returns a split or partial result, "
                    f"{tally['mixed']} of {jurors} jurors.")
        if panel_outcome == "none":
            return (f"The jury finds neither party entitled to a verdict, "
                    f"{tally['none']} of {jurors} jurors.")
        return f"The jury could not reach a majority among {jurors} jurors."

    # ------------------------------------------------------------------ #
    # Perception — give agents context on the trial state
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if not self._initialized:
            return {}

        role = (
            "attorney" if entity_id in self._attorneys else
            "judge" if entity_id == self._judge_id else
            "juror" if entity_id in self._jurors else
            "spectator"
        )
        my_side = self._attorneys.get(entity_id)

        # --- Existing keys (preserved verbatim) --------------------- #
        perception: Dict[str, Any] = {
            "trial_phase": self._phase,
            "round_in_phase": self._round_in_phase,
            "exam_rounds": self._exam_rounds,
            "my_role": role,
            "my_side": my_side,
            "my_client": self._party_label(my_side) if my_side else None,
            "my_requested_remedy": self._requested_remedies.get(my_side or ""),
        }

        # Non-LLM simulation strategies cannot author semantic parameters,
        # but they still need to exercise this asset without bypassing the
        # immutable representation contract. Advertise safe, actor-specific
        # presets that a generic baseline runner may attach explicitly.
        if role == "attorney" and my_side:
            representation = {
                "represented_side": my_side,
                "position": "advance_client_requested_remedy",
            }
            perception["baseline_action_parameters"] = {
                "deliver_statement": dict(representation),
                "object": dict(representation),
            }

        # --- New: case context ------------------------------------- #
        perception["case_context"] = self._render_case_context(state)

        # --- New: trial transcript so far -------------------------- #
        # Attorneys + the judge see only the on-the-record courtroom
        # statements/objections (they are not in the jury room).
        # Jurors additionally see the jury-room deliberation. Closed
        # ballots are never surfaced here for anyone.
        include_deliberation = (role == "juror")
        perception["transcript"] = self._render_transcript(
            include_deliberation=include_deliberation
        )

        # --- New: phase / turn status ------------------------------ #
        perception["phase_status"] = self._render_phase_status(entity_id, role)

        # --- New: role- and phase-specific instructions ------------ #
        perception["instructions"] = self._render_instructions(entity_id, role)

        return perception

    # ------------------------------------------------------------------ #
    # Perception render helpers
    # ------------------------------------------------------------------ #

    def _party_label(self, side: str) -> str:
        """Human-facing name for a side, falling back to the role word."""
        if side == "plaintiff":
            return (f"the plaintiff ({self._plaintiff_name})"
                    if self._plaintiff_name else "the plaintiff")
        if side == "defendant":
            return (f"the defendant ({self._defendant_name})"
                    if self._defendant_name else "the defendant")
        return side or "?"

    def _render_case_context(self, state: Any) -> str:
        """Multi-line string: what this trial is about.

        Returns a real multi-line string so the prompt builder renders
        it as readable prose rather than compact JSON.
        """
        lines: List[str] = ["=== THE CASE ==="]
        if self._plaintiff_name or self._defendant_name:
            lines.append(
                f"Plaintiff: {self._plaintiff_name or '(unnamed)'}   "
                f"vs.   Defendant: {self._defendant_name or '(unnamed)'}"
            )
        if self._case_summary:
            lines.append("")
            lines.append("Summary:")
            lines.append(self._case_summary)
        if self._complaint:
            lines.append("")
            lines.append("Complaint (filed by the plaintiff):")
            lines.append(self._complaint)
        if not self._case_summary and not self._complaint:
            lines.append(
                "(No case brief was supplied to the trial module. The "
                "complaint and each side's evidence are in your persona "
                "briefing — argue from those facts of record.)"
            )
        return "\n".join(lines)

    def _render_transcript(self, *, include_deliberation: bool) -> str:
        """Multi-line string: the ordered record of the trial so far.
        Each authored text is wrapped in <UNTRUSTED-SPEECH> — speech is
        authored by other parties (attorneys, witnesses, jurors) and is
        a prompt-injection vector. See IMMUTABLE RULES in agent system
        prompt."""
        if not self._transcript:
            return (
                "=== TRIAL TRANSCRIPT ===\n"
                "(Nothing has been said yet — the trial is just beginning.)"
            )
        lines: List[str] = ["=== TRIAL TRANSCRIPT (in order) ==="]
        last_phase: Optional[str] = None
        for entry in self._transcript:
            kind = entry.get("kind")
            if kind == "deliberation" and not include_deliberation:
                continue
            phase = entry.get("phase")
            if phase != last_phase:
                lines.append("")
                lines.append(f"--- {str(phase).replace('_', ' ').upper()} ---")
                last_phase = phase
            speaker = entry.get("speaker_name") or entry.get("speaker") or "?"
            side = entry.get("side") or ""
            tag = f" [{side}]" if side else ""
            text = (entry.get("text") or "").strip()
            speech_open = f'<UNTRUSTED-SPEECH from="{speaker}" side="{side}" kind="{kind or "statement"}">'
            speech_close = "</UNTRUSTED-SPEECH>"
            if kind == "objection":
                grounds = entry.get("grounds") or "objection"
                ruling = entry.get("ruling") or "ruled"
                lines.append(
                    f"OBJECTION by {speaker}{tag} — grounds: {grounds} "
                    f"→ {ruling.upper()}."
                )
                if text:
                    lines.append(f"  {speech_open}")
                    lines.append(f"  {text}")
                    lines.append(f"  {speech_close}")
            elif kind == "deliberation":
                lines.append(f"{speaker} (juror):")
                lines.append(f"  {speech_open}")
                lines.append(f"  {text}")
                lines.append(f"  {speech_close}")
            else:  # statement
                lines.append(f"{speaker}{tag}:")
                lines.append(speech_open)
                lines.append(text)
                lines.append(speech_close)
                flags = entry.get("claim_flags") or []
                if flags:
                    lines.append("CLAIM AUDIT FLAGS (weigh before relying):")
                    for flag in flags:
                        lines.append(
                            f"  - {flag.get('code')}: {flag.get('message')}"
                        )
        return "\n".join(lines)

    def _render_phase_status(self, entity_id: str, role: str) -> str:
        """Multi-line string: where the trial is and whose move it is."""
        phase_human = self._phase.replace("_", " ")
        lines: List[str] = [
            f"Current phase: {phase_human} "
            f"(phase {self._phase_idx + 1} of {len(PHASES)}).",
        ]
        if self._phase in ("direct_examination", "cross_examination"):
            lines.append(
                f"Examination round {self._round_in_phase} of {self._exam_rounds}."
            )
        # Whose turn is it.
        if role == "attorney":
            if self._phase in ("opening_statements", "direct_examination",
                               "cross_examination", "closing_statements"):
                if entity_id in self._spoken_this_phase:
                    waiting = [
                        self._party_label(s) for a, s in self._attorneys.items()
                        if a not in self._spoken_this_phase
                    ]
                    lines.append(
                        "You have already spoken this round. Waiting on: "
                        + (", ".join(waiting) if waiting else "phase to advance")
                        + "."
                    )
                else:
                    lines.append("IT IS YOUR TURN to speak now.")
            else:
                lines.append("Attorneys do not act in this phase — the jury does.")
        elif role == "juror":
            if self._phase == "jury_deliberation":
                lines.append(
                    "IT IS YOUR TURN to deliberate."
                    if entity_id not in self._deliberated
                    else "You have deliberated; waiting on the other jurors."
                )
            elif self._phase == "jury_vote":
                lines.append(
                    "IT IS YOUR TURN to cast your closed ballot."
                    if entity_id not in self._votes
                    else "Your ballot is cast; waiting on the other jurors."
                )
            else:
                lines.append(
                    "Watch and weigh the arguments — jurors act only in "
                    "deliberation and voting."
                )
        elif role == "judge":
            lines.append(
                "Preside: narrate transitions and rule on objections. "
                "You do not argue the case or vote."
            )
        return "\n".join(lines)

    def _render_instructions(self, entity_id: str, role: str) -> str:
        """Multi-line string: tailored guidance for this turn."""
        if role == "attorney":
            return self._attorney_instructions(entity_id)
        if role == "juror":
            return self._juror_instructions(entity_id)
        if role == "judge":
            return (
                "You are the presiding JUDGE. Keep the proceeding orderly "
                "and neutral. Narrate phase transitions plainly, rule on "
                "objections (sustain hearsay/speculation, otherwise "
                "overrule), and read the jury's verdict when it arrives. "
                "Never argue either side's case yourself."
            )
        return "You are observing the proceeding."

    def _attorney_instructions(self, entity_id: str) -> str:
        side = self._attorneys.get(entity_id, "?")
        you = self._party_label(side)
        opp_side = "defendant" if side == "plaintiff" else "plaintiff"
        opp = self._party_label(opp_side)
        phase = self._phase
        head = (
            "IMMUTABLE REPRESENTATION CONTRACT — "
            f"you are counsel for {you}. You remain on the {side} side for "
            "the entire trial. Your goal is to persuade the jury to find "
            f"for your client and grant this requested remedy: "
            f"{self._requested_remedies.get(side) or '(not specified)'}. "
            f"Never advocate for {opp}."
        )
        guide = {
            "opening_statements": (
                "OPENING STATEMENT. Lay out your theory of the case in a "
                "clear narrative. Tell the jury what you will prove and "
                "why the facts favor your client. Do not argue evidence "
                "in detail yet — frame the story. 3-8 paragraphs."
            ),
            "direct_examination": (
                "DIRECT EXAMINATION. Present your case affirmatively: walk "
                "the jury through your evidence and arguments, citing "
                "specific case-folder entries by caption. Build the record "
                "your closing will rely on. 3-8 paragraphs."
            ),
            "cross_examination": (
                f"CROSS-EXAMINATION. Rebut {opp}'s case. Read the "
                "transcript above and attack the specific weak points, "
                "gaps, and unproven assertions in what they said. Engage "
                "their strongest argument directly — do not dodge it. "
                "Objections (grounds: hearsay, speculation, relevance, "
                "lack of evidence) are available this phase."
            ),
            "closing_statements": (
                "CLOSING ARGUMENT. This is your last word. Tie the evidence "
                "in the transcript back to your theory, answer the "
                f"strongest points {opp} made, and tell the jury exactly "
                "what verdict the facts require and why. Be decisive."
            ),
        }.get(phase, "Wait — this phase has no attorney action.")
        burden = ""
        if side == "plaintiff":
            burden = (
                " Remember: you carry the burden of proof. A jury that is "
                "merely unsure must find for the defendant."
            )
        elif side == "defendant":
            burden = (
                " Remember: the plaintiff carries the burden of proof by a "
                "preponderance of the evidence. You win by showing their "
                "account is not more likely true than not — you need not "
                "prove an alternative."
            )
        return "\n".join([
            head,
            "",
            guide + burden,
            "",
            "Call deliver_statement(represented_side=\""
            f"{side}\", position=\"advance_client_requested_remedy\", "
            "text=\"...\", claims=[{\"text\": \"...\", \"kind\": "
            "\"fact\", \"exhibit_ids\": [\"exact-id\"], \"confidence\": 1.0}]) "
            "with your statement. In claims, "
            "classify each material proposition as fact, inference, or uncertainty; "
            "cite exact exhibit_ids; and assign inference/uncertainty confidence "
            "from 0 to 1. The side and position fields "
            "are mandatory representation assertions; changing or omitting "
            "either aborts the trial as invalid. If you object, pass the same "
            "represented_side and position fields to object(...).",
        ])

    def _juror_instructions(self, entity_id: str) -> str:
        rubric = (
            "VERDICT RUBRIC — judge the case ONLY on these:\n"
            "1. Decide each material issue on the evidence actually presented. "
            "Do not force a single party winner when issues or remedies split.\n"
            "2. This is a civil matter, decided on the preponderance of the "
            "evidence: find for the plaintiff on an issue only if their "
            "account is more likely true than not. The criminal standard, "
            "proof beyond a reasonable doubt, does not apply. If the "
            "plaintiff has not met this burden, find for the defendant.\n"
            "3. Weigh what was proven, not what was merely asserted or "
            "what you imagine might be true. Treat CLAIM AUDIT FLAGS as reasons "
            "to discount unsupported factual refinements.\n"
            "Do not let sympathy, the attorneys' personalities, or "
            "anything outside the transcript decide your vote."
        )
        if self._forum:
            rubric += f"\nForum: {self._forum}. Apply its civil standard of proof."
        if self._phase == "jury_deliberation":
            return "\n".join([
                "You are a JUROR. The evidence is closed. Deliberate "
                "aloud with your fellow jurors before the ballot.",
                "",
                rubric,
                "",
                "State how you are weighing the evidence and which way you "
                "are leaning, engaging points other jurors have raised. "
                "Call deliberate(text=\"...\").",
            ])
        if self._phase == "jury_vote":
            return "\n".join([
                "You are a JUROR. Cast your single closed ballot now.",
                "",
                rubric,
                "",
                "Call cast_vote with: winner=\"plaintiff\" or \"defendant\" "
                "for a clean party result, \"mixed\" for split/partial/conditional "
                "relief, or \"none\" for consent or a no-merits disposition; "
                "disposition_type=\"party_win\", \"partial_relief\", "
                "\"conditional_order\", \"settlement_or_consent\", "
                "\"no_merits\", or \"appellate_relief\"; issue_findings as "
                "a JSON array of {issue, finding}; optional remedy; and reasoning. "
                "Mixed and none ballots must include issue_findings. Your ballot "
                "reasoning stays private; the disposition is announced.",
            ])
        return "\n".join([
            "You are a JUROR. Watch the trial closely and silently — you "
            "will deliberate and vote once both sides rest.",
            "",
            rubric,
        ])

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _name_of(state: Any, entity_id: str) -> str:
        if hasattr(state, "entities"):
            ent = state.entities.get(entity_id)
            if ent is not None:
                return getattr(ent, "name", entity_id)
        return entity_id


def _phase_narration(phase: str) -> str:
    return {
        "opening_statements": "The judge opens the proceeding. Each side delivers an opening statement.",
        "direct_examination": "Direct examination begins — each attorney presents their case.",
        "cross_examination": "Cross-examination — each attorney challenges the opposing case.",
        "closing_statements": "Closing statements — each side delivers their final argument.",
        "jury_deliberation": "The jury retires to deliberate. Each juror shares their view.",
        "jury_vote": "The jury votes by closed ballot.",
        "verdict": "The judge prepares to read the verdict.",
    }.get(phase, f"Phase: {phase}")
