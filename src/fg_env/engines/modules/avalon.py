"""Avalon (The Resistance: Avalon) domain module.

Ruleset:
  - 5–10 players. Hidden roles split into Good and Evil teams.
  - Roles:
      Good:  Merlin (knows Evil except Mordred), Percival (sees Merlin +
             Morgana indistinguishable), Loyal Servant of Arthur (no info)
      Evil:  Assassin (kills Merlin if Good wins 3), Morgana (looks like
             Merlin to Percival), Mordred (hidden from Merlin),
             plain Minion of Mordred. Evil (except Mordred) know each
             other.
  - 5 missions. Each mission proceeds:
      a. Leader proposes a team of N players.
      b. ALL players publicly vote approve / reject.
      c. If rejected, next leader proposes. 5 consecutive rejections in
         the same mission slot → Evil wins.
      d. If approved, team members secretly vote success / fail. Good
         MUST vote success; Evil may vote either. Most missions need just
         1 fail vote to fail; the 4th mission with 7+ players needs 2.
  - 3 successful missions → Good provisionally wins; Assassin gets one
    guess at who is Merlin. Correct → Evil steals the win. Wrong → Good
    wins.
  - 3 failed missions → Evil wins outright.

Engine wiring:
  * Single phase "playing" with resolution_mode="simultaneous" so sealed
    votes resolve atomically.
  * One stage per engine round:
      STAGE_PROPOSE      → leader uses propose_team; others discuss
      STAGE_TEAM_VOTE    → every alive player uses vote_team
      STAGE_MISSION      → proposed team members use vote_mission;
                           non-team players can discuss
      STAGE_ASSASSINATE  → Assassin uses assassinate(target)
  * Stage advanced inside tick() based on previous-round results.
"""
from __future__ import annotations

import logging
import random
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule
from fg_env.roles import Role

logger = logging.getLogger(__name__)


STAGE_PROPOSE = "propose"
STAGE_TEAM_VOTE = "team_vote"
STAGE_MISSION = "mission"
STAGE_ASSASSINATE = "assassinate"
STAGE_OVER = "over"


# Standard Avalon team sizes per (player_count, mission_index 0..4)
TEAM_SIZES: Dict[int, List[int]] = {
    5:  [2, 3, 2, 3, 3],
    6:  [2, 3, 4, 3, 4],
    7:  [2, 3, 3, 4, 4],
    8:  [3, 4, 4, 5, 5],
    9:  [3, 4, 4, 5, 5],
    10: [3, 4, 4, 5, 5],
}

# Missions requiring 2 fails to fail (0-indexed). Standard Avalon: mission 4 (index 3) for 7+ players.
TWO_FAIL_MISSIONS: Dict[int, List[int]] = {
    7: [3], 8: [3], 9: [3], 10: [3],
}

# Default evil count per player count (standard Avalon).
EVIL_COUNTS: Dict[int, int] = {5: 2, 6: 2, 7: 3, 8: 3, 9: 3, 10: 4}


class AvalonModule(DomainModule):
    """Drives an Avalon match."""

    def __init__(self, name: str = "avalon", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0xA17AEA))
        self._bootstrapped = False
        self._game_over = False

        # Per-mission state.
        self._mission_index: int = 0          # 0..4
        self._mission_results: List[str] = []  # "success" | "fail" per completed mission
        self._reject_streak: int = 0           # consecutive rejected proposals in current mission slot
        self._leader_index: int = 0            # rotates through seat order
        self._stage: str = STAGE_PROPOSE
        self._current_team: List[str] = []
        self._seat_order: List[str] = []
        self._last_round_resolved: int = 0
        # Track which evil roles are in play (for assassinate identification etc.)
        self._roles_in_play: List[str] = []

    @property
    def description(self) -> str:
        return ("Avalon: hidden-role social deduction. Good vs Evil, "
                "5 missions, sealed team approval + mission votes.")

    @property
    def custom_actions(self) -> List[str]:
        return ["propose_team", "vote_team", "vote_mission", "assassinate", "discuss"]

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _alive_players(self, state: Any) -> List[Any]:
        return [e for e in state.entities.values()
                if e.entity_type == self._player_type and e.alive]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return

        # Define roles. Teams: "good" / "evil".
        # sees_teammates is engine-level, but evil players don't ALL see each other
        # (Mordred hides from teammates? — no, only from Merlin). Keep it false here
        # and inject who-sees-who manually via get_perception_data.
        for role_name, team, desc in [
            ("merlin", "good", "You are Merlin. You know who all Evil players are EXCEPT Mordred. Help Good win — but don't get assassinated."),
            ("percival", "good", "You are Percival. You see Merlin and Morgana but can't tell which is which. Protect Merlin."),
            ("loyal", "good", "You are a Loyal Servant of Arthur. You know nothing — only that you're Good. Win 3 missions."),
            ("assassin", "evil", "You are the Assassin. You know your fellow Evil (except Mordred). If Good wins 3 missions, you get ONE guess at Merlin — if correct, Evil wins."),
            ("morgana", "evil", "You are Morgana. You appear as Merlin to Percival. Sow confusion. You know your fellow Evil (except Mordred)."),
            ("mordred", "evil", "You are Mordred. You are HIDDEN from Merlin. Use this — Merlin can't see you. You know your fellow Evil."),
            ("minion", "evil", "You are a Minion of Mordred. You know your fellow Evil (except Mordred). Sabotage missions and protect the Assassin's identity."),
        ]:
            state.roles.define_role(Role(name=role_name, team=team, sees_teammates=False, description=desc))

        players = self._alive_players(state)
        all_ents = [e for e in state.entities.values()]
        logger.info(
            f"[avalon setup] entities total={len(all_ents)} "
            f"types={sorted({e.entity_type for e in all_ents})} "
            f"alive_players={len(players)} "
            f"player_type={self._player_type!r}"
        )
        n = len(players)
        if n < 5:
            # Diagnostic: dump all entities so we can see why the count is low.
            for e in all_ents:
                logger.warning(
                    f"  ent id={e.id} type={e.entity_type!r} alive={getattr(e, 'alive', None)}"
                )
            logger.warning(f"Avalon: only {n} players — needs 5+. Game disabled.")
            self._bootstrapped = True
            self._game_over = True
            return
        n = min(n, 10)

        # Build role pack.
        p = self._params or {}
        def _truthy(v: Any, default: bool) -> bool:
            if v is None:
                return default
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("true", "1", "yes", "y", "on")
            return bool(v)
        include_percival = _truthy(p.get("include_percival"), True)
        include_morgana = include_percival  # Morgana only makes sense if Percival is in
        include_mordred = _truthy(p.get("include_mordred"), n >= 7)
        evil_target = EVIL_COUNTS[n]

        evil_roles: List[str] = ["assassin"]
        if include_morgana and len(evil_roles) < evil_target:
            evil_roles.append("morgana")
        if include_mordred and len(evil_roles) < evil_target:
            evil_roles.append("mordred")
        while len(evil_roles) < evil_target:
            evil_roles.append("minion")

        good_target = n - evil_target
        good_roles: List[str] = ["merlin"]
        if include_percival and len(good_roles) < good_target:
            good_roles.append("percival")
        while len(good_roles) < good_target:
            good_roles.append("loyal")

        all_roles = evil_roles + good_roles
        if len(all_roles) != n:
            logger.warning(f"Avalon role-count mismatch: {len(all_roles)} roles for {n} players")

        # Shuffle and assign.
        shuffled = list(players)
        self._rng.shuffle(shuffled)
        self._rng.shuffle(all_roles)
        for player, role_name in zip(shuffled, all_roles):
            state.roles.assign(player.id, role_name)
            role_obj = state.roles.roles.get(role_name)
            player.set("team", role_obj.team if role_obj else "")
            player.set("role_label", role_name)

        # Seat order (stable for leader rotation). Use entity order from state for
        # determinism — players list is already in insertion order.
        self._seat_order = [p.id for p in players[:n]]
        self._leader_index = self._rng.randrange(n)
        self._roles_in_play = list(all_roles)
        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _player_count(self) -> int:
        return len(self._seat_order)

    def _current_leader(self) -> Optional[str]:
        if not self._seat_order:
            return None
        return self._seat_order[self._leader_index % len(self._seat_order)]

    def _team_size_for_current_mission(self) -> int:
        n = self._player_count()
        if n in TEAM_SIZES and self._mission_index < 5:
            return TEAM_SIZES[n][self._mission_index]
        return 2

    def _fails_needed(self) -> int:
        n = self._player_count()
        if n in TWO_FAIL_MISSIONS and self._mission_index in TWO_FAIL_MISSIONS[n]:
            return 2
        return 1

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        if self._game_over:
            return []
        ent = state.get_entity(entity_id)
        if not ent or not ent.alive:
            return []

        if self._stage == STAGE_PROPOSE:
            if entity_id == self._current_leader():
                return [a for a in valid_actions if a in ("propose_team", "discuss")]
            return [a for a in valid_actions if a == "discuss"]

        # `discuss` is always a safe fallback so agents don't blow their
        # validation-retry budget when they pick a free-form utterance
        # instead of the canonical sealed action. The module auto-fills
        # missing sealed inputs in resolution.
        if self._stage == STAGE_TEAM_VOTE:
            return [a for a in valid_actions if a in ("vote_team", "discuss")]

        if self._stage == STAGE_MISSION:
            if entity_id in self._current_team:
                return [a for a in valid_actions if a in ("vote_mission", "discuss")]
            return [a for a in valid_actions if a == "discuss"]

        if self._stage == STAGE_ASSASSINATE:
            role = state.roles.get_role_name(entity_id)
            if role == "assassin":
                return [a for a in valid_actions if a in ("assassinate", "discuss")]
            return [a for a in valid_actions if a == "discuss"]

        return []

    # ------------------------------------------------------------------
    # Tick — advance stages between rounds
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []

        # Resolve previous round's stage if not yet handled.
        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            prev_stage = state.get_world_state().get("avalon_stage_in_round", {}).get(str(prev)) if False else None
            # Resolve based on the current persisted stage at the time the previous round ran.
            # We tracked it via self._stage being the stage that was just played.
            stage_played = self._stage
            if stage_played == STAGE_PROPOSE:
                events.extend(self._resolve_propose(state, prev))
            elif stage_played == STAGE_TEAM_VOTE:
                events.extend(self._resolve_team_vote(state, prev))
            elif stage_played == STAGE_MISSION:
                events.extend(self._resolve_mission(state, prev))
            elif stage_played == STAGE_ASSASSINATE:
                events.extend(self._resolve_assassinate(state, prev))
            self._last_round_resolved = prev

            # Check for game end after resolution.
            win = self._check_victory(state, round_number)
            if win:
                events.append(win)
                self._game_over = True
                self._stage = STAGE_OVER
                return events

        # Announce the current round's stage.
        events.append({
            "event_type": "avalon_stage",
            "narrative": self._stage_narrative(),
            "data": {
                "stage": self._stage,
                "round": round_number,
                "mission_index": self._mission_index,
                "mission_number": self._mission_index + 1,
                "leader_id": self._current_leader(),
                "team_size": self._team_size_for_current_mission(),
                "fails_needed": self._fails_needed(),
                "reject_streak": self._reject_streak,
                "mission_results": list(self._mission_results),
                "current_team": list(self._current_team),
            },
        })
        return events

    def _stage_narrative(self) -> str:
        mission_n = self._mission_index + 1
        if self._stage == STAGE_PROPOSE:
            leader = self._current_leader() or "?"
            return (f"Mission {mission_n} — proposal {self._reject_streak + 1}/5. "
                    f"Leader picks {self._team_size_for_current_mission()} player(s).")
        if self._stage == STAGE_TEAM_VOTE:
            return f"Mission {mission_n} — team approval vote."
        if self._stage == STAGE_MISSION:
            need = self._fails_needed()
            tail = "1 fail vote fails the mission." if need == 1 else f"{need} fail votes needed to fail the mission."
            return f"Mission {mission_n} — sealed success/fail vote. {tail}"
        if self._stage == STAGE_ASSASSINATE:
            return "Good has won 3 missions. The Assassin names their target — guess Merlin or lose."
        return ""

    # ------------------------------------------------------------------
    # Stage resolvers — called for the round that just ENDED
    # ------------------------------------------------------------------

    def _resolve_propose(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        leader_id = self._current_leader()
        proposal = []
        if leader_id:
            leader = state.get_entity(leader_id)
            if leader:
                proposal = leader.get(f"_propose_r{round_number}") or []

        events: List[Dict[str, Any]] = []
        expected = self._team_size_for_current_mission()

        # Defensive fallback: if leader didn't propose a valid team, pick deterministically.
        if not proposal or len(proposal) != expected or not self._valid_proposal(proposal, state):
            # Auto-fill: leader + first N-1 distinct alive players in seat order.
            alive_ids = [p.id for p in self._alive_players(state)]
            fallback = [leader_id] if leader_id in alive_ids else []
            for pid in alive_ids:
                if pid in fallback:
                    continue
                if len(fallback) >= expected:
                    break
                fallback.append(pid)
            proposal = fallback[:expected]
            events.append({
                "event_type": "avalon_propose_auto",
                "narrative": "Leader failed to propose a valid team; defaulting to seat-order team.",
                "data": {"team": proposal, "round": round_number},
            })

        self._current_team = proposal
        # Open the team-approval poll on state.polls for clean audit.
        poll_id = f"avalon_team_r{round_number}_m{self._mission_index}"
        alive_ids = [p.id for p in self._alive_players(state)]
        if state.polls.get(poll_id) is None and len(alive_ids) > 0:
            state.polls.open_poll(
                poll_id=poll_id,
                eligible_voters=alive_ids,
                options=["approve", "reject"],
                rule="plurality",
                allow_abstain=False,
                description=f"Mission {self._mission_index + 1} team approval",
                round_opened=round_number + 1,
            )

        leader_ent = state.get_entity(leader_id) if leader_id else None
        leader_name = leader_ent.name if leader_ent else leader_id or "?"
        team_names = []
        for pid in proposal:
            ent = state.get_entity(pid)
            team_names.append(ent.name if ent else pid)
        events.append({
            "event_type": "avalon_team_proposed",
            "narrative": f"{leader_name} proposes a team: {', '.join(team_names)}.",
            "data": {
                "leader_id": leader_id,
                "team": proposal,
                "team_names": team_names,
                "mission_index": self._mission_index,
                "attempt": self._reject_streak + 1,
            },
        })

        # Advance to team vote stage.
        self._stage = STAGE_TEAM_VOTE
        return events

    def _valid_proposal(self, proposal: List[str], state: Any) -> bool:
        if not proposal:
            return False
        seen = set()
        for pid in proposal:
            if pid in seen:
                return False
            seen.add(pid)
            ent = state.get_entity(pid)
            if not ent or not getattr(ent, "alive", True) or ent.entity_type != self._player_type:
                return False
        return True

    def _resolve_team_vote(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        votes: Dict[str, str] = {}
        for player in self._alive_players(state):
            v = player.get(f"_team_vote_r{round_number}")
            if v in ("approve", "reject"):
                votes[player.id] = v

        # Auto-fill any missing votes RANDOMLY (50/50) so a silent agent
        # doesn't tilt the table toward Evil hammer-win. Previously we
        # defaulted missing → reject which let an LLM hiccup snowball
        # into a 5-rejection Evil victory with stakes on the line.
        missing_ids: List[str] = []
        for player in self._alive_players(state):
            if player.id in votes:
                continue
            missing_ids.append(player.id)
            votes[player.id] = self._rng.choice(("approve", "reject"))
            player.set(f"_team_vote_r{round_number}", votes[player.id])

        approves = sum(1 for v in votes.values() if v == "approve")
        rejects = sum(1 for v in votes.values() if v == "reject")

        events: List[Dict[str, Any]] = []
        per_player = []
        for p in self._alive_players(state):
            v = votes[p.id]
            per_player.append({"id": p.id, "name": p.name, "vote": v, "missing": p.id in missing_ids})

        approved = approves > rejects
        events.append({
            "event_type": "avalon_team_vote_resolved",
            "narrative": (f"Team {('APPROVED' if approved else 'REJECTED')} — "
                          f"{approves} approve / {rejects} reject."),
            "data": {
                "approved": approved,
                "approves": approves,
                "rejects": rejects,
                "votes": per_player,
                "team": list(self._current_team),
                "mission_index": self._mission_index,
                "attempt": self._reject_streak + 1,
            },
        })

        if approved:
            self._reject_streak = 0
            self._stage = STAGE_MISSION
        else:
            self._reject_streak += 1
            self._leader_index = (self._leader_index + 1) % self._player_count()
            self._current_team = []
            if self._reject_streak >= 5:
                # Evil wins by hammer.
                events.append({
                    "event_type": "evil_victory",
                    "narrative": "Five proposals rejected in a row. Evil seizes power.",
                    "data": {
                        "winning_team": "evil",
                        "winners": [p.id for p in self._alive_players(state)
                                    if (state.roles.get_role(p.id) and
                                        state.roles.get_role(p.id).team == "evil")],
                        "reason": "hammer",
                        "mission_index": self._mission_index,
                    },
                })
                self._game_over = True
                self._stage = STAGE_OVER
            else:
                self._stage = STAGE_PROPOSE
        return events

    def _resolve_mission(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        # Tally mission votes — only team members vote.
        fails = 0
        successes = 0
        per_player = []
        for pid in self._current_team:
            p = state.get_entity(pid)
            if not p:
                continue
            raw = p.get(f"_mission_vote_r{round_number}")
            role_obj = state.roles.get_role(pid)
            team = role_obj.team if role_obj else "?"
            was_missing = raw not in ("success", "fail")
            # Fill missing / invalid votes:
            #   Good is FORCED to success (strict Avalon rule).
            #   Evil missing → coin flip — don't auto-sabotage on every
            #   silent turn; that would hand Evil free wins whenever an
            #   LLM hiccups during a mission.
            if was_missing:
                if team == "good":
                    v = "success"
                else:
                    v = self._rng.choice(("success", "fail"))
            else:
                v = raw
            # Strict Avalon: Good cannot vote fail.
            if team == "good" and v == "fail":
                v = "success"
            if v == "fail":
                fails += 1
            else:
                successes += 1
            per_player.append({"id": pid, "name": p.name, "vote": v, "missing": was_missing})

        need = self._fails_needed()
        mission_failed = fails >= need
        result = "fail" if mission_failed else "success"
        self._mission_results.append(result)

        events: List[Dict[str, Any]] = []
        events.append({
            "event_type": "avalon_mission_resolved",
            "narrative": (f"Mission {self._mission_index + 1} {result.upper()} — "
                          f"{successes} success / {fails} fail."),
            "data": {
                "result": result,
                "successes": successes,
                "fails": fails,
                "fails_needed": need,
                "team": list(self._current_team),
                "per_player": per_player,  # vote-per-team-member visible to nobody publicly except via UI
                "mission_index": self._mission_index,
                "mission_results": list(self._mission_results),
            },
        })

        # Advance to next mission or assassination.
        self._mission_index += 1
        self._reject_streak = 0
        self._current_team = []
        self._leader_index = (self._leader_index + 1) % self._player_count()

        good_wins = sum(1 for r in self._mission_results if r == "success")
        evil_wins = sum(1 for r in self._mission_results if r == "fail")

        if evil_wins >= 3:
            events.append({
                "event_type": "evil_victory",
                "narrative": "Evil has failed three missions. Camelot falls.",
                "data": {
                    "winning_team": "evil",
                    "winners": [p.id for p in self._alive_players(state)
                                if (state.roles.get_role(p.id) and
                                    state.roles.get_role(p.id).team == "evil")],
                    "mission_results": list(self._mission_results),
                },
            })
            self._game_over = True
            self._stage = STAGE_OVER
        elif good_wins >= 3:
            # Assassin gets a guess at Merlin.
            assassin_alive = any(
                state.roles.get_role_name(p.id) == "assassin"
                for p in self._alive_players(state)
            )
            if assassin_alive:
                self._stage = STAGE_ASSASSINATE
            else:
                # No assassin in play (e.g. someone removed evil role); Good wins.
                events.append(self._good_victory(state, killed_merlin=False))
                self._game_over = True
                self._stage = STAGE_OVER
        else:
            self._stage = STAGE_PROPOSE
        return events

    def _resolve_assassinate(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        assassin = None
        for p in self._alive_players(state):
            if state.roles.get_role_name(p.id) == "assassin":
                assassin = p
                break
        if not assassin:
            return [self._good_victory(state, killed_merlin=False)]
        target_id = assassin.get(f"_assassinate_r{round_number}")
        target_ent = state.get_entity(target_id) if target_id else None
        target_role = state.roles.get_role_name(target_id) if target_id else None
        killed_merlin = target_role == "merlin"

        events: List[Dict[str, Any]] = []
        events.append({
            "event_type": "avalon_assassinate",
            "narrative": (f"The Assassin names {target_ent.name if target_ent else '???'}. "
                          + ("They were Merlin." if killed_merlin else "They were NOT Merlin.")),
            "data": {
                "assassin_id": assassin.id,
                "target_id": target_id,
                "target_name": target_ent.name if target_ent else None,
                "target_role": target_role,
                "killed_merlin": killed_merlin,
            },
        })
        if killed_merlin:
            events.append({
                "event_type": "evil_victory",
                "narrative": "The Assassin found Merlin. Evil snatches victory.",
                "data": {
                    "winning_team": "evil",
                    "winners": [p.id for p in self._alive_players(state)
                                if (state.roles.get_role(p.id) and
                                    state.roles.get_role(p.id).team == "evil")],
                    "reason": "assassinated_merlin",
                    "mission_results": list(self._mission_results),
                },
            })
        else:
            events.append(self._good_victory(state, killed_merlin=False))
        self._game_over = True
        self._stage = STAGE_OVER
        return events

    def _good_victory(self, state: Any, killed_merlin: bool) -> Dict[str, Any]:
        winners = [p.id for p in self._alive_players(state)
                   if (state.roles.get_role(p.id) and
                       state.roles.get_role(p.id).team == "good")]
        return {
            "event_type": "good_victory",
            "narrative": "Good has won 3 missions and survived the Assassin.",
            "data": {
                "winning_team": "good",
                "winners": winners,
                "mission_results": list(self._mission_results),
            },
        }

    def _check_victory(self, state: Any, round_number: int) -> Optional[Dict[str, Any]]:
        # Victory events are emitted inline by resolvers; this is a safety net.
        return None

    # ------------------------------------------------------------------
    # Action recording (stash intents; resolution happens in tick)
    # ------------------------------------------------------------------

    def validate_action(self, action_name: str, actor: Any, target: Any, state: Any) -> Optional[str]:
        if not actor or not getattr(actor, "alive", True):
            return "Dead/missing actor."
        if self._game_over:
            return "Game over."
        return None

    def post_resolution(
        self,
        actor_id: str,
        action_name: str,
        success: bool,
        result: Any,
        state: Any,
    ) -> List[Dict[str, Any]]:
        if action_name not in {"propose_team", "vote_team", "vote_mission", "assassinate", "discuss"}:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        action_params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name == "propose_team":
            members = (
                action_params.get("members")
                or action_params.get("team")
                or action_params.get("players")
                or details.get("members")
                or []
            )
            # Also accept a single id submitted as target_id (common LLM mistake).
            target_id = details.get("target_id") or action_params.get("target_id")
            if isinstance(members, str):
                # Could be JSON-stringified array, or comma-separated, or single id.
                s = members.strip()
                if s.startswith("[") and s.endswith("]"):
                    try:
                        import json as _json
                        parsed = _json.loads(s)
                        if isinstance(parsed, list):
                            members = parsed
                        else:
                            members = [s]
                    except Exception:
                        members = [t.strip() for t in s.strip("[]").split(",") if t.strip()]
                else:
                    members = [t.strip().strip('"\'') for t in s.split(",") if t.strip()]
            if not isinstance(members, list):
                members = [members] if members else []

            # Normalize each token: id, name, or even "Name (p_xxx)" form.
            normalized: List[str] = []
            seen: set = set()
            for token in list(members) + ([target_id] if target_id else []):
                if not isinstance(token, str):
                    continue
                token = token.strip().strip('"\'')
                if not token:
                    continue
                # Direct entity id.
                if state.get_entity(token) and token not in seen:
                    normalized.append(token); seen.add(token); continue
                # Strip parenthetical id suffix: "Khachapuri (p_001)" → try id then name.
                m = None
                if "(" in token and token.endswith(")"):
                    inner = token[token.rfind("(") + 1:-1].strip()
                    if state.get_entity(inner):
                        m = inner
                        token = inner
                if not m:
                    # Name match (case-insensitive, allow partial first-word).
                    lower = token.lower()
                    hit = next(
                        (e.id for e in state.entities.values()
                         if e.entity_type == self._player_type
                         and (e.name.lower() == lower
                              or e.name.lower().split()[0] == lower
                              or lower in e.name.lower())),
                        None,
                    )
                    if hit:
                        m = hit
                if m and m not in seen:
                    normalized.append(m); seen.add(m)

            # Final fallback: if leader proposed nothing/garbage, include themselves
            # so the auto-fill has a sane anchor (and we don't bench the leader).
            if not normalized and actor_id == self._current_leader():
                normalized = [actor_id]

            actor.set(f"_propose_r{round_number}", normalized)
            # Make the intent VISIBLE in the public log so the user sees it
            # land even before stage resolution.
            names = []
            for pid in normalized:
                e = state.get_entity(pid)
                names.append(e.name if e else pid)
            events.append({
                "event_type": "avalon_propose_intent",
                "actor_id": actor_id,
                "data": {"team": normalized, "team_names": names, "round": round_number},
                "narrative": (
                    f"{actor.name} locks in: {', '.join(names)}."
                    if names else f"{actor.name} locks in an empty proposal."
                ),
            })

        elif action_name == "vote_team":
            raw = action_params.get("vote") or action_params.get("approve") or details.get("vote")
            v = self._coerce_approve(raw)
            actor.set(f"_team_vote_r{round_number}", v)
            # Sealed — don't broadcast individual votes mid-stage.
            events.append({
                "event_type": "avalon_team_vote_cast",
                "actor_id": actor_id,
                "data": {"round": round_number, "private_to": actor_id, "vote": v},
                "narrative": f"{actor.name} casts a sealed vote.",
            })

        elif action_name == "vote_mission":
            raw = action_params.get("vote") or action_params.get("success") or details.get("vote")
            v = self._coerce_mission(raw)
            role_obj = state.roles.get_role(actor_id)
            team = role_obj.team if role_obj else "?"
            # Strict rule enforced again at resolution; record raw intent here.
            if team == "good" and v == "fail":
                v = "success"
            actor.set(f"_mission_vote_r{round_number}", v)
            events.append({
                "event_type": "avalon_mission_vote_cast",
                "actor_id": actor_id,
                "data": {"round": round_number, "private_to": actor_id, "vote": v},
                "narrative": f"{actor.name} drops their sealed mission card.",
            })

        elif action_name == "assassinate":
            raw = action_params.get("target") or action_params.get("target_id") or details.get("target_id")
            target_id = None
            if isinstance(raw, str) and raw:
                if state.get_entity(raw):
                    target_id = raw
                else:
                    hit = next(
                        (e.id for e in state.entities.values()
                         if e.entity_type == self._player_type
                         and e.name.lower() == raw.lower()),
                        None,
                    )
                    target_id = hit
            actor.set(f"_assassinate_r{round_number}", target_id)
            events.append({
                "event_type": "avalon_assassinate_intent",
                "actor_id": actor_id,
                "data": {"target_id": target_id, "round": round_number},
                "narrative": f"{actor.name} prepares to name a target.",
            })

        return events

    @staticmethod
    def _coerce_approve(raw: Any) -> str:
        if isinstance(raw, bool):
            return "approve" if raw else "reject"
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("approve", "yes", "y", "true", "1", "accept", "pass"):
                return "approve"
            return "reject"
        return "reject"

    @staticmethod
    def _coerce_mission(raw: Any) -> str:
        if isinstance(raw, bool):
            return "success" if raw else "fail"
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("fail", "f", "false", "0", "sabotage"):
                return "fail"
            return "success"
        return "success"

    # ------------------------------------------------------------------
    # Perception — surface role-specific private info
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        my_role_obj = state.roles.get_role(entity_id)
        my_role = my_role_obj.name if my_role_obj else ""
        my_team = my_role_obj.team if my_role_obj else "?"

        # Roster — id+name pairs the LLM can copy/paste into propose_team.
        roster = [
            {"id": p.id, "name": p.name}
            for p in self._alive_players(state)
        ]
        leader_id = self._current_leader()
        leader_ent = state.get_entity(leader_id) if leader_id else None

        # Cheat sheet that surfaces the EXACT shape the agent_brain needs to
        # echo for the current stage. Keeps the LLM from guessing.
        stage_hint = ""
        if self._stage == STAGE_PROPOSE:
            if entity_id == leader_id:
                example_ids = [p["id"] for p in roster[:self._team_size_for_current_mission()]]
                import json as _json
                stage_hint = (
                    f"YOU ARE THE LEADER. Use propose_team with members as a JSON "
                    f"array of {self._team_size_for_current_mission()} player entity_ids. "
                    f"EXAMPLE: propose_team(members={_json.dumps(example_ids)})."
                )
            else:
                stage_hint = "You are NOT the leader. Use discuss to argue who the team should be."
        elif self._stage == STAGE_TEAM_VOTE:
            stage_hint = ('Cast vote_team(vote="approve") or vote_team(vote="reject"). '
                          'Sealed — no one sees individual votes until reveal.')
        elif self._stage == STAGE_MISSION:
            if entity_id in self._current_team:
                stage_hint = ('You are ON the team. Cast vote_mission(vote="success") '
                              'or vote_mission(vote="fail"). Good MUST vote success.')
            else:
                stage_hint = "You are NOT on the team. Use discuss while the team votes."
        elif self._stage == STAGE_ASSASSINATE:
            if my_role == "assassin":
                stage_hint = 'YOU ARE THE ASSASSIN. Pick assassinate(target="<player_id>") — guess Merlin.'
            else:
                stage_hint = "Wait — the Assassin is choosing who to kill."

        out: Dict[str, Any] = {
            "stage": self._stage,
            "mission_index": self._mission_index,
            "mission_number": self._mission_index + 1,
            "mission_results": list(self._mission_results),
            "reject_streak": self._reject_streak,
            "leader_id": leader_id,
            "leader_name": leader_ent.name if leader_ent else None,
            "i_am_leader": entity_id == leader_id,
            "team_size": self._team_size_for_current_mission(),
            "fails_needed": self._fails_needed(),
            "current_team": list(self._current_team),
            "my_role": my_role,
            "my_team": my_team,
            "seat_order": list(self._seat_order),
            "roster": roster,
            "stage_hint": stage_hint,
        }

        # Role-specific info.
        if my_role == "merlin":
            # Merlin sees all Evil except Mordred.
            seen = []
            for pid, role_name in self._iter_roles(state):
                if role_name and role_name != "mordred":
                    role_obj = state.roles.roles.get(role_name)
                    if role_obj and role_obj.team == "evil" and pid != entity_id:
                        ent2 = state.get_entity(pid)
                        seen.append({"id": pid, "name": ent2.name if ent2 else pid})
            out["evil_visible_to_merlin"] = seen
        elif my_role == "percival":
            # Percival sees Merlin + Morgana but can't distinguish them.
            candidates = []
            for pid, role_name in self._iter_roles(state):
                if role_name in ("merlin", "morgana") and pid != entity_id:
                    ent2 = state.get_entity(pid)
                    candidates.append({"id": pid, "name": ent2.name if ent2 else pid})
            self._rng_shuffle_view(candidates, entity_id)
            out["merlin_or_morgana"] = candidates
        elif my_team == "evil" and my_role != "oberon":
            # Evil (except Oberon, not implemented) see each other except Mordred-hides-rule:
            # Standard rule: Mordred IS visible to evil teammates (he only hides from Merlin).
            allies = []
            for pid, role_name in self._iter_roles(state):
                if not role_name or pid == entity_id:
                    continue
                role_obj = state.roles.roles.get(role_name)
                if role_obj and role_obj.team == "evil":
                    ent2 = state.get_entity(pid)
                    allies.append({"id": pid, "name": ent2.name if ent2 else pid, "role": role_name})
            out["evil_allies"] = allies

        return out

    def _iter_roles(self, state: Any):
        for e in state.entities.values():
            if e.entity_type == self._player_type:
                yield e.id, state.roles.get_role_name(e.id)

    def _rng_shuffle_view(self, items: List[Any], salt: str) -> None:
        # Stable shuffle keyed by the viewer so order stays consistent across rounds.
        rng = random.Random(hash((salt, "avalon_view")) & 0xFFFFFFFF)
        rng.shuffle(items)
