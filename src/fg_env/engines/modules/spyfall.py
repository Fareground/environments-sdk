"""Spyfall domain module.

Ruleset:
  - 3–8 players. One is secretly the Spy; everyone else shares the same
    secret Location and is assigned a Role at that location.
  - Each questioning round, one player ASKS another a single question
    about the location. The answerer becomes the next asker. The Spy
    must blend in (answers should sound plausible without naming the
    place); non-spies must probe without revealing the place to the
    Spy.
  - At any point:
      * Any player may ACCUSE another → forces an immediate vote.
      * The Spy may GUESS the Location → if right, Spy wins.
  - After `max_questions` rounds with no resolution, a final mandatory
    vote happens.
  - Vote: every alive player sealed-votes for one player. If a strict
    majority lands on a single player:
        - That player is the Spy → non-spies win.
        - That player is NOT the Spy → Spy wins.
      Otherwise (no majority) → continue to spy_guess stage if rounds
      are exhausted, else continue questioning.
  - Spy guess stage: if Spy survived voting (or the questioning timed
    out without a successful identification), Spy gets one last chance
    to name the Location.

Engine wiring:
  * Single phase `playing` with `resolution_mode: simultaneous` so the
    sealed vote rounds resolve atomically.
  * Stage advanced inside tick() based on the previous round's events.
"""
from __future__ import annotations

import logging
import random
from collections import Counter
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule
from fg_env.roles import Role

logger = logging.getLogger(__name__)


# Classic Spyfall locations + plausible stock roles. The engine assigns
# one role per non-spy and keeps the location secret from the Spy.
LOCATIONS: Dict[str, List[str]] = {
    "Airplane": ["Pilot", "Flight Attendant", "Air Marshal", "First-Class Passenger", "Economy Passenger", "Mechanic", "Co-Pilot"],
    "Bank": ["Teller", "Manager", "Security Guard", "Customer", "Loan Officer", "Janitor", "Robber"],
    "Beach": ["Lifeguard", "Surfer", "Tourist", "Ice Cream Vendor", "Sunbather", "Beach Volleyball Player", "Photographer"],
    "Casino": ["Dealer", "Gambler", "Bouncer", "Pit Boss", "Waitress", "Security Guard", "High Roller", "Card Counter"],
    "Cathedral": ["Priest", "Choir Singer", "Tourist", "Organist", "Nun", "Wedding Guest", "Bell Ringer"],
    "Circus Tent": ["Acrobat", "Clown", "Lion Tamer", "Ringmaster", "Magician", "Juggler", "Spectator"],
    "Corporate Party": ["CEO", "Intern", "DJ", "Caterer", "Accountant", "Manager", "Marketing Lead"],
    "Crusader Army": ["Knight", "Archer", "Priest", "Squire", "Monarch", "Scout", "Servant"],
    "Day Spa": ["Masseuse", "Customer", "Receptionist", "Manicurist", "Esthetician", "Yoga Instructor", "Owner"],
    "Embassy": ["Ambassador", "Diplomat", "Security Officer", "Refugee", "Tourist Seeking Help", "Secretary", "Government Official"],
    "Hospital": ["Doctor", "Nurse", "Surgeon", "Patient", "Anesthesiologist", "Janitor", "Therapist", "Intern"],
    "Hotel": ["Bellhop", "Manager", "Doorman", "Concierge", "Housekeeper", "Guest", "Security"],
    "Military Base": ["General", "Soldier", "Medic", "Tank Engineer", "Cook", "Sniper", "Officer"],
    "Movie Studio": ["Director", "Actor", "Lighting Tech", "Sound Engineer", "Costume Designer", "Producer", "Stunt Double"],
    "Ocean Liner": ["Captain", "Bartender", "Musician", "Rich Passenger", "Cook", "Deckhand", "Stowaway"],
    "Passenger Train": ["Conductor", "Engineer", "Passenger", "Restaurant Chef", "Stowaway", "Mechanic", "Train Robber"],
    "Pirate Ship": ["Captain", "First Mate", "Cabin Boy", "Cook", "Cannoneer", "Prisoner", "Lookout"],
    "Polar Station": ["Researcher", "Cook", "Mechanic", "Medic", "Radio Operator", "Biologist", "Geologist"],
    "Police Station": ["Detective", "Officer", "Lawyer", "Suspect", "Witness", "Forensic Scientist", "Custodian"],
    "Restaurant": ["Chef", "Waiter", "Sommelier", "Customer", "Manager", "Bartender", "Dishwasher"],
    "School": ["Principal", "Teacher", "Student", "Janitor", "Cafeteria Worker", "Counselor", "Gym Teacher"],
    "Service Station": ["Mechanic", "Pump Attendant", "Customer", "Manager", "Tow Truck Driver", "Cashier", "Auto Body Repairer"],
    "Space Station": ["Commander", "Engineer", "Scientist", "Doctor", "Space Tourist", "Alien Researcher", "Mission Control"],
    "Submarine": ["Captain", "Sonar Operator", "Cook", "Engineer", "Navigator", "Medic", "Torpedo Operator"],
    "Supermarket": ["Cashier", "Customer", "Stock Clerk", "Manager", "Butcher", "Bakery Worker", "Security"],
    "Theater": ["Actor", "Director", "Stage Hand", "Audience Member", "Usher", "Ticket Vendor", "Critic"],
    "University": ["Professor", "Student", "Janitor", "Dean", "Research Assistant", "Librarian", "Security"],
    "World War II Squad": ["Officer", "Sniper", "Medic", "Radio Operator", "Tank Crewman", "Engineer", "Cook"],
    "Zoo": ["Zookeeper", "Visitor", "Veterinarian", "Photographer", "Concession Stand Worker", "Tour Guide", "Animal Trainer"],
}


STAGE_DISCUSS = "discuss"
STAGE_VOTE = "vote"
STAGE_SPY_GUESS = "spy_guess"
STAGE_OVER = "over"


class SpyfallModule(DomainModule):
    """Drives a Spyfall match."""

    def __init__(self, name: str = "spyfall", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0x5099FA11))
        self._max_questions: int = int(p.get("max_questions", 8))
        # Accusations are LOCKED until EVERY alive player has answered at
        # least one question. Without this, agents accuse on the very
        # first answer and the match ends after one Q&A. Players who've
        # answered are tracked in `_answered_players`.
        self._answered_players: set = set()
        self._bootstrapped = False
        self._game_over = False

        # Per-match secret state.
        self._location: str = ""
        self._location_roles: List[str] = []
        self._spy_id: str = ""
        self._role_by_player: Dict[str, str] = {}  # non-spy → location role
        # Per-round mutable state.
        self._stage: str = STAGE_DISCUSS
        self._question_round: int = 0           # how many questions asked
        self._seat_order: List[str] = []
        self._active_asker: str = ""            # whose turn to ask
        self._pending_answerer: str = ""        # who must answer next
        self._pending_question: str = ""
        self._accused_id: str = ""              # if someone accused → triggers vote
        self._accuser_id: str = ""
        self._last_round_resolved: int = 0
        self._winning_team: Optional[str] = None

    @property
    def description(self) -> str:
        return ("Spyfall: hidden-spy social deduction. Find the spy via "
                "probing questions without revealing the location.")

    @property
    def custom_actions(self) -> List[str]:
        return ["ask", "answer", "discuss", "accuse", "guess_location", "vote_spy"]

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _alive_players(self, state: Any) -> List[Any]:
        return [e for e in state.entities.values()
                if e.entity_type == self._player_type and e.alive]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return
        # Roles for the kernel registry (so perception filtering knows
        # team membership). Two teams: townfolk (good guys at the
        # location) and spy.
        state.roles.define_role(Role(
            name="townfolk", team="townfolk", sees_teammates=False,
            description="You're at the secret location. Find the Spy among you — without giving the location away.",
        ))
        state.roles.define_role(Role(
            name="spy", team="spy", sees_teammates=False,
            description="You're the Spy. You DON'T know the location. Blend in by asking and answering plausibly; guess the location any round to win.",
        ))

        players = self._alive_players(state)
        n = len(players)
        if n < 3:
            logger.warning(f"Spyfall: only {n} players — needs 3+. Game disabled.")
            self._bootstrapped = True
            self._game_over = True
            return

        # Pick a location.
        loc_override = (self._params or {}).get("location")
        if loc_override and loc_override in LOCATIONS:
            loc_name = loc_override
        else:
            loc_name = self._rng.choice(list(LOCATIONS.keys()))
        self._location = loc_name
        self._location_roles = list(LOCATIONS[loc_name])

        # Assign one Spy + give everyone else a location-specific role.
        shuffled = list(players)
        self._rng.shuffle(shuffled)
        spy = shuffled[0]
        self._spy_id = spy.id
        state.roles.assign(spy.id, "spy")
        spy.set("spyfall_role", "(Spy)")

        roles_pool = list(self._location_roles)
        self._rng.shuffle(roles_pool)
        for i, p in enumerate(shuffled[1:]):
            role = roles_pool[i % len(roles_pool)]
            state.roles.assign(p.id, "townfolk")
            self._role_by_player[p.id] = role
            p.set("spyfall_role", role)

        # Seat order + starting asker.
        self._seat_order = [p.id for p in players]
        self._active_asker = shuffled[self._rng.randrange(len(shuffled))].id
        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_seat(self, current: str) -> str:
        if not self._seat_order:
            return current
        try:
            idx = self._seat_order.index(current)
        except ValueError:
            return self._seat_order[0]
        return self._seat_order[(idx + 1) % len(self._seat_order)]

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        if self._game_over:
            return []
        ent = state.get_entity(entity_id)
        if not ent or not ent.alive:
            return []

        # `discuss` is ALWAYS a safe fallback. Keeping it in every
        # stage's option set prevents validation-retry storms when the
        # LLM picks a side action that isn't strictly the "right" one for
        # the stage — the engine just records a public utterance and the
        # module auto-fills the canonical action in resolution.
        if self._stage == STAGE_DISCUSS:
            options = {"discuss"}
            # Accusations only open once every alive player has answered
            # at least one question — so the table has heard from
            # everyone before pulling the trigger.
            alive_ids = {p.id for p in self._alive_players(state)}
            if alive_ids.issubset(self._answered_players):
                options.add("accuse")
            if entity_id == self._pending_answerer and self._pending_question:
                options.add("answer")
            elif entity_id == self._active_asker:
                options.add("ask")
            if entity_id == self._spy_id:
                options.add("guess_location")
            return [a for a in valid_actions if a in options]

        if self._stage == STAGE_VOTE:
            # Vote is the canonical action; allow `discuss` so the agent
            # doesn't burn validation attempts if it free-forms instead.
            return [a for a in valid_actions if a in {"vote_spy", "discuss"}]

        if self._stage == STAGE_SPY_GUESS:
            if entity_id == self._spy_id:
                return [a for a in valid_actions if a in {"guess_location", "discuss"}]
            return [a for a in valid_actions if a == "discuss"]

        return []

    # ------------------------------------------------------------------
    # Tick — resolve previous round, advance stage, emit narrative
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []

        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            stage_played = self._stage
            if stage_played == STAGE_DISCUSS:
                events.extend(self._resolve_discuss(state, prev))
            elif stage_played == STAGE_VOTE:
                events.extend(self._resolve_vote(state, prev))
            elif stage_played == STAGE_SPY_GUESS:
                events.extend(self._resolve_spy_guess(state, prev))
            self._last_round_resolved = prev
            if self._game_over:
                return events

        # Stage announcement.
        events.append({
            "event_type": "spyfall_stage",
            "narrative": self._stage_narrative(),
            "data": {
                "stage": self._stage,
                "round": round_number,
                "question_round": self._question_round,
                "max_questions": self._max_questions,
                "active_asker": self._active_asker,
                "pending_answerer": self._pending_answerer,
                "pending_question": self._pending_question,
            },
        })
        return events

    def _stage_narrative(self) -> str:
        if self._stage == STAGE_DISCUSS:
            n = self._question_round + 1
            return f"Question {n}/{self._max_questions} — the table probes for the Spy."
        if self._stage == STAGE_VOTE:
            return "Sealed vote — pick who you think is the Spy."
        if self._stage == STAGE_SPY_GUESS:
            return "The Spy's last chance — guess the location."
        return ""

    # ------------------------------------------------------------------
    # Stage resolvers
    # ------------------------------------------------------------------

    def _resolve_discuss(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []

        # 1) Check spy guess (highest priority — instant win).
        guess_payload = self._collect_guess(state, round_number)
        if guess_payload:
            return self._handle_spy_guess(state, guess_payload, round_number)

        # 2) Check accusation — triggers a vote stage.
        for p in self._alive_players(state):
            if p.get(f"_accuse_target_r{round_number}"):
                target_id = p.get(f"_accuse_target_r{round_number}")
                self._accuser_id = p.id
                self._accused_id = target_id
                tgt = state.get_entity(target_id)
                events.append({
                    "event_type": "spyfall_accusation",
                    "actor_id": p.id,
                    "target_id": target_id,
                    "data": {"round": round_number, "accuser": p.id, "accused": target_id},
                    "narrative": f"{p.name} ACCUSES {(tgt.name if tgt else target_id)} of being the Spy. Vote called.",
                })
                self._stage = STAGE_VOTE
                return events

        # 3) Normal question/answer flow.
        # If there was a pending answer this round, surface it; then the
        # answerer becomes the next asker.
        if self._pending_answerer and self._pending_question:
            ans_ent = state.get_entity(self._pending_answerer)
            answer = ans_ent.get(f"_answer_r{round_number}") if ans_ent else None
            if not answer:
                answer = "(no answer)"
            events.append({
                "event_type": "spyfall_qa",
                "actor_id": self._pending_answerer,
                "data": {
                    "round": round_number,
                    "asker_id": self._active_asker,
                    "answerer_id": self._pending_answerer,
                    "question": self._pending_question,
                    "answer": answer,
                },
                "narrative": f"{ans_ent.name if ans_ent else self._pending_answerer} answers: {answer}",
            })
            # Record that this player has now answered at least once —
            # used to gate the `accuse` action.
            self._answered_players.add(self._pending_answerer)
            # Hand the asker baton to the answerer.
            self._active_asker = self._pending_answerer
            self._pending_answerer = ""
            self._pending_question = ""
            self._question_round += 1
        else:
            # Ask phase: gather the active asker's question.
            asker_ent = state.get_entity(self._active_asker)
            ask_target = asker_ent.get(f"_ask_target_r{round_number}") if asker_ent else None
            ask_text = asker_ent.get(f"_ask_question_r{round_number}") if asker_ent else None
            if not ask_target or not ask_text:
                # Auto-pick to keep the game moving.
                alive_others = [p.id for p in self._alive_players(state)
                                if p.id != self._active_asker]
                if alive_others:
                    ask_target = self._rng.choice(alive_others)
                ask_text = ask_text or "Tell me something about this place."
            self._pending_answerer = ask_target
            self._pending_question = ask_text
            tgt_ent = state.get_entity(ask_target)
            events.append({
                "event_type": "spyfall_question",
                "actor_id": self._active_asker,
                "target_id": ask_target,
                "data": {
                    "round": round_number,
                    "asker_id": self._active_asker,
                    "answerer_id": ask_target,
                    "question": ask_text,
                },
                "narrative": (
                    f"{(asker_ent.name if asker_ent else self._active_asker)} → "
                    f"{(tgt_ent.name if tgt_ent else ask_target)}: {ask_text}"
                ),
            })

        # Stage flip to vote after max_questions consumed.
        if self._question_round >= self._max_questions:
            events.append({
                "event_type": "spyfall_force_vote",
                "narrative": "Questions exhausted. Final sealed vote.",
                "data": {"round": round_number},
            })
            self._stage = STAGE_VOTE
        return events

    def _collect_guess(self, state: Any, round_number: int) -> Optional[Dict[str, Any]]:
        spy = state.get_entity(self._spy_id)
        if not spy:
            return None
        g = spy.get(f"_guess_location_r{round_number}")
        if not g:
            return None
        return {"location": g, "round": round_number}

    def _handle_spy_guess(self, state: Any, payload: Dict[str, Any], round_number: int) -> List[Dict[str, Any]]:
        g = str(payload.get("location") or "").strip().lower()
        actual = self._location.strip().lower()
        # Accept partial match against the canonical location name.
        correct = bool(g) and (g == actual or g in actual or actual in g)
        spy = state.get_entity(self._spy_id)
        events: List[Dict[str, Any]] = [{
            "event_type": "spyfall_spy_guess",
            "actor_id": self._spy_id,
            "data": {
                "round": round_number,
                "guess": payload.get("location"),
                "actual_location": self._location,
                "correct": correct,
            },
            "narrative": (
                f"{(spy.name if spy else 'The Spy')} guesses the location: "
                f"\"{payload.get('location')}\" — {'CORRECT.' if correct else 'WRONG.'} "
                f"(actual: {self._location})"
            ),
        }]
        if correct:
            events.append(self._spy_victory(state, "guessed_location"))
        else:
            events.append(self._townfolk_victory(state, "spy_guess_wrong"))
        self._game_over = True
        self._stage = STAGE_OVER
        return events

    def _resolve_vote(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        votes: Dict[str, str] = {}
        alive_ids = [p.id for p in self._alive_players(state)]
        for p in self._alive_players(state):
            v = p.get(f"_vote_spy_r{round_number}")
            if v and v in alive_ids:
                votes[p.id] = v
        # Auto-fill any missing votes with a random non-self target so
        # every player participates — silent non-voters were tipping the
        # spy's hand and confusing players.
        for p in self._alive_players(state):
            if p.id in votes:
                continue
            options = [pid for pid in alive_ids if pid != p.id]
            if options:
                votes[p.id] = self._rng.choice(options)
                # Also stash on the entity so per-voter event tracking
                # downstream picks it up consistently.
                p.set(f"_vote_spy_r{round_number}", votes[p.id])
        tally = Counter(votes.values())
        if not tally:
            # No votes — treat as inconclusive.
            self._stage = STAGE_SPY_GUESS
            return [{
                "event_type": "spyfall_vote_resolved",
                "narrative": "No votes cast. The Spy gets one last chance.",
                "data": {"round": round_number, "tally": {}},
            }]
        top, top_count = tally.most_common(1)[0]
        majority = top_count > len(self._alive_players(state)) // 2
        events: List[Dict[str, Any]] = []
        tally_named = {
            (state.get_entity(pid).name if state.get_entity(pid) else pid): c
            for pid, c in tally.items()
        }
        # Per-voter detail so the viz can draw voter → target arrows.
        per_voter = [
            {"voter_id": vid, "target_id": tid,
             "voter_name": (state.get_entity(vid).name if state.get_entity(vid) else vid),
             "target_name": (state.get_entity(tid).name if state.get_entity(tid) else tid)}
            for vid, tid in votes.items()
        ]
        if not majority:
            events.append({
                "event_type": "spyfall_vote_resolved",
                "data": {
                    "round": round_number, "tally": tally_named,
                    "majority_id": None,
                    "result": "no_majority",
                    "per_voter": per_voter,
                },
                "narrative": f"No majority — votes split: {tally_named}. The Spy gets one last chance.",
            })
            self._stage = STAGE_SPY_GUESS
            return events
        # Majority landed on `top`.
        accused_ent = state.get_entity(top)
        is_spy = top == self._spy_id
        events.append({
            "event_type": "spyfall_vote_resolved",
            "data": {
                "round": round_number,
                "tally": tally_named,
                "majority_id": top,
                "majority_name": accused_ent.name if accused_ent else top,
                "was_spy": is_spy,
                "result": "majority_caught_spy" if is_spy else "majority_wrong",
                "per_voter": per_voter,
            },
            "narrative": (
                f"The vote: {tally_named} → majority on "
                f"{(accused_ent.name if accused_ent else top)}. "
                + ("They WERE the Spy. Townfolk win." if is_spy
                   else f"They were NOT the Spy — the Spy was {self._spy_name(state)}. Spy wins.")
            ),
        })
        if is_spy:
            events.append(self._townfolk_victory(state, "voted_out_spy"))
        else:
            events.append(self._spy_victory(state, "voted_wrong"))
        self._game_over = True
        self._stage = STAGE_OVER
        return events

    def _resolve_spy_guess(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        spy = state.get_entity(self._spy_id)
        g = spy.get(f"_guess_location_r{round_number}") if spy else None
        if not g:
            # Spy didn't guess — they lose.
            events = [{
                "event_type": "spyfall_spy_guess",
                "actor_id": self._spy_id,
                "data": {"round": round_number, "guess": None, "actual_location": self._location, "correct": False},
                "narrative": f"The Spy declined to guess. Location was {self._location}. Townfolk win.",
            }, self._townfolk_victory(state, "spy_no_guess")]
            self._game_over = True
            self._stage = STAGE_OVER
            return events
        return self._handle_spy_guess(state, {"location": g, "round": round_number}, round_number)

    def _spy_name(self, state: Any) -> str:
        e = state.get_entity(self._spy_id)
        return e.name if e else self._spy_id

    def _spy_victory(self, state: Any, reason: str) -> Dict[str, Any]:
        return {
            "event_type": "spy_victory",
            "narrative": f"Spy wins ({reason.replace('_', ' ')}).",
            "data": {
                "winning_team": "spy",
                "winners": [self._spy_id],
                "reason": reason,
                "location": self._location,
                "spy_id": self._spy_id,
                "spy_name": self._spy_name(state),
            },
        }

    def _townfolk_victory(self, state: Any, reason: str) -> Dict[str, Any]:
        winners = [p.id for p in self._alive_players(state) if p.id != self._spy_id]
        return {
            "event_type": "townfolk_victory",
            "narrative": f"Townfolk win ({reason.replace('_', ' ')}).",
            "data": {
                "winning_team": "townfolk",
                "winners": winners,
                "reason": reason,
                "location": self._location,
                "spy_id": self._spy_id,
                "spy_name": self._spy_name(state),
            },
        }

    # ------------------------------------------------------------------
    # Action recording
    # ------------------------------------------------------------------

    def validate_action(self, action_name: str, actor: Any, target: Any, state: Any) -> Optional[str]:
        if not actor or not getattr(actor, "alive", True):
            return "Dead/missing actor."
        if self._game_over:
            return "Game over."
        return None

    def post_resolution(
        self, actor_id: str, action_name: str, success: bool, result: Any, state: Any,
    ) -> List[Dict[str, Any]]:
        if action_name not in {"ask", "answer", "discuss", "accuse", "guess_location", "vote_spy"}:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name == "ask":
            target_id = self._resolve_player_token(state, params.get("target") or params.get("target_id") or details.get("target_id"))
            question = str(params.get("question") or "").strip()
            if target_id:
                actor.set(f"_ask_target_r{round_number}", target_id)
                actor.set(f"_ask_question_r{round_number}", question or "Tell me about this place.")
                events.append({
                    "event_type": "spyfall_ask_intent",
                    "actor_id": actor_id, "target_id": target_id,
                    "data": {"round": round_number, "question": question},
                    "narrative": f"{actor.name} prepares a question.",
                })

        elif action_name == "answer":
            text = str(params.get("text") or params.get("answer") or "").strip()
            actor.set(f"_answer_r{round_number}", text or "(no answer)")
            events.append({
                "event_type": "spyfall_answer_intent",
                "actor_id": actor_id,
                "data": {"round": round_number, "answer": text},
                "narrative": f"{actor.name} answers (sealed until reveal).",
            })

        elif action_name == "accuse":
            target_id = self._resolve_player_token(state, params.get("target") or params.get("target_id") or details.get("target_id"))
            if target_id and target_id != actor_id:
                actor.set(f"_accuse_target_r{round_number}", target_id)
                tgt = state.get_entity(target_id)
                events.append({
                    "event_type": "spyfall_accuse_intent",
                    "actor_id": actor_id, "target_id": target_id,
                    "data": {"round": round_number},
                    "narrative": f"{actor.name} points the finger at {(tgt.name if tgt else target_id)}.",
                })

        elif action_name == "guess_location":
            guess = str(params.get("location") or params.get("guess") or "").strip()
            if guess:
                actor.set(f"_guess_location_r{round_number}", guess)
                events.append({
                    "event_type": "spyfall_guess_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "private_to": actor_id},
                    "narrative": f"{actor.name} prepares to guess the location.",
                })

        elif action_name == "vote_spy":
            target_id = self._resolve_player_token(state, params.get("target") or params.get("target_id") or details.get("target_id"))
            if target_id:
                actor.set(f"_vote_spy_r{round_number}", target_id)
                tgt = state.get_entity(target_id)
                events.append({
                    "event_type": "spyfall_vote_cast",
                    "actor_id": actor_id,
                    "target_id": target_id,
                    "data": {
                        "round": round_number,
                        "voter_id": actor_id,
                        "target_id": target_id,
                        "target_name": tgt.name if tgt else target_id,
                    },
                    "narrative": f"{actor.name} casts a sealed vote.",
                })

        return events

    def _resolve_player_token(self, state: Any, token: Any) -> Optional[str]:
        if not isinstance(token, str) or not token.strip():
            return None
        token = token.strip().strip('"\'')
        if state.get_entity(token):
            return token
        lower = token.lower()
        # Allow trailing "(p_xxx)" form.
        if "(" in token and token.endswith(")"):
            inner = token[token.rfind("(") + 1:-1].strip()
            if state.get_entity(inner):
                return inner
        for e in state.entities.values():
            if e.entity_type != self._player_type:
                continue
            if e.name.lower() == lower or e.name.lower().split()[0] == lower or lower in e.name.lower():
                return e.id
        return None

    # ------------------------------------------------------------------
    # Perception — private location / role info per player
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        is_spy = entity_id == self._spy_id

        roster = [
            {"id": p.id, "name": p.name}
            for p in self._alive_players(state)
        ]
        asker_ent = state.get_entity(self._active_asker) if self._active_asker else None
        answerer_ent = state.get_entity(self._pending_answerer) if self._pending_answerer else None

        # Stage cheat sheet so the LLM knows what to call.
        alive_ids = {p.id for p in self._alive_players(state)}
        not_yet_answered = alive_ids - self._answered_players
        accuse_open = len(not_yet_answered) == 0
        if not accuse_open:
            missing_names = sorted(
                (state.get_entity(pid).name if state.get_entity(pid) else pid)
                for pid in not_yet_answered
            )
            accuse_lock_note = (
                f" Accusations are LOCKED until everyone has answered at "
                f"least once. Still waiting on: {', '.join(missing_names)}."
            )
        else:
            accuse_lock_note = ""
        if self._stage == STAGE_DISCUSS:
            if entity_id == self._pending_answerer and self._pending_question:
                stage_hint = (
                    f'You were asked: "{self._pending_question}". Reply with '
                    'answer(text="…"). Sound like someone who really belongs here — '
                    "specific enough to look credible, vague enough not to hand the "
                    "Spy the location."
                )
            elif entity_id == self._active_asker:
                example_id = next((p["id"] for p in roster if p["id"] != entity_id), "")
                stage_hint = (
                    f'YOU are the asker. Use ask(target="{example_id}", question="…") '
                    "to probe someone — short, pointed, location-specific."
                    + (' Or accuse(target="…") if you\'re sure.' if accuse_open else "")
                    + (" Or guess_location(location=\"…\") to win." if is_spy else "")
                    + accuse_lock_note
                )
            else:
                stage_hint = (
                    'Use discuss to push the table\'s read.'
                    + (' accuse(target="…") if you\'re confident.' if accuse_open else "")
                    + (' Or guess_location(location="…") to win the round.' if is_spy else "")
                    + accuse_lock_note
                )
        elif self._stage == STAGE_VOTE:
            stage_hint = 'Sealed final vote. Use vote_spy(target="<player_id>").'
        elif self._stage == STAGE_SPY_GUESS:
            if is_spy:
                stage_hint = ('Last chance. Use guess_location(location="…"). '
                              'Right = you win; wrong = townfolk win.')
            else:
                stage_hint = "Wait — the Spy is making their final guess."
        else:
            stage_hint = ""

        out: Dict[str, Any] = {
            "stage": self._stage,
            "question_round": self._question_round,
            "max_questions": self._max_questions,
            "active_asker_id": self._active_asker,
            "active_asker_name": asker_ent.name if asker_ent else None,
            "pending_answerer_id": self._pending_answerer,
            "pending_answerer_name": answerer_ent.name if answerer_ent else None,
            "pending_question": self._pending_question,
            "roster": roster,
            "i_am_spy": is_spy,
            "stage_hint": stage_hint,
        }

        if is_spy:
            # Spy doesn't see location or roles, but does see what others
            # have said publicly (engine handles speech).
            out["your_role"] = "(Spy)"
            out["location_pool"] = list(LOCATIONS.keys())
        else:
            out["your_location"] = self._location
            out["your_role"] = self._role_by_player.get(entity_id, "")
            # Show all location roles so the player can ask role-specific
            # questions without giving away the place.
            out["all_roles_at_location"] = list(self._location_roles)

        return out
