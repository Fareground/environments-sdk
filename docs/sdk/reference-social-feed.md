# social / feed

### `social.feed`
A social network: posts (type `<name>_post`), replies, reposts, reactions, follows, friend requests, blocks and mutes (relations `<name>_follows`, `<name>_friends`, `<name>_blocks` …), a ranked feed view per account, trending, reputation moved by engagement, and moderator labels that downrank posts. Read it with $feed(viewer, n?), $trending(n?), $following(a), $followers(a), $influence(a), $insularity(a?), $homophily(prop).

Config:
- `who` (required): Agent type that has an account (subtypes included).
- `follows` (default true): Offer follow and unfollow tools.
- `friends` (default false): Offer friend requests (mutual friendship).
- `block` (default true): Offer a block tool (blocked accounts vanish from each other's feeds).
- `mute` (default false): Offer a mute tool (muted accounts vanish from your feed).
- `posts` (default true): Offer a post tool.
- `replies` (default true): Offer a reply tool.
- `reposts` (default true): Offer a repost tool.
- `reactions` (default ["like"]): Reactions accounts may give; empty for none.
- `max_chars` (default 280): Longest post, in characters.
- `per_turn` (default 1): Posts, replies and reposts per turn (each).
- `feed_size` (default 8): Posts in a feed.
- `window` (default 12): Rounds a post stays eligible for feeds and trending.
- `discover` (default false): Feeds also rank posts from accounts the viewer does not follow.
- `weights` (default "follows=2.0 recency=1.0 engagement=1.0 reputation=0.5"): 
- `downrank` (default "labels=[] factor=1.0"): 
- `trending_size` (default 5): Posts in the trending list.
- `moderators` (default null): Agent type that may label posts.
- `labels` (default ["misleading"]): Labels moderators may apply.
- `reputation` (default "start=0.5 reaction=0.005 repost=0.02 reply=0.005 label=-0.05"): 
- `notify` (default ["follow", "reply", "repost", "friend", "label"]): What an account is told about: follow, reply, repost, reaction, friend, label.
- `turns` (default "simultaneous"): Turns of the generated stage.
- `stage` (default null): Offer the tools during this declared stage instead of a generated one.
- `tools` (default "each"): How the generated tools are offered: each (one tool per action) | one (one tool named after the mechanism, whose `action` argument lists the actions legal now) | auto (one tool only when every action takes the same arguments).

Nested config:
**FeedWeights** — How much each signal counts when ranking a feed.
- `follows`: number = 2.0 — Bonus for posts by accounts you follow or befriended.
- `recency`: number = 1.0 — Weight of 1/(1+age in rounds).
- `engagement`: number = 1.0 — Weight of ln(1 + reactions + 2·reposts + replies).
- `reputation`: number = 0.5 — Weight of the author's reputation (0–1).
**Downrank** — Moderation: labelled posts rank lower (factor 1 = no effect, 0 = hidden at the bottom).
- `labels`: [text] — Labels that downrank a post.
- `factor`: number = 1.0 — Score multiplier for a downranked post.
**Reputation** — How engagement moves an author's reputation (clamped to 0–1).
- `start`: number = 0.5
- `reaction`: number = 0.005 — Change per reaction received.
- `repost`: number = 0.02 — Change per repost received.
- `reply`: number = 0.005 — Change per reply received.
- `label`: number = -0.05 — Change when a moderator labels one of your posts.

Actions of the `social` op:
- `post` — takes `text`, `who` (needs `text`): {"social": "net", "action": "post", "text": "$params.text"}  (publish a post to the account's followers)
- `reply` — takes `target`, `text`, `who` (needs `target`, `text`): {"social": "net", "action": "reply", "target": "$params.post", "text": "$params.text"}  (reply to a post)
- `repost` — takes `target`, `who` (needs `target`): {"social": "net", "action": "repost", "target": "$params.post"}  (repost a post to the account's followers)
- `react` — takes `target`, `reaction`, `who` (needs `target`, `reaction`): {"social": "net", "action": "react", "target": "$params.post", "reaction": "like"}  (react to a post)
- `follow` — takes `account`, `who` (needs `account`): {"social": "net", "action": "follow", "account": "$params.who"}  (follow an account: its posts reach the feed)
- `unfollow` — takes `account`, `who` (needs `account`): {"social": "net", "action": "unfollow", "account": "$params.who"}  (stop following an account)
- `befriend` — takes `account`, `who` (needs `account`): {"social": "net", "action": "befriend", "account": "$params.who"}  (send a friend request, or accept one)
- `unfriend` — takes `account`, `who` (needs `account`): {"social": "net", "action": "unfriend", "account": "$params.who"}  (end a friendship)
- `block` — takes `account`, `who` (needs `account`): {"social": "net", "action": "block", "account": "$params.who"}  (block an account: neither sees the other, follows are removed)
- `unblock` — takes `account`, `who` (needs `account`): {"social": "net", "action": "unblock", "account": "$params.who"}  (unblock an account)
- `mute` — takes `account`, `who` (needs `account`): {"social": "net", "action": "mute", "account": "$params.who"}  (mute an account: its posts leave the feed)
- `unmute` — takes `account`, `who` (needs `account`): {"social": "net", "action": "unmute", "account": "$params.who"}  (unmute an account)
- `label` — takes `target`, `label`, `who` (needs `target`, `label`): {"social": "net", "action": "label", "target": "$params.post", "label": "$params.label"}  (a moderator labels a post and its reposts)

```json
{"mechanisms": {"my_feed": {"kind": "social", "mode": "feed", "who": "account", "feed_size": 6, "moderators": "moderator", "downrank": {"labels": ["misleading"], "factor": 0.2}}}}
```
