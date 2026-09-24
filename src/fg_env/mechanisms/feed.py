"""A social network: follows, friends, blocks and mutes; posts, replies, reposts and reactions;
ranked feeds, trending, reputation, influence and insularity.

.. code-block:: json

    "mechanisms": {"net": {"kind": "social", "mode": "feed", "who": "account", "reactions": ["like"],
                           "feed_size": 8, "moderators": "moderator", "labels": ["misleading"],
                           "downrank": {"labels": ["misleading"], "factor": 0.2}}}

Posts are entities of the type ``<name>_post`` (props: author, text, kind, parent, origin, born,
reacts, reactions, reposts, replies, labels, reacted, reposters). The network is ordinary
relations — ``<name>_follows`` (from follows to), ``<name>_friends`` (symmetric),
``<name>_requests``, ``<name>_blocks``, ``<name>_mutes`` — so ``links`` generators build the
starting graph and ``$neighbors``/``$linked`` read it.

A feed ranks recent posts (``window`` rounds) from accounts the viewer follows or befriended —
plus everyone else's with ``discover`` — by
``follows·followed + recency/(1+age) + engagement·ln(1+reacts+2·reposts+replies) + reputation·author``,
multiplied by ``downrank.factor`` when a post carries a downranked label. Blocked and muted
accounts never appear; nor do accounts that block the viewer. Ties go to the newer post.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ..world.entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import MechanismError, family_action, mode
from ..world.live import Abort
from ._common import ToolsSetting, tools_field
from ._social import props, cache, config_of, edges, eid, entity, named_use, require_type, seat_order

__all__ = ["FeedConfig", "feed"]

KIND = "social.feed"
#: Actions that change who follows, befriends, blocks or mutes whom (they take `account`).
_RELATING = ("follow", "unfollow", "befriend", "unfriend", "block", "unblock", "mute", "unmute")


class FeedWeights(BaseModel):
    """How much each signal counts when ranking a feed."""

    model_config = ConfigDict(extra="forbid")

    follows: float = Field(2.0, ge=0, description="Bonus for posts by accounts you follow or befriended.")
    recency: float = Field(1.0, ge=0, description="Weight of 1/(1+age in rounds).")
    engagement: float = Field(1.0, ge=0, description="Weight of ln(1 + reactions + 2·reposts + replies).")
    reputation: float = Field(0.5, ge=0, description="Weight of the author's reputation (0–1).")


class Downrank(BaseModel):
    """Moderation: labelled posts rank lower (factor 1 = no effect, 0 = hidden at the bottom)."""

    model_config = ConfigDict(extra="forbid")

    labels: List[str] = Field(default_factory=list, description="Labels that downrank a post.")
    factor: float = Field(1.0, ge=0, le=1, description="Score multiplier for a downranked post.")


class Reputation(BaseModel):
    """How engagement moves an author's reputation (clamped to 0–1)."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(0.5, ge=0, le=1)
    reaction: float = Field(0.005, description="Change per reaction received.")
    repost: float = Field(0.02, description="Change per repost received.")
    reply: float = Field(0.005, description="Change per reply received.")
    label: float = Field(-0.05, description="Change when a moderator labels one of your posts.")


class FeedConfig(BaseModel):
    """A social network among agents of one type."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that has an account (subtypes included).")
    follows: bool = Field(True, description="Offer follow and unfollow tools.")
    friends: bool = Field(False, description="Offer friend requests (mutual friendship).")
    block: bool = Field(True, description="Offer a block tool (blocked accounts vanish from each other's feeds).")
    mute: bool = Field(False, description="Offer a mute tool (muted accounts vanish from your feed).")
    posts: bool = Field(True, description="Offer a post tool.")
    replies: bool = Field(True, description="Offer a reply tool.")
    reposts: bool = Field(True, description="Offer a repost tool.")
    reactions: List[str] = Field(default_factory=lambda: ["like"], description="Reactions accounts may give; empty for none.")
    max_chars: int = Field(280, ge=1, le=4000, description="Longest post, in characters.")
    per_turn: Optional[int] = Field(1, ge=1, description="Posts, replies and reposts per turn (each).")
    feed_size: int = Field(8, ge=1, le=60, description="Posts in a feed.")
    window: int = Field(12, ge=1, description="Rounds a post stays eligible for feeds and trending.")
    discover: bool = Field(False, description="Feeds also rank posts from accounts the viewer does not follow.")
    weights: FeedWeights = Field(default_factory=FeedWeights)
    downrank: Downrank = Field(default_factory=Downrank)
    trending_size: int = Field(5, ge=1, le=60, description="Posts in the trending list.")
    moderators: Optional[str] = Field(None, description="Agent type that may label posts.")
    labels: List[str] = Field(default_factory=lambda: ["misleading"], description="Labels moderators may apply.")
    reputation: Reputation = Field(default_factory=Reputation)
    notify: List[Literal["follow", "reply", "repost", "reaction", "friend", "label"]] = Field(
        ["follow", "reply", "repost", "friend", "label"],
        description="What an account is told about: follow, reply, repost, reaction, friend, label.")
    turns: Literal["sequential", "simultaneous"] = Field("simultaneous", description="Turns of the generated stage.")
    stage: Optional[str] = Field(None, description="Offer the tools during this declared stage instead of a generated one.")
    tools: ToolsSetting = tools_field()


# ---------------------------------------------------------------------------
# Reading the network
# ---------------------------------------------------------------------------


def _use(call: Call, index: int) -> Tuple[str, FeedConfig]:
    name = named_use(call, KIND, index)
    return name, config_of(call.scope.world, name, KIND, FeedConfig)


def _out(world: Any, relation: str, account: str) -> List[str]:
    return edges(world, relation)[0].get(account, [])


def _in(world: Any, relation: str, account: str) -> List[str]:
    return edges(world, relation)[1].get(account, [])


def _has(world: Any, relation: str, a: str, b: str) -> bool:
    return world.relation(a, b, relation) is not None


def _connections(world: Any, name: str, config: FeedConfig, account: str) -> Set[str]:
    linked = set(_out(world, f"{name}_follows", account))
    if config.friends:
        linked |= set(_out(world, f"{name}_friends", account))
    return linked


def _recent_posts(world: Any, name: str, config: FeedConfig) -> List[Entity]:
    """Alive posts born within the window, newest first (cached per state)."""
    found = cache(world, f"{name}:recent")
    if "posts" not in found:
        oldest = world.round - config.window
        posts = [p for p in world.entities_of(f"{name}_post") if props(p).get("born", 0) >= oldest]
        order = seat_order(world)
        posts.sort(key=lambda p: -order.get(p.id, 0))
        found["posts"] = posts
    return found["posts"]  # type: ignore[no-any-return]


def _downranked(config: FeedConfig, post: Entity) -> bool:
    labels = props(post).get("labels") or []
    return bool(config.downrank.labels) and any(label in config.downrank.labels for label in labels)


def _engagement(post: Entity) -> int:
    values = props(post)
    return int(values.get("reacts", 0)) + 2 * int(values.get("reposts", 0)) + int(values.get("replies", 0))


def feed(world: Any, name: str, config: FeedConfig, viewer: Entity, n: Optional[int] = None) -> List[Entity]:
    """The ranked feed ``viewer`` reads now."""
    size = config.feed_size if n is None else n
    found = cache(world, f"{name}:feed")
    key = (viewer.id, size)
    if key in found:
        return list(found[key])
    follows = _connections(world, name, config, viewer.id)
    hidden = set(_out(world, f"{name}_blocks", viewer.id)) | set(_in(world, f"{name}_blocks", viewer.id))
    if config.mute:
        hidden |= set(_out(world, f"{name}_mutes", viewer.id))
    weights, order = config.weights, seat_order(world)
    scored: List[Tuple[float, int, Entity]] = []
    for post in _recent_posts(world, name, config):
        author = props(post).get("author")
        if author == viewer.id or author in hidden:
            continue
        followed = author in follows
        if not followed and not config.discover:
            continue
        writer = world.entities.get(author)
        reputation = float(props(writer).get(f"{name}_reputation", 0)) if writer is not None else 0.0
        age = max(0, world.round - int(props(post).get("born", 0)))
        score = (weights.follows * followed + weights.recency / (1 + age)
                 + weights.engagement * math.log1p(_engagement(post)) + weights.reputation * reputation)
        if _downranked(config, post):
            score *= config.downrank.factor
        scored.append((-score, -order.get(post.id, 0), post))
    scored.sort(key=lambda t: (t[0], t[1]))
    ranked = [post for _, _, post in scored[:size]]
    found[key] = ranked
    return list(ranked)


def _trending(world: Any, name: str, config: FeedConfig, n: int) -> List[Entity]:
    order = seat_order(world)
    rows = []
    for post in _recent_posts(world, name, config):
        if props(post).get("kind") == "repost":
            continue
        age = max(0, world.round - int(props(post).get("born", 0)))
        score = _engagement(post) / (1 + age) * (config.downrank.factor if _downranked(config, post) else 1.0)
        if score > 0:
            rows.append((-score, -order.get(post.id, 0), post))
    rows.sort(key=lambda t: (t[0], t[1]))
    return [post for _, _, post in rows[:n]]


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _account(call: Call, index: int = 0) -> Entity:
    world: Any = call.scope.world
    found = world.entity(eid(call.arg(index), call.source))
    if found is None:
        raise ExprError(f"${call.name}: no entity {call.arg(index)!r}", call.source)
    return found  # type: ignore[no-any-return]


def _size(call: Call, index: int, default: int) -> int:
    value = call.arg(index)
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
        raise ExprError(f"${call.name}: n must be a whole number from 0 to 1000, got {value!r}", call.source)
    return value


@function("feed(viewer, n?, mechanism?)", "The viewer's ranked feed: up to n posts (default feed_size) from the social feed "
          "mechanism.", min_args=1, max_args=3)
def _feed_fn(call: Call) -> List[Entity]:
    world: Any = call.scope.world
    name, config = _use(call, 2)
    return feed(world, name, config, _account(call), _size(call, 1, config.feed_size))


@function("trending(n?, mechanism?)", "Recent posts with the most engagement per round of age (reposts count toward the "
          "original).", min_args=0, max_args=2)
def _trending_fn(call: Call) -> List[Entity]:
    world: Any = call.scope.world
    name, config = _use(call, 1)
    return _trending(world, name, config, _size(call, 0, config.trending_size))


@function("following(account, mechanism?)", "Ids of the accounts this account follows.", min_args=1, max_args=2)
def _following_fn(call: Call) -> List[str]:
    world: Any = call.scope.world
    name, _ = _use(call, 1)
    return list(_out(world, f"{name}_follows", eid(call.arg(0), call.source)))


@function("followers(account, mechanism?)", "Ids of the accounts that follow this account.", min_args=1, max_args=2)
def _followers_fn(call: Call) -> List[str]:
    world: Any = call.scope.world
    name, _ = _use(call, 1)
    return list(_in(world, f"{name}_follows", eid(call.arg(0), call.source)))


@function("influence(account, mechanism?)", "Followers + friends + reposts received: how far an account's voice carries.",
          min_args=1, max_args=2)
def _influence_fn(call: Call) -> float:
    world: Any = call.scope.world
    name, config = _use(call, 1)
    account = _account(call)
    friends = len(_out(world, f"{name}_friends", account.id)) if config.friends else 0
    return float(len(_in(world, f"{name}_follows", account.id)) + friends
                 + int(props(account).get(f"{name}_reposts_received", 0)))


@function("insularity(account?, mechanism?)", "Share of an account's connections that are connected to each other (0 diverse "
          "– 1 echo chamber); without an account, the mean over accounts with at least two connections.", min_args=0, max_args=2)
def _insularity_fn(call: Call) -> float:
    world: Any = call.scope.world
    name, config = _use(call, 1)
    if call.arg(0) is not None:
        return _insularity(world, name, config, eid(call.arg(0), call.source))
    scores = [_insularity(world, name, config, a.id) for a in world.entities_of(config.who)
              if len(_connections(world, name, config, a.id)) >= 2]
    return sum(scores) / len(scores) if scores else 0.0


def _insularity(world: Any, name: str, config: FeedConfig, account: str) -> float:
    linked = _connections(world, name, config, account)
    if len(linked) < 2:
        return 0.0
    ties = sum(len(_connections(world, name, config, other) & linked) for other in linked)
    return ties / (len(linked) * (len(linked) - 1))


@function("homophily(prop, mechanism?)", "Share of follow links joining accounts with the same value of `prop` (null "
          "without links).", min_args=1, max_args=2)
def _homophily_fn(call: Call) -> Optional[float]:
    world: Any = call.scope.world
    name, config = _use(call, 1)
    prop = str(call.arg(0))
    same = total = 0
    for a, b in world.links.get(f"{name}_follows", {}):
        x, y = world.entities.get(a), world.entities.get(b)
        if x is None or y is None or not (x.alive and y.alive):
            continue
        if prop not in props(x) or prop not in props(y):
            raise ExprError(f"$homophily: accounts have no property '{prop}'", call.source)
        total += 1
        same += props(x)[prop] == props(y)[prop]
    return same / total if total else None


# ---------------------------------------------------------------------------
# The social op's feed actions
# ---------------------------------------------------------------------------

#: action → (the keys it needs, example keys, what it does). `who`, the acting account, defaults to $actor.
_ACTIONS: Dict[str, Tuple[Tuple[str, ...], str, str]] = {
    "post": (("text",), '"text": "$params.text"', "publish a post to the account's followers"),
    "reply": (("target", "text"), '"target": "$params.post", "text": "$params.text"', "reply to a post"),
    "repost": (("target",), '"target": "$params.post"', "repost a post to the account's followers"),
    "react": (("target", "reaction"), '"target": "$params.post", "reaction": "like"', "react to a post"),
    "follow": (("account",), '"account": "$params.who"', "follow an account: its posts reach the feed"),
    "unfollow": (("account",), '"account": "$params.who"', "stop following an account"),
    "befriend": (("account",), '"account": "$params.who"', "send a friend request, or accept one"),
    "unfriend": (("account",), '"account": "$params.who"', "end a friendship"),
    "block": (("account",), '"account": "$params.who"', "block an account: neither sees the other, follows are removed"),
    "unblock": (("account",), '"account": "$params.who"', "unblock an account"),
    "mute": (("account",), '"account": "$params.who"', "mute an account: its posts leave the feed"),
    "unmute": (("account",), '"account": "$params.who"', "unmute an account"),
    "label": (("target", "label"), '"target": "$params.post", "label": "$params.label"',
              "a moderator labels a post and its reposts"),
}


def _runner(action: str) -> Callable[[Any, Dict[str, Any], Dict[str, Any], str], None]:
    def run(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        world = runner.world
        name = effect["social"]
        config = config_of(world, name, KIND, FeedConfig)
        raw = runner.eval(effect["who"], vars) if "who" in effect else vars.get("actor")
        if raw is None:
            raise RunError(f"`{action}` needs an account: run it in an action ($actor) or give `who`", where)
        actor = entity(world, raw, where)

        def arg(key: str) -> Any:
            return runner.eval(effect[key], vars)

        if action == "label":
            _label(world, name, config, actor, _post(world, name, arg("target"), where), arg("label"))
            return
        if not world.is_a(actor.entity_type, config.who):
            raise Abort(f"{actor.name} has no account here.")
        if action == "post":
            _create(world, name, config, actor, "post", arg("text"), None, where)
        elif action in _RELATING:
            _relate(world, name, config, actor, action, entity(world, arg("account"), where, config.who), where)
        else:
            target = _post(world, name, arg("target"), where)
            if _has(world, f"{name}_blocks", str(props(target).get("author")), actor.id):
                raise Abort("You cannot interact with that account.")
            if action == "reply":
                _create(world, name, config, actor, "reply", arg("text"), target, where)
            elif action == "repost":
                _repost(world, name, config, actor, target, where)
            else:
                _react(world, name, config, actor, target, arg("reaction"))

    return run


def _register_actions() -> None:
    for action, (needs, fields, doc) in _ACTIONS.items():
        example = '{"social": "net", "action": "' + action + f'", {fields}}}  ({doc})'
        family_action("social", ("feed",), action, keys=(*needs, "who"), required=needs, example=example)(_runner(action))


_register_actions()


def _post(world: Any, name: str, value: Any, where: str) -> Entity:
    found = world.entities.get(eid(value, where)) if isinstance(value, (str, Entity)) else None
    if found is None or not found.alive or found.entity_type != f"{name}_post":
        raise Abort(f"There is no post {value!r}.")
    return found  # type: ignore[no-any-return]


def _notify(world: Any, name: str, config: FeedConfig, what: str, actor: Entity, target: str, text: str) -> None:
    if what in config.notify and target != actor.id:
        world.emit("social", text, actor=actor.id, to=(target,), data={"mechanism": name, "notice": what})


def _bump(world: Any, owner: Entity, prop: str, by: float) -> None:
    world.set_prop(owner, prop, props(owner).get(prop, 0) + by)


def _create(world: Any, name: str, config: FeedConfig, author: Entity, kind: str, text: Any,
            parent: Optional[Entity], where: str) -> Entity:
    if not isinstance(text, str) or not text.strip():
        raise Abort("A post needs some text.")
    if len(text) > config.max_chars:
        raise Abort(f"Posts are at most {config.max_chars} characters; yours has {len(text)}.")
    post = _new_post(world, name, author, kind, text, parent, where)
    _bump(world, author, f"{name}_posts", 1)
    if parent is not None:
        _bump(world, parent, "replies", 1)
        owner = world.entities.get(props(parent).get("author"))
        if owner is not None:
            _bump(world, owner, f"{name}_reputation", config.reputation.reply)
            _notify(world, name, config, "reply", author, owner.id, f"{author.name} replied to your post [{parent.id}] with [{post.id}].")
    return post


def _new_post(world: Any, name: str, author: Entity, kind: str, text: Any, parent: Optional[Entity], where: str) -> Entity:
    # Created empty, then filled: participant text must never be read as an expression.
    post = world.create(f"{name}_post", None, f"post by {author.name}", {}, None, world.scope(), where)
    origin = props(parent).get("origin") or props(parent).get("author") if parent is not None else author.id
    for prop, value in (("author", author.id), ("text", text), ("kind", kind), ("parent", parent.id if parent else ""),
                        ("origin", origin), ("born", world.round)):
        world.set_prop(post, prop, value)
    return post  # type: ignore[no-any-return]


def _repost(world: Any, name: str, config: FeedConfig, actor: Entity, target: Entity, where: str) -> None:
    root = target
    if props(target).get("kind") == "repost":
        found = world.entities.get(props(target).get("parent"))
        root = found if found is not None and found.alive else target
    if props(root).get("author") == actor.id:
        raise Abort("You cannot repost your own post.")
    if actor.id in (props(root).get("reposters") or []):
        raise Abort("You already reposted that.")
    world.set_prop(root, "reposters", list(props(root).get("reposters") or []) + [actor.id])
    _bump(world, root, "reposts", 1)
    post = _new_post(world, name, actor, "repost", props(root).get("text"), root, where)
    world.set_prop(post, "labels", list(props(root).get("labels") or []))
    owner = world.entities.get(props(root).get("author"))
    if owner is not None:
        _bump(world, owner, f"{name}_reputation", config.reputation.repost)
        _bump(world, owner, f"{name}_reposts_received", 1)
        _notify(world, name, config, "repost", actor, owner.id, f"{actor.name} reposted your post [{root.id}].")


def _react(world: Any, name: str, config: FeedConfig, actor: Entity, post: Entity, reaction: Any) -> None:
    if reaction not in config.reactions:
        raise Abort(f"Reactions here: {', '.join(config.reactions) or 'none'}.")
    reacted = dict(props(post).get("reacted") or {})
    mine = list(reacted.get(actor.id) or [])
    if reaction in mine:
        raise Abort(f"You already reacted {reaction} to that post.")
    reacted[actor.id] = mine + [reaction]
    world.set_prop(post, "reacted", reacted)
    counts = dict(props(post).get("reactions") or {})
    counts[reaction] = counts.get(reaction, 0) + 1
    world.set_prop(post, "reactions", counts)
    _bump(world, post, "reacts", 1)
    owner = world.entities.get(props(post).get("author"))
    if owner is not None and owner.id != actor.id:
        _bump(world, owner, f"{name}_reputation", config.reputation.reaction)
        _bump(world, owner, f"{name}_reactions_received", 1)
        _notify(world, name, config, "reaction", actor, owner.id, f"{actor.name} reacted {reaction} to your post [{post.id}].")


def _label(world: Any, name: str, config: FeedConfig, actor: Entity, post: Entity, label: Any) -> None:
    if config.moderators is None or not world.is_a(actor.entity_type, config.moderators):
        raise Abort(f"{actor.name} may not label posts.")
    if label not in config.labels:
        raise Abort(f"Labels here: {', '.join(config.labels)}.")
    root = props(post).get("parent") if props(post).get("kind") == "repost" else post.id
    changed = False
    for item in world.entities_of(f"{name}_post"):
        if item.id == root or (props(item).get("kind") == "repost" and props(item).get("parent") == root):
            labels = list(props(item).get("labels") or [])
            if label not in labels:
                world.set_prop(item, "labels", labels + [label])
                changed = changed or item.id == root
    if not changed:
        raise Abort(f"That post is already labelled {label}.")
    owner = world.entities.get(props(world.entities[root]).get("author")) if root in world.entities else None
    if owner is not None:
        _bump(world, owner, f"{name}_reputation", config.reputation.label)
        _notify(world, name, config, "label", actor, owner.id, f"Your post [{root}] was labelled {label}.")


def _relate(world: Any, name: str, config: FeedConfig, actor: Entity, act: str, who: Entity, where: str) -> None:
    if who.id == actor.id:
        raise Abort("That is your own account.")
    follows, friends, requests = f"{name}_follows", f"{name}_friends", f"{name}_requests"
    blocks, mutes = f"{name}_blocks", f"{name}_mutes"
    if act in ("follow", "befriend") and (_has(world, blocks, who.id, actor.id) or _has(world, blocks, actor.id, who.id)):
        raise Abort(f"You cannot {act} {who.name}.")
    if act == "follow":
        if _has(world, follows, actor.id, who.id):
            raise Abort(f"You already follow {who.name}.")
        world.link(follows, actor, who, 1, where)
        _notify(world, name, config, "follow", actor, who.id, f"{actor.name} followed you.")
    elif act == "unfollow":
        if not _has(world, follows, actor.id, who.id):
            raise Abort(f"You do not follow {who.name}.")
        world.unlink(follows, actor, who, where)
    elif act == "befriend":
        if not config.friends and friends not in world.links:
            raise Abort("There are no friendships here.")
        if _has(world, friends, actor.id, who.id):
            raise Abort(f"You are already friends with {who.name}.")
        if _has(world, requests, who.id, actor.id):
            world.unlink(requests, who, actor, where)
            world.link(friends, actor, who, 1, where)
            _notify(world, name, config, "friend", actor, who.id, f"{actor.name} accepted your friend request.")
        elif _has(world, requests, actor.id, who.id):
            raise Abort(f"You already asked {who.name}; wait for them to accept.")
        else:
            world.link(requests, actor, who, 1, where)
            _notify(world, name, config, "friend", actor, who.id, f"{actor.name} sent you a friend request (befriend them to accept).")
    elif act == "unfriend":
        world.unlink(friends, actor, who, where)
    elif act == "block":
        world.link(blocks, actor, who, 1, where)
        for a, b in ((actor, who), (who, actor)):
            world.unlink(follows, a, b, where)
            world.unlink(requests, a, b, where)
        world.unlink(friends, actor, who, where)
    elif act == "unblock":
        world.unlink(blocks, actor, who, where)
    elif act == "mute":
        world.link(mutes, actor, who, 1, where)
    else:
        world.unlink(mutes, actor, who, where)


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


_FEED_LINE = ("[{id}] {$entity($it.author)}{$' reposted ' + $text($entity($it.origin)) if $it.kind == 'repost' else "
              "($' replied to [' + $it.parent + ']' if $it.kind == 'reply' else '')}: {text} · {reacts} reactions · "
              "{reposts} reposts · {replies} replies{$' · labelled ' + $join($it.labels) if $len($it.labels) > 0 else ''}")


@mode("social", "feed", FeedConfig,
           "A social network: posts (type `<name>_post`), replies, reposts, reactions, follows, friend requests, blocks and "
           "mutes (relations `<name>_follows`, `<name>_friends`, `<name>_blocks` …), a ranked feed view per account, "
           "trending, reputation moved by engagement, and moderator labels that downrank posts. Read it with "
           "$feed(viewer, n?), $trending(n?), $following(a), $followers(a), $influence(a), $insularity(a?), $homophily(prop); "
           "with several feeds, name one as the last argument ($following($actor, 'net')).",
           example={"who": "account", "feed_size": 6, "moderators": "moderator",
                    "downrank": {"labels": ["misleading"], "factor": 0.2}})
def _expand(name: str, config: FeedConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    require_type(contract, config.who, "who", agent=True)
    require_type(contract, config.moderators, "moderators", agent=True)
    if f"{name}_post" in (contract.get("types") or {}):
        raise MechanismError(f"type '{name}_post' is generated by this mechanism", "rename your type", "")
    accounts, post_type = config.who, f"{name}_post"
    rate: Dict[str, Any] = {"per_turn": config.per_turn} if config.per_turn else {}
    visible = f"$union($ids($feed($actor, null, '{name}')), $ids($trending(null, '{name}')))"  # every post the account is shown: its feed and trending
    in_feed = {"type": "enum", "values": visible, "description": "The [id] of a post in your feed or trending."}
    feed_when = [{"expr": f"$len({visible}) > 0", "why": "There is no post to see: your feed and trending are empty."}]
    text = {"type": "text", "max_len": config.max_chars}
    who = {"type": "entity", "of": accounts, "description": "The account (not yourself)."}

    def act(description: str, do: Dict[str, Any], params: Dict[str, Any], when: Optional[List[Any]] = None,
            **extra: Any) -> Dict[str, Any]:
        return {"by": accounts, "description": description, "params": params, "when": when or [],
                "do": [{"social": name, **do}], "private": True, **extra}

    actions: Dict[str, Any] = {}
    if config.posts:
        actions[f"{name}_post"] = act("Publish a post to your followers.", {"action": "post", "text": "$params.text"},
                                      {"text": text}, outcome="Posted.", **rate)
    if config.replies:
        actions[f"{name}_reply"] = act("Reply to a post in your feed or trending.", {"action": "reply", "target": "$params.post", "text": "$params.text"},
                                       {"post": in_feed, "text": text}, feed_when, outcome="Replied to [{$params.post}].", **rate)
    if config.reposts:
        actions[f"{name}_repost"] = act("Repost a post from your feed or trending to your followers.", {"action": "repost", "target": "$params.post"},
                                        {"post": in_feed}, feed_when, outcome="Reposted [{$params.post}].", **rate)
    if len(config.reactions) == 1:
        reaction = config.reactions[0]
        actions[f"{name}_{reaction}"] = act(f"React '{reaction}' to a post in your feed or trending.",
                                            {"action": "react", "target": "$params.post", "reaction": reaction},
                                            {"post": in_feed}, feed_when, outcome=f"You reacted {reaction} to [{{$params.post}}].")
    elif config.reactions:
        actions[f"{name}_react"] = act("React to a post in your feed or trending.",
                                       {"action": "react", "target": "$params.post", "reaction": "$params.reaction"},
                                       {"post": in_feed, "reaction": {"type": "enum", "values": list(config.reactions)}},
                                       feed_when, outcome="You reacted {$params.reaction} to [{$params.post}].")
    if config.follows:
        actions[f"{name}_follow"] = act("Follow an account: its posts reach your feed.", {"action": "follow", "account": "$params.who"},
                                        {"who": who}, outcome="You follow {$params.who.name}.")
        actions[f"{name}_unfollow"] = act("Stop following an account.", {"action": "unfollow", "account": "$params.who"},
                                          {"who": {"type": "enum", "values": f"$following($actor, '{name}')", "description": "An account you follow."}},
                                          [{"expr": f"$len($following($actor, '{name}')) > 0", "why": "You follow nobody."}],
                                          outcome="You unfollowed {$params.who}.")
    if config.friends:
        actions[f"{name}_befriend"] = act("Send a friend request, or accept one sent to you.", {"action": "befriend", "account": "$params.who"},
                                          {"who": who}, outcome="Done: {$params.who.name}.")
    if config.block:
        actions[f"{name}_block"] = act("Block an account: neither of you sees the other, follows are removed.",
                                       {"action": "block", "account": "$params.who"}, {"who": who}, outcome="You blocked {$params.who.name}.")
    if config.mute:
        actions[f"{name}_mute"] = act("Mute an account: its posts leave your feed.", {"action": "mute", "account": "$params.who"},
                                      {"who": who}, outcome="You muted {$params.who.name}.")
    if config.moderators:
        actions[f"{name}_label"] = {"by": config.moderators, "description": "Label a post (and its reposts).",
                                    "params": {"post": {"type": "entity", "of": post_type, "description": "The post id."},
                                               "label": {"type": "enum", "values": list(config.labels)}},
                                    "do": [{"social": name, "action": "label", "target": "$params.post", "label": "$params.label"}],
                                    "outcome": "Labelled [{$params.post.id}] {$params.label}.", "private": True}
    rep = config.reputation
    fragment: Dict[str, Any] = {
        "types": {
            accounts: {"props": {f"{name}_reputation": {"type": "number", "default": rep.start, "min": 0, "max": 1},
                                 f"{name}_posts": {"type": "int", "default": 0},
                                 f"{name}_reposts_received": {"type": "int", "default": 0},
                                 f"{name}_reactions_received": {"type": "int", "default": 0}}},
            post_type: {"description": "A post on the network.", "props": {
                "author": "", "text": {"type": "text", "default": ""},
                "kind": {"type": "enum", "values": ["post", "reply", "repost"], "default": "post"},
                "parent": "", "origin": "", "born": {"type": "int", "default": 0},
                "reacts": {"type": "int", "default": 0}, "reactions": {"type": "map", "default": {}},
                "reposts": {"type": "int", "default": 0}, "replies": {"type": "int", "default": 0},
                "labels": {"type": "list", "default": []}, "reacted": {"type": "map", "default": {}},
                "reposters": {"type": "list", "default": []}}},
        },
        "relations": {f"{name}_follows": {"description": "from follows to: to's posts reach from's feed."},
                      f"{name}_friends": {"symmetric": True, "description": "Mutual friends."},
                      f"{name}_requests": {"description": "from asked to to be friends."},
                      f"{name}_blocks": {"description": "from blocked to."},
                      f"{name}_mutes": {"description": "from muted to."}},
        "actions": actions,
        "views": {
            f"{name}_feed": {"for": accounts, "title": "Your feed", "of": f"$feed($actor, null, '{name}')", "show": _FEED_LINE,
                             "empty": "Nothing new from the accounts you follow."},
            f"{name}_profile": {"for": accounts, "show": f"You have {{$len($followers($actor, '{name}'))}} followers, follow "
                                                        f"{{$len($following($actor, '{name}'))}}, reputation {{{name}_reputation|pct}}."},
            f"{name}_trending": {"for": [accounts] + ([config.moderators] if config.moderators else []), "title": "Trending",
                                 "of": f"$trending(null, '{name}')", "show": _FEED_LINE, "empty": "Nothing is trending.", "look": True},
        },
    }
    if config.moderators:
        fragment["views"][f"{name}_queue"] = {"for": config.moderators, "title": "Most engaged recent posts",
                                              "of": f"$trending(10, '{name}')", "show": _FEED_LINE, "empty": "Nothing to review."}
    names = list(actions)
    if not names:
        return fragment
    if config.stage is None:
        fragment["stages"] = [{"name": name, "turns": config.turns, "actions": names, "max_actions": 2}]
    else:
        fragment["stage_hooks"] = {config.stage: {"actions": names, "max_actions": 2}}
    return fragment
