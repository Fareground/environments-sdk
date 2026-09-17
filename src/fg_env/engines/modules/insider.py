"""Insider domain module.

Ruleset:
  - 4–8 players. One MASTER (publicly known), one INSIDER (secret),
    the rest are COMMONERS.
  - At setup the Master is dealt a secret noun. The Insider also knows
    it; Commoners do not.

Phase 1 — Questioning (max_questions Q&A pairs):
  - Each Q&A is a pair of engine rounds.
  - Ask round: every non-Master player may submit ask_question(text);
    the module picks one to put to the Master.
  - Answer round: the Master submits answer_question(answer) ∈
    {yes, no, unknown}; the answer reveals publicly.
  - At ANY ask round, any non-Master player may submit
    guess_answer(text). If the guess matches the secret noun (case-
    insensitive, substring tolerant) → jump to Phase 2.
  - If max_questions Q&A pairs complete with no correct guess →
    EVERYONE loses (time_out outcome).

Phase 2 — Insider hunt:
  - One discuss round to argue who the Insider was, then a single
    sealed vote round.
  - Majority on the actual Insider → Commoners + Master win.
  - Otherwise (no majority, or majority on wrong person) → Insider wins.

Engine wiring:
  * Single phase "playing" with resolution_mode=simultaneous.
  * Stages cycle internally: ASK → ANSWER (×N) → DISCUSS → VOTE → OVER.
"""
from __future__ import annotations

import logging
import random
from collections import Counter
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule
from fg_env.roles import Role

logger = logging.getLogger(__name__)


STAGE_ASK = "ask"
STAGE_ANSWER = "answer"
STAGE_DISCUSS = "discuss"
STAGE_VOTE = "vote"
STAGE_OVER = "over"


# Classic Insider noun pool — common, concrete, ~100 entries.
WORD_POOL: List[str] = [
    # Animals
    "lion", "elephant", "octopus", "penguin", "kangaroo", "owl", "dolphin",
    "giraffe", "spider", "bee", "shark", "tiger", "horse", "rabbit", "bear",
    # Food & drink
    "pizza", "sushi", "banana", "chocolate", "coffee", "ice cream", "bread",
    "noodles", "cheese", "honey", "pancake", "burger", "salad", "wine",
    # Objects
    "telephone", "umbrella", "mirror", "guitar", "clock", "telescope",
    "backpack", "key", "candle", "scissors", "ladder", "hammer", "bicycle",
    "computer", "camera",
    # Places
    "library", "beach", "cathedral", "subway", "mountain", "desert",
    "forest", "casino", "hospital", "airport", "stadium", "kitchen",
    "garden", "factory", "island",
    # Vehicles
    "submarine", "helicopter", "motorcycle", "spaceship", "train",
    "skateboard",
    # Concepts / abstract
    "gravity", "language", "music", "shadow", "dream", "memory", "silence",
    "winter", "thunder", "rainbow", "fire", "magnet", "echo",
    # Sports & games
    "soccer", "chess", "tennis", "skiing", "boxing", "yoga", "marathon",
    # Body parts
    "elbow", "tongue", "heart", "eyelash",
    # Misc
    "volcano", "diamond", "robot", "pyramid", "laser", "compass", "needle",
    "balloon", "vampire", "magic",
]


class InsiderModule(DomainModule):
    """Drives an Insider match."""

    def __init__(self, name: str = "insider", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0x1A51DE5))
        self._max_questions: int = int(p.get("max_questions", 20))
        # Guesses are LOCKED until the group has asked at least this
        # many questions. Without the gate, agents rush to a correct
        # guess on Q1 and the vote phase has no signal — the table
        # never built a read on who the Insider is. Default ≈ 40% of
        # the budget so there's real questioning before any guess
        # can resolve.
        self._min_questions_before_guess: int = int(
            p.get("min_questions_before_guess", max(6, self._max_questions * 2 // 5))
        )
        self._bootstrapped = False
        self._game_over = False

        # Secret state.
        self._secret: str = ""
        self._master_id: str = ""
        self._insider_id: str = ""
        # Stage / round state.
        self._stage: str = STAGE_ASK
        self._question_round: int = 0
        self._pending_asker_id: str = ""
        self._pending_question: str = ""
        self._last_round_resolved: int = 0
        self._correct_guesser_id: str = ""

    @property
    def description(self) -> str:
        return ("Insider: 4-8 players, one Master + one hidden Insider; "
                "group guesses a secret noun via yes/no questions, then "
                "votes out the Insider.")

    @property
    def custom_actions(self) -> List[str]:
        return [
            "ask_question", "answer_question", "guess_answer",
            "discuss", "vote_insider",
        ]

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _alive_players(self, state: Any) -> List[Any]:
        return [e for e in state.entities.values()
                if e.entity_type == self._player_type and e.alive]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return

        state.roles.define_role(Role(
            name="master", team="commoners", sees_teammates=False,
            description=("You are the MASTER. You know the secret noun. "
                         "Answer each yes/no question truthfully with "
                         "'yes', 'no', or 'unknown'. You're on the "
                         "Commoners' side: help the group reach the "
                         "answer, then help vote out the Insider."),
        ))
        state.roles.define_role(Role(
            name="insider", team="insider", sees_teammates=False,
            description=("You are the INSIDER. You also know the secret. "
                         "Subtly steer the group toward the answer without "
                         "being detected. You win if the group reaches the "
                         "answer in time AND fails to vote you out."),
        ))
        state.roles.define_role(Role(
            name="commoner", team="commoners", sees_teammates=False,
            description=("You are a COMMONER. You don't know the noun. "
                         "Ask sharp yes/no questions to converge on it, "
                         "then hunt the Insider in the vote phase."),
        ))

        players = self._alive_players(state)
        n = len(players)
        if n < 4:
            logger.warning(f"Insider: only {n} players — needs 4+. Game disabled.")
            self._bootstrapped = True
            self._game_over = True
            return
        if n > 8:
            # Trim the role pool, not the seats; the extra players just
            # become commoners.
            pass

        # Lock the guess gate to the player count — one round-robin of
        # questions guarantees every commoner has spoken at least once
        # before any guess can resolve. Removes the redundant
        # `min_questions_before_guess` knob users had to tune by hand.
        self._min_questions_before_guess = n

        # Pick a secret noun.
        word_override = (self._params or {}).get("secret_word")
        if isinstance(word_override, str) and word_override.strip():
            self._secret = word_override.strip()
        else:
            self._secret = self._rng.choice(WORD_POOL)

        # Assign Master + Insider; the rest become Commoners.
        shuffled = list(players)
        self._rng.shuffle(shuffled)
        master = shuffled[0]
        insider = shuffled[1]
        self._master_id = master.id
        self._insider_id = insider.id
        state.roles.assign(master.id, "master")
        master.set("insider_role", "Master")
        state.roles.assign(insider.id, "insider")
        insider.set("insider_role", "Insider")
        for p in shuffled[2:]:
            state.roles.assign(p.id, "commoner")
            p.set("insider_role", "Commoner")

        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        if self._game_over:
            return []
        ent = state.get_entity(entity_id)
        if not ent or not ent.alive:
            return []
        is_master = entity_id == self._master_id

        # `discuss` is always a safe fallback so the LLM never burns
        # validation retries on a side action.
        guesses_open = self._question_round >= self._min_questions_before_guess
        if self._stage == STAGE_ASK:
            options = {"discuss"}
            if is_master:
                # Master waits — they answer only in the answer round.
                pass
            else:
                options.add("ask_question")
                if guesses_open:
                    options.add("guess_answer")
            return [a for a in valid_actions if a in options]

        if self._stage == STAGE_ANSWER:
            options = {"discuss"}
            if is_master:
                options.add("answer_question")
            elif guesses_open:
                # Non-master may still attempt a guess on the answer
                # round (they react to the answer that just landed) —
                # but only once enough questions have been asked.
                options.add("guess_answer")
            return [a for a in valid_actions if a in options]

        if self._stage == STAGE_DISCUSS:
            return [a for a in valid_actions if a == "discuss"]

        if self._stage == STAGE_VOTE:
            return [a for a in valid_actions if a in {"vote_insider", "discuss"}]

        return []

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []

        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            played = self._stage
            if played == STAGE_ASK:
                events.extend(self._resolve_ask(state, prev))
            elif played == STAGE_ANSWER:
                events.extend(self._resolve_answer(state, prev))
            elif played == STAGE_DISCUSS:
                events.extend(self._resolve_discuss(state, prev))
            elif played == STAGE_VOTE:
                events.extend(self._resolve_vote(state, prev))
            self._last_round_resolved = prev
            if self._game_over:
                return events

        # Announce the current stage.
        events.append({
            "event_type": "insider_stage",
            "narrative": self._stage_narrative(),
            "data": {
                "stage": self._stage,
                "round": round_number,
                "question_round": self._question_round,
                "max_questions": self._max_questions,
                "pending_asker_id": self._pending_asker_id,
                "pending_question": self._pending_question,
                "master_id": self._master_id,
            },
        })
        return events

    def _stage_narrative(self) -> str:
        n = self._question_round + 1
        if self._stage == STAGE_ASK:
            return (f"Question {n}/{self._max_questions} — anyone may ask a "
                    "yes/no question (or guess the answer).")
        if self._stage == STAGE_ANSWER:
            return f"The Master answers the question."
        if self._stage == STAGE_DISCUSS:
            return "Answer reached. Now hunt the Insider — open discussion."
        if self._stage == STAGE_VOTE:
            return "Sealed vote — pick who you think the Insider is."
        return ""

    # ------------------------------------------------------------------
    # Stage resolvers
    # ------------------------------------------------------------------

    def _resolve_ask(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []

        # 1) Check for a correct guess — highest priority.
        for p in self._alive_players(state):
            if p.id == self._master_id:
                continue  # Master can't guess; they know.
            g = p.get(f"_guess_r{round_number}")
            if not g:
                continue
            events.append({
                "event_type": "insider_guess",
                "actor_id": p.id,
                "data": {
                    "round": round_number,
                    "guess": g,
                    "correct": self._matches_secret(g),
                    "secret": self._secret if self._matches_secret(g) else None,
                },
                "narrative": (f"{p.name} guesses \"{g}\" — "
                              + ("CORRECT!" if self._matches_secret(g)
                                 else "incorrect.")),
            })
            if self._matches_secret(g):
                self._correct_guesser_id = p.id
                self._stage = STAGE_DISCUSS
                events.append({
                    "event_type": "insider_secret_revealed",
                    "data": {"secret": self._secret, "guesser_id": p.id},
                    "narrative": (f"The secret was \"{self._secret}\". "
                                  f"Now find the Insider."),
                })
                return events

        # 2) No correct guess — pick a question to put to the Master.
        candidates: List[Dict[str, Any]] = []
        for p in self._alive_players(state):
            if p.id == self._master_id:
                continue
            q = p.get(f"_ask_q_r{round_number}")
            if q:
                candidates.append({"asker_id": p.id, "name": p.name, "text": q})
        if candidates:
            chosen = self._rng.choice(candidates)
            self._pending_asker_id = chosen["asker_id"]
            self._pending_question = chosen["text"]
            events.append({
                "event_type": "insider_question",
                "actor_id": chosen["asker_id"],
                "data": {
                    "round": round_number,
                    "asker_id": chosen["asker_id"],
                    "asker_name": chosen["name"],
                    "question": chosen["text"],
                    "other_questions": [
                        {"asker_id": c["asker_id"], "asker_name": c["name"], "text": c["text"]}
                        for c in candidates if c["asker_id"] != chosen["asker_id"]
                    ],
                },
                "narrative": f"{chosen['name']} asks: {chosen['text']}",
            })
            self._stage = STAGE_ANSWER
        else:
            # Nobody asked — quietly advance the question counter and
            # try again next round. Helps avoid stalls.
            self._question_round += 1
            events.append({
                "event_type": "insider_no_question",
                "data": {"round": round_number},
                "narrative": "(no question this round)",
            })

        # 3) Check time-out.
        if self._question_round >= self._max_questions:
            events.append(self._timeout_outcome(state))
            self._game_over = True
            self._stage = STAGE_OVER
        return events

    def _resolve_answer(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        master = state.get_entity(self._master_id)
        raw = master.get(f"_answer_r{round_number}") if master else None
        ans = self._coerce_answer(raw, master)
        events.append({
            "event_type": "insider_answer",
            "actor_id": self._master_id,
            "data": {
                "round": round_number,
                "asker_id": self._pending_asker_id,
                "question": self._pending_question,
                "answer": ans,
            },
            "narrative": f"Master: {ans}.",
        })
        # Also accept any guesses placed on this round (the reaction
        # round).
        for p in self._alive_players(state):
            if p.id == self._master_id:
                continue
            g = p.get(f"_guess_r{round_number}")
            if not g:
                continue
            events.append({
                "event_type": "insider_guess",
                "actor_id": p.id,
                "data": {
                    "round": round_number,
                    "guess": g,
                    "correct": self._matches_secret(g),
                    "secret": self._secret if self._matches_secret(g) else None,
                },
                "narrative": (f"{p.name} guesses \"{g}\" — "
                              + ("CORRECT!" if self._matches_secret(g)
                                 else "incorrect.")),
            })
            if self._matches_secret(g):
                self._correct_guesser_id = p.id
                self._stage = STAGE_DISCUSS
                events.append({
                    "event_type": "insider_secret_revealed",
                    "data": {"secret": self._secret, "guesser_id": p.id},
                    "narrative": (f"The secret was \"{self._secret}\". "
                                  f"Now find the Insider."),
                })
                self._pending_asker_id = ""
                self._pending_question = ""
                return events

        # Advance Q counter, clear pending, back to ASK stage.
        self._pending_asker_id = ""
        self._pending_question = ""
        self._question_round += 1
        if self._question_round >= self._max_questions:
            events.append(self._timeout_outcome(state))
            self._game_over = True
            self._stage = STAGE_OVER
        else:
            self._stage = STAGE_ASK
        return events

    def _resolve_discuss(self, state: Any, _round_number: int) -> List[Dict[str, Any]]:
        # Single discuss round, then flip to vote.
        self._stage = STAGE_VOTE
        return []

    def _resolve_vote(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        votes: Dict[str, str] = {}
        alive_ids = [p.id for p in self._alive_players(state)]
        for p in self._alive_players(state):
            v = p.get(f"_vote_insider_r{round_number}")
            if v and v in alive_ids:
                votes[p.id] = v
        # Auto-fill missing votes (random non-self) so the table reads
        # cleanly. Master gets the same treatment.
        for p in self._alive_players(state):
            if p.id in votes:
                continue
            opts = [pid for pid in alive_ids if pid != p.id]
            if opts:
                votes[p.id] = self._rng.choice(opts)
                p.set(f"_vote_insider_r{round_number}", votes[p.id])

        tally = Counter(votes.values())
        per_voter = [
            {"voter_id": vid, "target_id": tid,
             "voter_name": (state.get_entity(vid).name if state.get_entity(vid) else vid),
             "target_name": (state.get_entity(tid).name if state.get_entity(tid) else tid)}
            for vid, tid in votes.items()
        ]
        tally_named = {
            (state.get_entity(pid).name if state.get_entity(pid) else pid): c
            for pid, c in tally.items()
        }
        events: List[Dict[str, Any]] = []
        if not tally:
            # No votes — insider escapes.
            events.append({
                "event_type": "insider_vote_resolved",
                "data": {"round": round_number, "tally": tally_named,
                         "majority_id": None, "result": "no_votes",
                         "per_voter": per_voter},
                "narrative": "No votes cast. The Insider escapes.",
            })
            events.append(self._insider_victory(state, "no_votes"))
            self._game_over = True
            self._stage = STAGE_OVER
            return events
        top, top_count = tally.most_common(1)[0]
        majority = top_count > len(alive_ids) // 2
        accused = state.get_entity(top)
        if majority and top == self._insider_id:
            events.append({
                "event_type": "insider_vote_resolved",
                "data": {"round": round_number, "tally": tally_named,
                         "majority_id": top,
                         "majority_name": accused.name if accused else top,
                         "was_insider": True, "result": "caught_insider",
                         "per_voter": per_voter},
                "narrative": (f"Majority on {(accused.name if accused else top)} — "
                              f"the Insider is caught. Commoners win."),
            })
            events.append(self._commoners_victory(state, "caught_insider"))
        else:
            reason = "no_majority" if not majority else "wrong_majority"
            narrative = (f"No majority. Insider escapes."
                         if not majority
                         else f"Majority on {(accused.name if accused else top)} — "
                              f"not the Insider. Insider escapes.")
            events.append({
                "event_type": "insider_vote_resolved",
                "data": {"round": round_number, "tally": tally_named,
                         "majority_id": top if majority else None,
                         "majority_name": accused.name if (majority and accused) else None,
                         "was_insider": False if majority else None,
                         "result": reason,
                         "per_voter": per_voter},
                "narrative": narrative,
            })
            events.append(self._insider_victory(state, reason))
        self._game_over = True
        self._stage = STAGE_OVER
        return events

    # ------------------------------------------------------------------
    # Outcomes
    # ------------------------------------------------------------------

    def _timeout_outcome(self, state: Any) -> Dict[str, Any]:
        return {
            "event_type": "insider_timeout",
            "narrative": (f"Time ran out before the noun was guessed. "
                          f"The secret was \"{self._secret}\". Everyone loses."),
            "data": {
                "winning_team": None,
                "winners": [],
                "secret": self._secret,
                "reason": "timeout",
                "insider_id": self._insider_id,
                "master_id": self._master_id,
            },
        }

    def _commoners_victory(self, state: Any, reason: str) -> Dict[str, Any]:
        winners = [p.id for p in self._alive_players(state) if p.id != self._insider_id]
        insider = state.get_entity(self._insider_id)
        return {
            "event_type": "insider_commoners_victory",
            "narrative": f"Commoners + Master win ({reason.replace('_', ' ')}).",
            "data": {
                "winning_team": "commoners",
                "winners": winners,
                "reason": reason,
                "secret": self._secret,
                "insider_id": self._insider_id,
                "insider_name": insider.name if insider else None,
                "master_id": self._master_id,
            },
        }

    def _insider_victory(self, state: Any, reason: str) -> Dict[str, Any]:
        insider = state.get_entity(self._insider_id)
        return {
            "event_type": "insider_victory",
            "narrative": f"Insider wins ({reason.replace('_', ' ')}).",
            "data": {
                "winning_team": "insider",
                "winners": [self._insider_id],
                "reason": reason,
                "secret": self._secret,
                "insider_id": self._insider_id,
                "insider_name": insider.name if insider else None,
                "master_id": self._master_id,
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
        if action_name not in self.custom_actions:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name == "ask_question":
            text = str(params.get("text") or params.get("question") or "").strip()
            if text:
                actor.set(f"_ask_q_r{round_number}", text)
                events.append({
                    "event_type": "insider_ask_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "question": text},
                    "narrative": f"{actor.name} prepares a question.",
                })

        elif action_name == "answer_question":
            raw = params.get("answer") or params.get("response")
            v = self._coerce_answer(raw, actor)
            actor.set(f"_answer_r{round_number}", v)
            events.append({
                "event_type": "insider_answer_intent",
                "actor_id": actor_id,
                "data": {"round": round_number, "answer": v, "private_to": actor_id},
                "narrative": f"{actor.name} locks in an answer.",
            })

        elif action_name == "guess_answer":
            guess = str(params.get("guess") or params.get("answer") or "").strip()
            if guess:
                actor.set(f"_guess_r{round_number}", guess)
                events.append({
                    "event_type": "insider_guess_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "private_to": actor_id},
                    "narrative": f"{actor.name} commits to a guess.",
                })

        elif action_name == "vote_insider":
            target_id = self._resolve_player_token(state,
                params.get("target") or params.get("target_id") or details.get("target_id"))
            if target_id:
                actor.set(f"_vote_insider_r{round_number}", target_id)
                tgt = state.get_entity(target_id)
                events.append({
                    "event_type": "insider_vote_cast",
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _matches_secret(self, guess: str) -> bool:
        g = guess.strip().lower()
        s = self._secret.strip().lower()
        if not g:
            return False
        return g == s or g in s or s in g

    @staticmethod
    def _coerce_answer(raw: Any, _actor: Any) -> str:
        if isinstance(raw, bool):
            return "yes" if raw else "no"
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("y", "yes", "true", "1", "yep", "yeah"):
                return "yes"
            if s in ("n", "no", "false", "0", "nope", "nah"):
                return "no"
            if s in ("unknown", "i don't know", "idk", "unclear", "maybe"):
                return "unknown"
        return "unknown"

    def _resolve_player_token(self, state: Any, token: Any) -> Optional[str]:
        if not isinstance(token, str) or not token.strip():
            return None
        token = token.strip().strip('"\'')
        if state.get_entity(token):
            return token
        if "(" in token and token.endswith(")"):
            inner = token[token.rfind("(") + 1:-1].strip()
            if state.get_entity(inner):
                return inner
        lower = token.lower()
        for e in state.entities.values():
            if e.entity_type != self._player_type:
                continue
            if (e.name.lower() == lower
                    or e.name.lower().split()[0] == lower
                    or lower in e.name.lower()):
                return e.id
        return None

    # ------------------------------------------------------------------
    # Perception
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        is_master = entity_id == self._master_id
        is_insider = entity_id == self._insider_id

        roster = [{"id": p.id, "name": p.name} for p in self._alive_players(state)]
        master_ent = state.get_entity(self._master_id) if self._master_id else None

        guesses_open = self._question_round >= self._min_questions_before_guess
        guess_lock_note = (
            ""
            if guesses_open
            else (f" Guesses are LOCKED until at least "
                  f"{self._min_questions_before_guess} questions have been asked "
                  f"(currently {self._question_round}). Keep probing first.")
        )
        # Stage hint: explicit instruction with literal example.
        if self._stage == STAGE_ASK:
            if is_master:
                stage_hint = ("You're the Master. Wait — you only answer when a "
                              "question gets put to you. (Use discuss for table talk.)")
            else:
                example_q = 'Is it alive?'
                stage_hint = (f'Ask a sharp yes/no question with '
                              f'ask_question(text="{example_q}").'
                              + (' Or commit to a guess with guess_answer(guess="…") '
                                 'if you think you know the answer.'
                                 if guesses_open else "")
                              + guess_lock_note)
        elif self._stage == STAGE_ANSWER:
            if is_master:
                stage_hint = (f'You were asked: "{self._pending_question}". '
                              'Reply with answer_question(answer="yes" | "no" | "unknown"). '
                              'Be truthful.')
            else:
                stage_hint = ("Master is answering."
                              + (' You can slip in a guess via '
                                 'guess_answer(guess="…") if you\'re sure, or '
                                 'discuss to chime in.'
                                 if guesses_open else " Discuss to chime in.")
                              + guess_lock_note)
        elif self._stage == STAGE_DISCUSS:
            stage_hint = ("The noun was guessed. Now hunt the Insider — share "
                          "your reads via discuss before the sealed vote.")
        elif self._stage == STAGE_VOTE:
            stage_hint = ('Sealed vote. Use vote_insider(target="<player_id>") '
                          'to name who you think the Insider is.')
        else:
            stage_hint = ""

        out: Dict[str, Any] = {
            "stage": self._stage,
            "question_round": self._question_round,
            "max_questions": self._max_questions,
            "master_id": self._master_id,
            "master_name": master_ent.name if master_ent else None,
            "pending_asker_id": self._pending_asker_id,
            "pending_question": self._pending_question,
            "roster": roster,
            "i_am_master": is_master,
            "i_am_insider": is_insider,
            "stage_hint": stage_hint,
        }

        if is_master or is_insider:
            out["secret"] = self._secret
            out["your_role"] = "Master" if is_master else "Insider"
        else:
            out["your_role"] = "Commoner"

        return out
