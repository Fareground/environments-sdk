"""Channels: public rooms, private groups, direct messages and broadcasts between agents.

.. code-block:: json

    "mechanisms": {"chat": {"kind": "channels", "members": "citizen", "rooms": ["plaza"],
                            "groups": {"cabal": {"members": ["ana", "ben"], "title": "Night committee"}},
                            "broadcast": "mayor", "create_groups": true}}

Every message is one entry of the record ``<name>`` and reaches only its audience: a room's
entries go to everyone, a group's to its members at the moment it is posted, a direct message to
its two parties. Because delivery rides on the record's ``to``, the engine's own filters keep a
private message out of every other agent's news, views, ``$records`` and ``$events``.

State: the world prop ``<name>_groups`` ({group: {title, members, invited, owner}}) and each
member's private prop ``<name>_read`` ({channel: last message read}). Unread counts are messages
after the last one an agent read (``<name>_read``) or wrote in that channel. A message that
mentions ``@id`` (or ``@name``) wakes the mentioned agent, if it is part of the audience.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import MechanismError, effect_op, mechanism
from ..template import format_value
from ..world import Abort
from ._social import props, NAME, cache, config_of, eid, entity, ids, literal_name_check, only_use, require_type, single_use_check

__all__ = ["ChannelsConfig", "MAX_MENTIONS"]

KIND = "channels"
#: Most agents one message may wake through mentions.
MAX_MENTIONS = 8
_MENTION = re.compile(r"@([A-Za-z0-9_\-]{1,64})")
_BROADCAST = "broadcast"
_ACTS = ("say", "dm", "reply", "broadcast", "read", "create", "invite", "join", "leave")


class GroupSpec(BaseModel):
    """A private group declared up front."""

    model_config = ConfigDict(extra="forbid")

    members: List[str] = Field(default_factory=list, description="Ids of the starting members.")
    title: str = Field("", description="What the group is for, shown to its members.")


class ChannelsConfig(BaseModel):
    """Rooms, groups and direct messages among agents of one type."""

    model_config = ConfigDict(extra="forbid")

    members: str = Field(..., description="Agent type that chats (subtypes included).")
    rooms: List[str] = Field(default_factory=lambda: ["general"], description="Public rooms every member reads and writes.")
    groups: Dict[str, GroupSpec] = Field(default_factory=dict, description="Private groups: {id: {members: [ids], title}}. Only members read them.")
    dm: bool = Field(True, description="Members may message one another directly.")
    replies: bool = Field(True, description="Offer a reply tool for recent messages.")
    broadcast: Optional[str] = Field(None, description="Agent type that may broadcast to everyone (e.g. a moderator).")
    create_groups: bool = Field(False, description="Members may create private groups and invite others.")
    max_len: int = Field(500, ge=1, le=4000, description="Longest message, in characters.")
    per_turn: Optional[int] = Field(3, ge=1, description="Messages one agent may send per turn (per tool).")
    per_round: Optional[int] = Field(None, ge=1, description="Messages one agent may send per round (per tool).")
    mentions: bool = Field(True, description="@id or @name in a message wakes that agent (if it can read the message).")
    notify: bool = Field(True, description="Deliver messages as news; false keeps them in the inbox (unread counts, read tool).")
    read_limit: int = Field(10, ge=1, le=50, description="Messages the read tool shows.")
    recent: int = Field(15, ge=1, le=60, description="Recent messages offered to reply to.")
    keep: Optional[int] = Field(None, ge=1, description="Keep only the latest N messages.")
    stage: Optional[str] = Field(None, description="Offer the tools during this declared stage; default: a sequential stage named after the mechanism.")
    passes: int = Field(1, ge=1, le=50, description="Passes of the generated stage (agents with nothing new are skipped after the first).")


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------


def _use(world: Any, source: Optional[str]) -> Tuple[str, ChannelsConfig]:
    name = only_use(world, KIND, source)
    return name, config_of(world, name, KIND, ChannelsConfig)


def _groups(world: Any, name: str) -> Dict[str, Dict[str, Any]]:
    return world.props.get(f"{name}_groups") or {}


def _is_member(world: Any, config: ChannelsConfig, agent: Entity) -> bool:
    return agent.alive and world.is_a(agent.entity_type, config.members)


def _postable(world: Any, name: str, config: ChannelsConfig, agent: Entity) -> List[str]:
    if not _is_member(world, config, agent):
        return []
    return list(config.rooms) + [g for g, spec in _groups(world, name).items() if agent.id in spec["members"]]


def _key(entry: Mapping[str, Any], viewer_id: str) -> str:
    """The channel an entry belongs to, as ``viewer_id`` sees it."""
    kind = entry.get("kind")
    if kind == "dm":
        to = entry.get("to") or [""]
        return "@" + (to[0] if entry.get("author") == viewer_id else str(entry.get("author")))
    if kind == _BROADCAST:
        return _BROADCAST
    return str(entry.get("channel"))


def _visible(world: Any, name: str, viewer: Entity) -> List[Any]:
    """Entries ``viewer`` may read, oldest first.

    The record is append-only and its generated ``visible`` rule is "all" (delivery rides on
    ``to``), so a viewer's list stays valid until an entry is added or dropped. The cache holds the
    first and last entries themselves and compares identity, so a rolled-back post followed by a
    different one is never mistaken for the old list.
    """
    rows = world.records(name)
    if world.contract.records[name].visible != "all":
        found = cache(world, f"{name}:visible")
        if viewer.id not in found:
            found[viewer.id] = world.visible_records(name, viewer)
        return found[viewer.id]  # type: ignore[no-any-return]
    store: Dict[str, Any] = world.__dict__.setdefault(f"_channel_visible:{name}", {})
    stamp = (len(rows), rows[0] if rows else None, rows[-1] if rows else None)
    held = store.get(viewer.id)
    if held is None or held[0] != stamp[0] or held[1] is not stamp[1] or held[2] is not stamp[2]:
        held = (*stamp, [row for row in rows if world.entry_visible(name, row, viewer)])
        store[viewer.id] = held
    return held[3]  # type: ignore[no-any-return]


def _unread_counts(world: Any, name: str, viewer: Entity) -> Dict[str, int]:
    marks = props(viewer).get(f"{name}_read") or {}
    counts: Dict[str, int] = {}
    for entry in _visible(world, name, viewer):
        if entry.get("author") == viewer.id:
            continue
        key = _key(entry, viewer.id)
        if entry["seq"] > marks.get(key, 0):
            counts[key] = counts.get(key, 0) + 1
    return counts


def _readable(world: Any, name: str, config: ChannelsConfig, viewer: Entity) -> List[str]:
    keys = dict.fromkeys(_postable(world, name, config, viewer))
    for entry in _visible(world, name, viewer):
        keys.setdefault(_key(entry, viewer.id), None)
    return list(keys)


def _label(world: Any, key: str) -> str:
    if key.startswith("@"):
        other = world.entities.get(key[1:])
        return f"DM with {other.name if other is not None else key[1:]}"
    if key == _BROADCAST:
        return "broadcasts"
    return f"#{key}"


def _line(world: Any, entry: Mapping[str, Any], viewer: Entity) -> str:
    author = world.entities.get(entry.get("author"))
    who = "you" if author is not None and author.id == viewer.id else (author.name if author is not None else "someone")
    reply = f" (reply to [{entry['reply_to']}])" if entry.get("reply_to") else ""
    return f"[{entry['seq']}] {who}{reply}: {format_value(entry.get('text'))}"


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _agent(call: Call, index: int = 0) -> Entity:
    world: Any = call.scope.world
    found = world.entity(eid(call.arg(index), call.source))
    if found is None:
        raise ExprError(f"${call.name}: no entity {call.arg(index)!r}", call.source)
    return found  # type: ignore[no-any-return]


@function("channels(agent)", "Rooms and groups an agent may post in (channels mechanism).", min_args=1, max_args=1)
def _channels_fn(call: Call) -> List[str]:
    name, config = _use(call.scope.world, call.source)
    return _postable(call.scope.world, name, config, _agent(call))


@function("inbox_channels(agent)", "Every channel an agent can read: rooms, its groups and its direct-message threads (@id).",
          min_args=1, max_args=1)
def _inbox_channels_fn(call: Call) -> List[str]:
    name, config = _use(call.scope.world, call.source)
    return _readable(call.scope.world, name, config, _agent(call))


@function("unread(agent, channel?)", "Unread messages for an agent, in one channel (a room, group or @id) or in all.",
          min_args=1, max_args=2)
def _unread_fn(call: Call) -> int:
    name, _ = _use(call.scope.world, call.source)
    counts = _unread_counts(call.scope.world, name, _agent(call))
    channel = call.arg(1)
    return counts.get(str(channel), 0) if channel is not None else sum(counts.values())


@function("channel_log(agent, channel, n?)", "The latest n messages of a channel as the agent reads them (default: the read limit).",
          min_args=2, max_args=3)
def _channel_log_fn(call: Call) -> str:
    world: Any = call.scope.world
    name, config = _use(world, call.source)
    viewer = _agent(call)
    return _log(world, name, config, viewer, str(call.arg(1)), call.arg(2))


def _log(world: Any, name: str, config: ChannelsConfig, viewer: Entity, key: str, n: Any = None) -> str:
    limit = config.read_limit if n is None else n
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ExprError(f"$channel_log: n must be a whole number ≥ 1, got {limit!r}")
    rows = [e for e in _visible(world, name, viewer) if _key(e, viewer.id) == key][-limit:]
    if not rows:
        return f"{_label(world, key)}: no messages yet."
    return f"{_label(world, key)}:\n" + "\n".join(_line(world, e, viewer) for e in rows)


@function("recent_messages(agent, n?)", "Sequence numbers of the latest messages an agent can read and did not write.",
          min_args=1, max_args=2)
def _recent_fn(call: Call) -> List[int]:
    world: Any = call.scope.world
    name, config = _use(world, call.source)
    viewer = _agent(call)
    n = call.arg(1, config.recent)
    rows = [e["seq"] for e in _visible(world, name, viewer) if e.get("author") != viewer.id]
    return rows[-n:] if isinstance(n, int) and n > 0 else []


@function("groups(agent)", "Private groups the agent belongs to (channels mechanism).", min_args=1, max_args=1)
def _groups_fn(call: Call) -> List[str]:
    name, _ = _use(call.scope.world, call.source)
    agent_id = eid(call.arg(0), call.source)
    return [g for g, spec in _groups(call.scope.world, name).items() if agent_id in spec["members"]]


@function("invites(agent)", "Private groups the agent has been invited to and not joined.", min_args=1, max_args=1)
def _invites_fn(call: Call) -> List[str]:
    name, _ = _use(call.scope.world, call.source)
    agent_id = eid(call.arg(0), call.source)
    return [g for g, spec in _groups(call.scope.world, name).items() if agent_id in spec["invited"]]


@function("inbox(agent)", "The agent's channels with unread counts: [{channel, label, title, unread, status}].",
          min_args=1, max_args=1)
def _inbox_fn(call: Call) -> List[Dict[str, Any]]:
    world: Any = call.scope.world
    name, config = _use(world, call.source)
    viewer = _agent(call)
    counts = _unread_counts(world, name, viewer)
    groups = _groups(world, name)
    items: List[Dict[str, Any]] = []
    for key in _readable(world, name, config, viewer):
        unread = counts.get(key, 0)
        spec = groups.get(key)
        label = f"group {key} ({len(spec['members'])} members)" if spec is not None else _label(world, key)
        items.append({"channel": key, "label": label, "title": spec["title"] if spec is not None else "",
                      "unread": unread, "status": f"{unread} unread" if unread else "all read"})
    for key, spec in groups.items():
        if viewer.id in spec["invited"]:
            owner = world.entities.get(spec.get("owner") or "")
            items.append({"channel": key, "label": f"invitation to group {key}" + (f" from {owner.name}" if owner else ""),
                          "title": spec["title"], "unread": 0, "status": "not joined"})
    return items


# ---------------------------------------------------------------------------
# The channel op
# ---------------------------------------------------------------------------


@effect_op("channel", keys=("act", "in", "to", "text", "message", "group", "who", "title", "author"),
           literal=("channel", "act"), required=("act",), check=literal_name_check(KIND, "channel"),
           example='{"channel": "chat", "act": "say", "in": "general", "text": "$params.text"}  '
                   "(acts: say, dm, reply, broadcast, read, create, invite, join, leave; `author` defaults to $actor)")
def _channel_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["channel"]
    config = config_of(world, name, KIND, ChannelsConfig)
    act = effect["act"]
    if act not in _ACTS:
        raise RunError(f"act must be one of {', '.join(_ACTS)}, got {act!r}", f"{where}.act")
    raw_author = runner.eval(effect["author"], vars) if "author" in effect else vars.get("actor")
    if raw_author is None:
        raise RunError("a channel act needs an author: run it in an action ($actor) or give `author`", where)
    author = entity(world, raw_author, where)

    def arg(key: str) -> Any:
        if key not in effect:
            raise RunError(f"act {act} needs `{key}`", where)
        return runner.eval(effect[key], vars)

    if act == "say":
        _say(world, name, config, author, str(arg("in")), arg("text"), None, where)
    elif act == "dm":
        _dm(world, name, config, author, entity(world, arg("to"), where), arg("text"), None, where)
    elif act == "reply":
        _reply(world, name, config, author, arg("message"), arg("text"), where)
    elif act == "broadcast":
        _broadcast(world, name, config, author, arg("text"), where)
    elif act == "read":
        _mark(world, name, author, str(arg("in")))
    elif act == "create":
        title = runner.eval(effect["title"], vars) if "title" in effect else ""
        _create(world, name, config, author, title, ids(runner.eval(effect.get("who"), vars), where), where)
    else:
        _membership(world, name, config, author, act, str(arg("group")),
                    entity(world, arg("who"), where) if act == "invite" else None)


def _text(config: ChannelsConfig, text: Any) -> Any:
    if not isinstance(text, str) or not text.strip():
        raise Abort("A message needs some text.")
    if len(text) > config.max_len:
        raise Abort(f"Messages are at most {config.max_len} characters; yours has {len(text)}.")
    return text


def _require_member(world: Any, config: ChannelsConfig, agent: Entity) -> None:
    if not _is_member(world, config, agent):
        raise Abort(f"{agent.name} is not a member of these channels.")


def _say(world: Any, name: str, config: ChannelsConfig, author: Entity, channel: str, text: Any,
         reply_to: Optional[int], where: str) -> None:
    _require_member(world, config, author)
    groups = _groups(world, name)
    if channel in config.rooms:
        audience = _all_members(world, config)
        _post(world, name, config, author, "room", channel, text, reply_to, None, audience, where)
    elif channel in groups:
        members = groups[channel]["members"]
        if author.id not in members:
            raise Abort(f"You are not a member of group {channel}.")
        _post(world, name, config, author, "group", channel, text, reply_to, tuple(members), members, where)
    else:
        raise Abort(f"There is no channel '{channel}' you can post in.")


def _dm(world: Any, name: str, config: ChannelsConfig, author: Entity, to: Entity, text: Any,
        reply_to: Optional[int], where: str) -> None:
    _require_member(world, config, author)
    if not config.dm:
        raise Abort("Direct messages are not allowed here.")
    if to.id == author.id or not _is_member(world, config, to):
        raise Abort(f"You cannot message {to.name} directly.")
    _post(world, name, config, author, "dm", "", text, reply_to, (to.id,), [to.id], where)


def _reply(world: Any, name: str, config: ChannelsConfig, author: Entity, message: Any, text: Any, where: str) -> None:
    seq = message if isinstance(message, int) and not isinstance(message, bool) else None
    entry = world.entry_by_seq.get(seq) if seq is not None else None
    if entry is None or entry not in world.records(name) or not world.entry_visible(name, entry, author):
        raise Abort(f"There is no message [{message}] you can reply to.")
    kind = entry.get("kind")
    if kind == "dm":
        other = entry["to"][0] if entry.get("author") == author.id else entry.get("author")
        _dm(world, name, config, author, entity(world, other, where), text, seq, where)
    elif kind == _BROADCAST:
        raise Abort("Broadcasts cannot be replied to; post in a room instead.")
    else:
        _say(world, name, config, author, str(entry.get("channel")), text, seq, where)


def _broadcast(world: Any, name: str, config: ChannelsConfig, author: Entity, text: Any, where: str) -> None:
    if config.broadcast is None or not world.is_a(author.entity_type, config.broadcast):
        raise Abort(f"{author.name} may not broadcast.")
    _post(world, name, config, author, _BROADCAST, "", text, None, None, _all_members(world, config), where)


def _all_members(world: Any, config: ChannelsConfig) -> List[str]:
    return [e.id for e in world.entities_of(config.members)]


def _post(world: Any, name: str, config: ChannelsConfig, author: Entity, kind: str, channel: str, text: Any,
          reply_to: Optional[int], to: Optional[Tuple[str, ...]], audience: List[str], where: str) -> None:
    body = _text(config, text)
    mentioned = _mentions(world, body, audience, author.id) if config.mentions else []
    entry = world.post(name, {"kind": kind, "channel": channel, "text": body, "reply_to": reply_to,
                              "mentions": mentioned}, author.id, to, where)
    key = _key(entry, author.id)
    _mark(world, name, author, key)
    place = {"dm": "a direct message", _BROADCAST: "a broadcast", "group": f"group {channel}"}.get(kind, _label(world, key))
    for target in mentioned:
        why = f"{author.name} mentioned you in {place} [{entry['seq']}]."
        world.request_wake(target, why)
        if not config.notify:
            world.emit("mention", why, actor=author.id, to=(target,), data={"mechanism": name, "message": entry["seq"]})


def _mentions(world: Any, text: str, audience: List[str], author_id: str) -> List[str]:
    """Audience members named as @id or @name (spaces in names written as _), author excluded."""
    tokens = _MENTION.findall(str.__str__(text))
    if not tokens:
        return []
    by_token: Dict[str, str] = {}
    for member_id in audience:
        member = world.entities.get(member_id)
        if member is None or not member.alive or member_id == author_id:
            continue
        by_token.setdefault(member_id.lower(), member_id)
        by_token.setdefault((member.name or "").replace(" ", "_").lower(), member_id)
    found = [by_token[t.lower()] for t in tokens if t.lower() in by_token]
    return list(dict.fromkeys(found))[:MAX_MENTIONS]


def _mark(world: Any, name: str, agent: Entity, key: str) -> None:
    """Mark ``key`` read for ``agent`` up to the newest message it can see there."""
    prop = f"{name}_read"
    if prop not in world.contract.props_of(agent.entity_type):
        return
    newest = 0
    for entry in reversed(_visible(world, name, agent)):
        if _key(entry, agent.id) == key:
            newest = entry["seq"]
            break
    marks = dict(props(agent).get(prop) or {})
    if newest > marks.get(key, 0):
        marks[key] = newest
        world.set_prop(agent, prop, marks)


def _create(world: Any, name: str, config: ChannelsConfig, owner: Entity, title: Any, invited: List[str], where: str) -> None:
    _require_member(world, config, owner)
    if not config.create_groups:
        raise Abort("Creating groups is not allowed here.")
    if title is not None and (not isinstance(title, str) or len(title) > 80):
        raise Abort("A group title is text of at most 80 characters.")
    groups = dict(_groups(world, name))
    n = len(groups) + 1
    while f"group_{n}" in groups or f"group_{n}" in config.rooms:
        n += 1
    group_id = f"group_{n}"
    guests = [g for g in invited if g != owner.id and (m := world.entities.get(g)) is not None and _is_member(world, config, m)]
    groups[group_id] = {"title": title or "", "members": [owner.id], "invited": guests, "owner": owner.id}
    world.set_world(f"{name}_groups", groups)
    world.emit("channel", f"You created group {group_id}.", actor=owner.id, to=(owner.id,), data={"mechanism": name, "group": group_id})
    for guest in guests:
        _invited(world, name, owner, guest, group_id, title or "")


def _invited(world: Any, name: str, host: Entity, guest: str, group: str, title: Any) -> None:
    about = f" ({format_value(title)})" if title else ""
    world.emit("channel", f"{host.name} invited you to group {group}{about}; join it to read and post there.",
               actor=host.id, to=(guest,), data={"mechanism": name, "group": group})


def _membership(world: Any, name: str, config: ChannelsConfig, agent: Entity, act: str, group: str,
                who: Optional[Entity]) -> None:
    groups = dict(_groups(world, name))
    spec = groups.get(group)
    if spec is None or (act != "join" and agent.id not in spec["members"]):
        raise Abort(f"You are not in a group '{group}'.")
    spec = {**spec, "members": list(spec["members"]), "invited": list(spec["invited"])}
    if act == "invite":
        assert who is not None
        if not _is_member(world, config, who) or who.id in spec["members"] or who.id in spec["invited"]:
            raise Abort(f"{who.name} cannot be invited to {group} (already in or invited, or not a member here).")
        spec["invited"].append(who.id)
        groups[group] = spec
        world.set_world(f"{name}_groups", groups)
        _invited(world, name, agent, who.id, group, spec["title"])
        return
    if act == "join":
        if agent.id not in spec["invited"]:
            raise Abort(f"You have no invitation to group {group}.")
        spec["invited"].remove(agent.id)
        spec["members"].append(agent.id)
        text = f"{agent.name} joined group {group}."
    else:
        spec["members"].remove(agent.id)
        text = f"{agent.name} left group {group}."
    groups[group] = spec
    world.set_world(f"{name}_groups", groups)
    world.emit("channel", text, actor=agent.id, to=tuple(dict.fromkeys(spec["members"] + [agent.id])),
               data={"mechanism": name, "group": group})


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


@mechanism(KIND, ChannelsConfig,
           "Rooms, private groups, direct messages and broadcasts: tools `<name>_say` (channel enum of the rooms and "
           "groups you are in), `<name>_dm`, `<name>_reply`, `<name>_read`, `<name>_broadcast`, and group tools. "
           "Messages are entries of the record `<name>` delivered only to their audience; @mentions wake the "
           "mentioned agent. Read state with $channels(agent), $unread(agent, channel?), $inbox(agent), "
           "$channel_log(agent, channel), $groups(agent), $invites(agent).",
           example={"kind": "channels", "members": "citizen", "rooms": ["plaza"],
                    "groups": {"council": {"members": ["ana", "ben"], "title": "Budget committee"}},
                    "per_turn": 2, "max_len": 400})
def _expand(name: str, config: ChannelsConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    single_use_check(KIND, contract)
    require_type(contract, config.members, "members", agent=True)
    require_type(contract, config.broadcast, "broadcast", agent=True)
    _check_names(config, contract)
    members = config.members
    rate = {k: v for k, v in (("per_turn", config.per_turn), ("per_round", config.per_round)) if v is not None}
    text = {"type": "text", "max_len": config.max_len, "description": f"Your message (at most {config.max_len} characters). Write @id to wake someone."}
    actions: Dict[str, Any] = {
        f"{name}_say": {"by": members, "description": "Post a message in a room or in a private group you belong to.",
                        "params": {"channel": {"type": "enum", "values": "$channels($actor)", "description": "Where to post."},
                                   "text": text},
                        "when": [{"expr": "$len($channels($actor)) > 0", "why": "You are in no room or group."}],
                        "do": [{"channel": name, "act": "say", "in": "$params.channel", "text": "$params.text"}],
                        "outcome": "Posted in {$params.channel}.", "private": True, **rate},
        f"{name}_read": {"by": members, "description": "Read the latest messages of one of your channels and mark it read.",
                         "params": {"channel": {"type": "enum", "values": "$inbox_channels($actor)",
                                                "description": "A room, a group, or @id for a direct-message thread."}},
                         "when": [{"expr": "$len($inbox_channels($actor)) > 0", "why": "You have no channels."}],
                         "do": [{"channel": name, "act": "read", "in": "$params.channel"}],
                         "outcome": "{$channel_log($actor, $params.channel)}", "private": True},
    }
    if config.dm:
        actions[f"{name}_dm"] = {"by": members, "description": "Send a private message only the recipient reads.",
                                 "params": {"to": {"type": "entity", "of": members,
                                                   "description": "Who receives it."}, "text": text},
                                 "do": [{"channel": name, "act": "dm", "to": "$params.to", "text": "$params.text"}],
                                 "outcome": "Message sent to {$params.to.name}.", "private": True, **rate}
    if config.replies:
        actions[f"{name}_reply"] = {"by": members, "description": "Reply to a recent message by its [number], in the same channel.",
                                    "params": {"message": {"type": "enum", "values": "$recent_messages($actor)",
                                                           "description": "The [number] of the message."}, "text": text},
                                    "when": [{"expr": "$len($recent_messages($actor)) > 0", "why": "There is nothing to reply to."}],
                                    "do": [{"channel": name, "act": "reply", "message": "$params.message", "text": "$params.text"}],
                                    "outcome": "Replied to [{$params.message}].", "private": True, **rate}
    if config.broadcast:
        actions[f"{name}_broadcast"] = {"by": config.broadcast, "description": "Broadcast a message every member reads.",
                                        "params": {"text": text},
                                        "do": [{"channel": name, "act": "broadcast", "text": "$params.text"}],
                                        "outcome": "Broadcast sent.", "private": True, **rate}
    if config.create_groups or config.groups:
        actions.update(_group_actions(name, config))
    reader_types = [members] + ([config.broadcast] if config.broadcast and config.broadcast != members else [])
    fragment: Dict[str, Any] = {
        "types": {t: {"props": {f"{name}_read": {"type": "map", "default": {}, "private": True,
                                                  "description": "Last message read per channel."}}} for t in reader_types},
        "world": {f"{name}_groups": {"type": "map", "default": _initial_groups(config),
                                     "description": "Private groups: members, invitations, owner."}},
        "records": {name: {"description": "Messages in rooms, private groups and direct messages.",
                           "fields": {"kind": "text", "channel": "text", "text": "text", "reply_to": "int", "mentions": "list"},
                           "show": _SHOW, "notify": config.notify, **({"keep": config.keep} if config.keep else {})}},
        "actions": actions,
        "views": {f"{name}_inbox": {"for": members, "title": "Your channels", "of": "$inbox($actor)",
                                    "show": "{label}{$' — ' if $it.title else ''}{$it.title or ''} · {status}",
                                    "empty": "You are in no channels."}},
    }
    names = list(actions)
    if config.stage is None:
        fragment["stages"] = [{"name": name, "turns": "sequential", "actions": names, "quiet": "skip",
                               "passes": config.passes, "max_actions": max(1, config.per_turn or 3) + 1}]
    else:
        fragment["stage_hooks"] = {config.stage: {"actions": names}}
    return fragment


_SHOW = ("[{seq}] {$'#' + $it.channel if $it.kind == 'room' else ($'group ' + $it.channel if $it.kind == 'group' "
         "else ($'direct message' if $it.kind == 'dm' else 'broadcast'))} · {author}"
         "{$' (reply to [' + $text($it.reply_to) + '])' if $it.reply_to else ''}: {text}")


def _group_actions(name: str, config: ChannelsConfig) -> Dict[str, Any]:
    members = config.members
    actions: Dict[str, Any] = {
        f"{name}_invite": {"by": members, "description": "Invite someone to a private group you belong to.",
                           "params": {"group": {"type": "enum", "values": "$groups($actor)"},
                                      "who": {"type": "entity", "of": members}},
                           "when": [{"expr": "$len($groups($actor)) > 0", "why": "You are in no group."}],
                           "do": [{"channel": name, "act": "invite", "group": "$params.group", "who": "$params.who"}],
                           "outcome": "Invited {$params.who.name} to {$params.group}.", "private": True},
        f"{name}_join": {"by": members, "description": "Join a private group you were invited to.",
                         "params": {"group": {"type": "enum", "values": "$invites($actor)"}},
                         "when": [{"expr": "$len($invites($actor)) > 0", "why": "You have no invitations."}],
                         "do": [{"channel": name, "act": "join", "group": "$params.group"}],
                         "outcome": "You joined {$params.group}.", "private": True},
        f"{name}_leave": {"by": members, "description": "Leave a private group.",
                          "params": {"group": {"type": "enum", "values": "$groups($actor)"}},
                          "when": [{"expr": "$len($groups($actor)) > 0", "why": "You are in no group."}],
                          "do": [{"channel": name, "act": "leave", "group": "$params.group"}],
                          "outcome": "You left {$params.group}.", "private": True},
    }
    if config.create_groups:
        actions[f"{name}_create_group"] = {
            "by": members, "description": "Create a private group and invite members to it.",
            "params": {"title": {"type": "text", "max_len": 80, "default": "", "description": "What the group is for."},
                       "invite": {"type": "list", "of": members, "max_items": 20,
                                  "default": [], "description": "Who to invite."}},
            "per_round": 1,
            "do": [{"channel": name, "act": "create", "title": "$params.title", "who": "$params.invite"}],
            "outcome": "Group created.", "private": True}
    return actions


def _initial_groups(config: ChannelsConfig) -> Dict[str, Any]:
    return {g: {"title": spec.title, "members": list(dict.fromkeys(spec.members)), "invited": [], "owner": None}
            for g, spec in config.groups.items()}


def _check_names(config: ChannelsConfig, contract: Mapping[str, Any]) -> None:
    seen: Dict[str, str] = {}
    for kind, names in (("rooms", config.rooms), ("groups", list(config.groups))):
        for channel in names:
            if not NAME.match(channel) or channel == _BROADCAST:
                raise MechanismError(f"'{channel}' is not a valid channel id", "use letters, digits and _, starting with a letter",
                                     kind)
            if channel in seen:
                raise MechanismError(f"channel '{channel}' is declared twice", "give every room and group its own id", kind)
            seen[channel] = kind
    entities = contract.get("entities") or {}
    for group, spec in config.groups.items():
        for member in spec.members:
            if entities and member not in entities and not contract.get("population"):
                raise MechanismError(f"group {group}: '{member}' is not a declared entity", None, f"groups.{group}.members")
