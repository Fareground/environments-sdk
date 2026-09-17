"""Mafia / Werewolf domain module.

Classic ruleset:
  - Hidden roles assigned at start: Mafia (minority), and on the Town side
    Detective, Doctor, plain Villagers.
  - One game-day = three engine rounds:
      round %% 3 == 1   → NIGHT  (mafia kill, detective investigates, doctor protects)
      round %% 3 == 2   → DAY    (everyone discusses freely)
      round %% 3 == 0   → VOTE   (everyone votes to lynch)
  - Win:
      Town wins when zero mafia remain alive.
      Mafia wins when mafia >= town (parity).

Engine wiring:
  * Roles live in state.roles (mafia/detective/doctor/villager + teams).
  * The day-vote uses state.polls (one open plurality poll per game-day).
  * Phase-style gating done via filter_valid_actions on round %% 3.
  * Night & vote phases SHOULD be set to resolution_mode="simultaneous"
    on the template so no agent acts on info they shouldn't see —
    the engine handles that automatically when the phase is configured.
"""
from __future__ import annotations

import logging
import random
from collections import Counter
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule
from fg_env.roles import Role

logger = logging.getLogger(__name__)


# The three "logical phases" of a single game-day, mapped onto rounds.
PHASE_NIGHT = "night"
PHASE_DAY = "day"
PHASE_VOTE = "vote"
PHASES_IN_ORDER = [PHASE_NIGHT, PHASE_DAY, PHASE_VOTE]


def _phase_for_round(round_number: int) -> str:
    """Map an engine round number to one of night / day / vote."""
    if round_number < 1:
        return PHASE_NIGHT
    return PHASES_IN_ORDER[(round_number - 1) % 3]


def _game_day_number(round_number: int) -> int:
    """Game-day index (1, 2, 3 …) for a given round."""
    return ((round_number - 1) // 3) + 1


class MafiaModule(DomainModule):
    """Drives a Mafia/Werewolf match."""

    def __init__(self, name: str = "mafia", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0xBADF00D))
        self._bootstrapped = False
        # detective per-round results: {detective_id: {round: [{target, verdict}, ...]}}
        self._investigations: Dict[str, Dict[int, List[Dict[str, str]]]] = {}
        # Track when we last ticked so we resolve each round exactly once.
        self._last_round_resolved: int = 0
        # Win flag — once set, further phase resolution is skipped.
        self._game_over: bool = False

    @property
    def description(self) -> str:
        return "Mafia / Werewolf: hidden roles, night kills, day deliberation, voting."

    @property
    def custom_actions(self) -> List[str]:
        return ["kill", "sk_kill", "investigate", "protect", "discuss", "vote"]

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _alive_players(self, state: Any) -> List[Any]:
        return [
            e for e in state.entities.values()
            if e.entity_type == self._player_type and e.alive
        ]

    def _all_players(self, state: Any) -> List[Any]:
        return [e for e in state.entities.values() if e.entity_type == self._player_type]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return
        # Roles + teams. Three factions:
        #   - mafia: coordinate to kill town
        #   - town:  sheriff, doctor, citizens (villagers)
        #   - serial_killer: solo faction — kills independently, wins by
        #                    being the last threat standing
        state.roles.define_role(Role(
            name="mafia", team="mafia", sees_teammates=True,
            description=("You are a member of the Mafia. Each night your team picks one "
                         "player to kill. Win when mafia equals or outnumbers all other "
                         "alive players."),
        ))
        state.roles.define_role(Role(
            name="sheriff", team="town", sees_teammates=False,
            description=("You are the Sheriff. Each night you may investigate one "
                         "player and learn whether they are Mafia. Win when all mafia "
                         "AND the serial killer are eliminated."),
        ))
        state.roles.define_role(Role(
            name="doctor", team="town", sees_teammates=False,
            description=("You are the Doctor. Each night you may protect one player "
                         "from being killed. Win when all mafia AND the serial killer "
                         "are eliminated."),
        ))
        state.roles.define_role(Role(
            name="citizen", team="town", sees_teammates=False,
            description=("You are a Citizen. You have no special power. Win when all "
                         "mafia AND the serial killer are eliminated through voting."),
        ))
        state.roles.define_role(Role(
            name="serial_killer", team="serial_killer", sees_teammates=False,
            description=("You are the Serial Killer. Each night you kill one player on "
                         "your own. You win by being the last threat standing — when "
                         "all mafia are eliminated and only you remain alongside at "
                         "most one helpless town."),
        ))

        players = self._alive_players(state)
        n = len(players)
        if n < 4:
            logger.warning(f"Mafia: only {n} players — game cannot function with < 4.")
            self._bootstrapped = True
            return

        # Role counts:
        #   1) Explicit per-match overrides (`mafia_count`, `sheriff_count`,
        #      `doctor_count`, `serial_killer_count`, `citizen_count`)
        #      take priority — the match creator gets exactly what they
        #      asked for.
        #   2) Otherwise scale around the canonical 12-player balance:
        #      3 mafia / 1 sheriff / 1 doctor / 1 SK / 6 citizens.
        p = self._params or {}
        explicit = any(
            f"{r}_count" in p
            for r in ("mafia", "sheriff", "doctor", "serial_killer", "citizen")
        )
        if explicit:
            target = {
                "mafia": int(p.get("mafia_count", 0) or 0),
                "sheriff": int(p.get("sheriff_count", 0) or 0),
                "doctor": int(p.get("doctor_count", 0) or 0),
                "serial_killer": int(p.get("serial_killer_count", 0) or 0),
            }
            citizens = int(p.get("citizen_count", -1))
            if citizens < 0:
                citizens = max(0, n - sum(target.values()))
            target["citizen"] = citizens
        else:
            target = {
                "mafia": max(1, round(n * 0.25)),
                "sheriff": 1 if n >= 5 else 0,
                "doctor": 1 if n >= 6 else 0,
                "serial_killer": 1 if n >= 8 else 0,
            }
            used = sum(target.values())
            target["citizen"] = max(0, n - used)

        # Clamp to the actual number of seated players so we never assign
        # more roles than people. Trim from the bottom (citizens first) if
        # over-allocated; trim from the most-numerous role otherwise.
        total = sum(target.values())
        if total > n:
            # Reduce citizens first, then SK / doctor / sheriff / mafia.
            for role in ("citizen", "serial_killer", "doctor", "sheriff", "mafia"):
                while target.get(role, 0) > 0 and sum(target.values()) > n:
                    target[role] -= 1
        elif total < n:
            target["citizen"] = target.get("citizen", 0) + (n - total)

        # Ensure at least one mafia, otherwise the game can't progress.
        if target["mafia"] == 0 and n >= 4:
            if target["citizen"] > 0:
                target["citizen"] -= 1
                target["mafia"] += 1

        # Random assignment — shuffle the player list and slice.
        shuffled = list(players)
        self._rng.shuffle(shuffled)

        i = 0
        for role_name in ("mafia", "sheriff", "doctor", "serial_killer", "citizen"):
            for _ in range(target.get(role_name, 0)):
                if i >= len(shuffled):
                    break
                state.roles.assign(shuffled[i].id, role_name)
                shuffled[i].set("status", "alive")
                i += 1

        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        ent = state.get_entity(entity_id)
        if not ent or not ent.alive:
            return []
        if self._game_over:
            return []
        role = state.roles.get_role_name(entity_id)
        phase = _phase_for_round(state.temporal.current_round)

        if phase == PHASE_NIGHT:
            if role == "mafia":
                return [a for a in valid_actions if a == "kill"]
            if role == "serial_killer":
                return [a for a in valid_actions if a == "sk_kill"]
            if role == "sheriff":
                return [a for a in valid_actions if a == "investigate"]
            if role == "doctor":
                return [a for a in valid_actions if a == "protect"]
            return []  # Citizens sleep.

        if phase == PHASE_DAY:
            return [a for a in valid_actions if a == "discuss"]

        if phase == PHASE_VOTE:
            return [a for a in valid_actions if a == "vote"]

        return valid_actions

    # ------------------------------------------------------------------
    # Tick — runs at the start of each round. Resolve previous round,
    # then prepare current round (open voting poll etc).
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if not self._bootstrapped:
            return []
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []

        # If a previous round just finished, resolve it now.
        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            prev_phase = _phase_for_round(prev)
            if prev_phase == PHASE_NIGHT:
                events.extend(self._resolve_night(state, prev))
            elif prev_phase == PHASE_VOTE:
                events.extend(self._resolve_vote(state, prev))
            self._last_round_resolved = prev

        # Check win conditions after any resolution.
        win = self._check_victory(state, round_number)
        if win:
            events.append(win)
            self._game_over = True
            return events

        # Prepare current round.
        cur_phase = _phase_for_round(round_number)
        if cur_phase == PHASE_VOTE:
            poll_id = f"day_vote_r{round_number}"
            if state.polls.get(poll_id) is None:
                alive = [p.id for p in self._alive_players(state)]
                if len(alive) > 1:
                    state.polls.open_poll(
                        poll_id=poll_id,
                        eligible_voters=alive,
                        options=alive,
                        rule="plurality",
                        allow_abstain=True,
                        description=f"Day {_game_day_number(round_number)}: vote to lynch.",
                        round_opened=round_number,
                    )
                    events.append({
                        "event_type": "mafia_vote_opened",
                        "narrative": f"The town gathers to vote (Day {_game_day_number(round_number)}).",
                        "data": {"poll_id": poll_id, "eligible": alive,
                                 "day": _game_day_number(round_number)},
                    })

        # Announce the phase so the UI / log carries it cleanly.
        events.append({
            "event_type": "mafia_phase",
            "narrative": {
                PHASE_NIGHT: f"Night {_game_day_number(round_number)} falls. The town sleeps.",
                PHASE_DAY: f"Day {_game_day_number(round_number)} breaks. Discussion begins.",
                PHASE_VOTE: f"The town gathers to vote.",
            }[cur_phase],
            "data": {
                "phase": cur_phase,
                "day": _game_day_number(round_number),
                "round": round_number,
            },
        })

        return events

    def _check_victory(self, state: Any, round_number: int) -> Optional[Dict[str, Any]]:
        alive = self._alive_players(state)
        roles_alive = [state.roles.get_role_name(p.id) for p in alive]
        mafia_alive = sum(1 for r in roles_alive if r == "mafia")
        sk_alive = sum(1 for r in roles_alive if r == "serial_killer")
        town_alive = sum(1 for r in roles_alive if r and r in ("sheriff", "doctor", "citizen"))

        # 1. Town wins only when BOTH mafia and the serial killer are out.
        if mafia_alive == 0 and sk_alive == 0 and (town_alive > 0):
            town_winners = [p.id for p in alive
                            if state.roles.get_role_name(p.id) in ("sheriff", "doctor", "citizen")]
            return {
                "event_type": "town_victory",
                "narrative": "Town wins! Both the Mafia and the Serial Killer are gone.",
                "data": {"round": round_number, "town_alive": town_alive,
                         "winning_team": "town", "winners": town_winners},
            }

        # 2. Serial killer wins when they're the only threat AND can mop up.
        # Concretely: all mafia dead AND no town remains (SK already
        # finished them), OR SK is alive with at most 1 town left and no
        # mafia (they'd be killed next night with no doctor saves matter
        # because doctor would be dead too — keep the simple endgame
        # check below for >1 town).
        if mafia_alive == 0 and sk_alive > 0 and town_alive == 0:
            sk_id = next((p.id for p in alive if state.roles.get_role_name(p.id) == "serial_killer"), None)
            return {
                "event_type": "serial_killer_victory",
                "narrative": "The Serial Killer wins — all rivals lie dead.",
                "data": {"round": round_number, "winning_team": "serial_killer",
                         "winners": [sk_id] if sk_id else []},
            }

        # 3. Mafia wins when they reach parity over EVERYONE else.
        if mafia_alive > 0 and mafia_alive >= (town_alive + sk_alive):
            mafia_winners = [p.id for p in alive if state.roles.get_role_name(p.id) == "mafia"]
            return {
                "event_type": "mafia_victory",
                "narrative": "Mafia wins! They have taken control of the town.",
                "data": {"round": round_number, "mafia_alive": mafia_alive,
                         "town_alive": town_alive, "sk_alive": sk_alive,
                         "winning_team": "mafia", "winners": mafia_winners},
            }

        return None

    # ------------------------------------------------------------------
    # Night resolution — apply kill (respecting doctor's protection)
    # ------------------------------------------------------------------

    def _resolve_night(self, state: Any, night_round: int) -> List[Dict[str, Any]]:
        # Gather mafia kill picks (team plurality)
        picks: List[str] = []
        for m in state.entities.values():
            if (m.entity_type == self._player_type and m.alive
                    and state.roles.get_role_name(m.id) == "mafia"):
                t = m.get(f"_kill_target_r{night_round}")
                if t:
                    picks.append(t)
        mafia_target = Counter(picks).most_common(1)[0][0] if picks else None

        # Serial killer pick (solo)
        sk_target: Optional[str] = None
        for s in state.entities.values():
            if (s.entity_type == self._player_type and s.alive
                    and state.roles.get_role_name(s.id) == "serial_killer"):
                t = s.get(f"_sk_kill_target_r{night_round}")
                if t:
                    sk_target = t
                    break

        # Doctor protections
        protected: set = set()
        for d in state.entities.values():
            if (d.entity_type == self._player_type and d.alive
                    and state.roles.get_role_name(d.id) == "doctor"):
                p = d.get(f"_protect_target_r{night_round}")
                if p:
                    protected.add(p)

        events: List[Dict[str, Any]] = []
        kills_resolved: List[Dict[str, Any]] = []

        def _try_kill(target_id: Optional[str], killer: str):
            if not target_id:
                return None
            if target_id in protected:
                return {"target_id": target_id, "killer": killer, "saved": True}
            victim = state.get_entity(target_id)
            if victim and victim.alive:
                victim.alive = False
                victim.set("status", "dead")
                victim.set("cause_of_death", killer)
                return {"target_id": target_id, "killer": killer, "victim_name": victim.name, "saved": False}
            return None

        # Apply both kills. They are independent, so if mafia and SK pick
        # different people, both die (assuming no doctor save).
        m_result = _try_kill(mafia_target, "mafia")
        s_result = _try_kill(sk_target, "serial_killer")

        for r, narr_killed, narr_saved in (
            (m_result, "killed by the mafia", "The Doctor saved the mafia's target"),
            (s_result, "murdered by a serial killer", "The Doctor saved the serial killer's target"),
        ):
            if r is None:
                continue
            if r["saved"]:
                events.append({
                    "event_type": "mafia_kill_resolved",
                    "target_id": r["target_id"],
                    "data": {"saved": True, "killer": r["killer"],
                             "night": night_round, "day": _game_day_number(night_round)},
                    "narrative": narr_saved + ".",
                })
            else:
                kills_resolved.append(r)
                events.append({
                    "event_type": "mafia_kill_resolved",
                    "target_id": r["target_id"],
                    "data": {"victim_name": r["victim_name"], "killer": r["killer"],
                             "saved": False, "night": night_round,
                             "day": _game_day_number(night_round)},
                    "narrative": f"{r['victim_name']} was {narr_killed} in the night.",
                })

        if not m_result and not s_result:
            events.append({
                "event_type": "mafia_kill_resolved",
                "data": {"victim_name": None, "night": night_round,
                         "day": _game_day_number(night_round)},
                "narrative": "Quiet night. No one died.",
            })
        return events

    # ------------------------------------------------------------------
    # Vote resolution
    # ------------------------------------------------------------------

    def _resolve_vote(self, state: Any, vote_round: int) -> List[Dict[str, Any]]:
        poll_id = f"day_vote_r{vote_round}"
        result = state.polls.close_poll(poll_id, round_closed=vote_round + 1)
        events: List[Dict[str, Any]] = []
        if not result:
            return events
        if result.winner:
            victim = state.get_entity(result.winner)
            if victim and victim.alive:
                victim.alive = False
                victim.set("status", "lynched")
                role = state.roles.get_role_name(result.winner) or "?"
                events.append({
                    "event_type": "mafia_lynch_resolved",
                    "target_id": result.winner,
                    "data": {
                        "victim_name": victim.name,
                        "victim_role": role,
                        "tally": result.tally,
                        "day": _game_day_number(vote_round),
                    },
                    "narrative": f"The town has spoken. {victim.name} ({role}) was lynched.",
                })
        elif result.tied:
            events.append({
                "event_type": "mafia_lynch_resolved",
                "data": {"victim_name": None, "tied": result.tied, "tally": result.tally,
                         "day": _game_day_number(vote_round)},
                "narrative": "The town is deadlocked. No lynch today.",
            })
        else:
            events.append({
                "event_type": "mafia_lynch_resolved",
                "data": {"victim_name": None, "day": _game_day_number(vote_round)},
                "narrative": "The vote ends inconclusively.",
            })
        return events

    # ------------------------------------------------------------------
    # Per-action handling — stash night intents; cast votes immediately
    # ------------------------------------------------------------------

    def validate_action(self, action_name: str, actor: Any, target: Any, state: Any) -> Optional[str]:
        if not actor or not getattr(actor, "alive", True):
            return "Dead players cannot act."
        if action_name in ("kill", "sk_kill", "investigate", "protect", "vote") and target is None:
            return f"{action_name} requires a target."
        if target is not None and not getattr(target, "alive", True):
            return "Target is already dead."
        role = state.roles.get_role_name(actor.id)
        if action_name == "kill" and role != "mafia":
            return "Only Mafia can kill."
        if action_name == "sk_kill" and role != "serial_killer":
            return "Only the Serial Killer can sk_kill."
        if action_name == "investigate" and role != "sheriff":
            return "Only the Sheriff can investigate."
        if action_name == "protect" and role != "doctor":
            return "Only the Doctor can protect."
        return None

    def post_resolution(
        self,
        actor_id: str,
        action_name: str,
        success: bool,
        result: Any,
        state: Any,
    ) -> List[Dict[str, Any]]:
        if action_name not in {"kill", "sk_kill", "investigate", "protect", "discuss", "vote"}:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        target_id = details.get("target_id") or details.get("_action_params", {}).get("target_id")
        changes: List[Dict[str, Any]] = []

        if action_name == "kill" and target_id:
            actor.set(f"_kill_target_r{round_number}", target_id)
            changes.append({
                "event_type": "mafia_kill_intent",
                "actor_id": actor_id,
                "target_id": target_id,
                "data": {"round": round_number},
                # Hide victim name from the public log — only mafia know.
                "narrative": "A Mafioso chooses their mark.",
            })

        elif action_name == "sk_kill" and target_id:
            actor.set(f"_sk_kill_target_r{round_number}", target_id)
            changes.append({
                "event_type": "mafia_sk_kill_intent",
                "actor_id": actor_id,
                "target_id": target_id,
                "data": {"round": round_number},
                "narrative": "A predator stalks in the dark.",
            })

        elif action_name == "investigate" and target_id:
            target_role = state.roles.get_role_name(target_id)
            verdict = "mafia" if target_role == "mafia" else "clear"
            self._investigations.setdefault(actor_id, {}).setdefault(round_number, []).append({
                "target": target_id, "verdict": verdict,
            })
            changes.append({
                "event_type": "mafia_investigation",
                "actor_id": actor_id,
                "target_id": target_id,
                "data": {"round": round_number, "verdict": verdict, "private_to": actor_id},
                # Public narrative says only that an investigation happened.
                "narrative": "The Detective examines a player in the night.",
            })

        elif action_name == "protect" and target_id:
            actor.set(f"_protect_target_r{round_number}", target_id)
            changes.append({
                "event_type": "mafia_protect_intent",
                "actor_id": actor_id,
                "target_id": target_id,
                "data": {"round": round_number},
                "narrative": "The Doctor stands watch.",
            })

        elif action_name == "vote":
            poll_id = f"day_vote_r{round_number}"
            chosen = target_id or "_abstain"
            ok = state.polls.cast_vote(poll_id, actor_id, chosen)
            if ok:
                # Vote is public — surface name and target so everyone
                # can react during the next day.
                target_name = None
                if target_id:
                    tgt = state.get_entity(target_id)
                    target_name = tgt.name if tgt else target_id
                changes.append({
                    "event_type": "mafia_vote_cast",
                    "actor_id": actor_id,
                    "target_id": target_id if target_id else None,
                    "data": {"round": round_number, "choice": chosen, "target_name": target_name},
                    "narrative": (
                        f"{actor.name} votes to lynch {target_name}." if target_name
                        else f"{actor.name} abstains."
                    ),
                })

        return changes

    # ------------------------------------------------------------------
    # Perception — surface role-specific private info
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        round_number = state.temporal.current_round
        phase = _phase_for_round(round_number)
        out: Dict[str, Any] = {
            "phase": phase,
            "day": _game_day_number(round_number),
            "alive_players": [
                {"id": p.id, "name": p.name}
                for p in self._alive_players(state)
            ],
            "dead_players": [
                {"id": e.id, "name": e.name,
                 "role_revealed": state.roles.get_role_name(e.id) if not e.alive else None,
                 "cause": e.get("status")}
                for e in state.entities.values()
                if e.entity_type == self._player_type and not e.alive
            ],
        }

        role_name = state.roles.get_role_name(entity_id)
        if role_name == "sheriff":
            own = self._investigations.get(entity_id, {})
            history = []
            for rnd, items in sorted(own.items()):
                for it in items:
                    tgt = state.get_entity(it["target"])
                    history.append({
                        "round": rnd,
                        "target_id": it["target"],
                        "target_name": tgt.name if tgt else it["target"],
                        "verdict": it["verdict"],
                    })
            if history:
                out["investigation_history"] = history
        return out
