"""The mechanism families: what each is for, and the fields its modes name the same way.

A family is registered here once; its modes and actions register in their own modules. The
guide's mechanism table and family pages are generated from these entries.
"""
from __future__ import annotations

from ..registry import family

__all__ = ["SHARED"]

#: Names every family uses for the same concept (the guide lists them once).
SHARED = {
    "who": "the agent type(s) taking part (a type name or a list; subtypes included)",
    "tools": "how generated tools are offered: each (one tool per action, the default) | one (one tool named after "
             "the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every "
             "action takes the same arguments)",
    "stage": "a declared stage the mechanism runs in (default: a stage it generates)",
    "when": "only while this expression is true",
    "views": "generate the mechanism's views",
    "private": "keep choices hidden from other agents",
    "ties": "how a tie is decided: random (from the run's seed) | first (declared order) | none (nobody wins) | share",
    "phase": "start | end: when in the round the mechanism's own step runs",
    "max_chars": "the longest text accepted or kept",
    "currency": "the property (or ledger currency) holding money",
    "qty": "a number of units (items, shares, cards, batches); money is `amount`",
}

family("market",
       "Trading venues: continuous order books, auctions and procurement tenders, prediction markets and posted-price "
       "shops.",
       {"who": "agent type that trades (in effects: the trading agent, default $actor)", "currency": SHARED["currency"],
        "qty": "units traded (shares, items); money is `amount`", "stage": SHARED["stage"], "tools": SHARED["tools"]})
family("economy", "Money, goods and making things: ledgers (currencies, taxes, loans), inventories, production, "
                  "supply chains, customers' demand for stocked items and the policies that replenish them.",
       {"who": "agent type(s) holding money or goods", "tools": SHARED["tools"]})
family("agreements", "Commitments between agents over time: negotiated deals, jobs, subscriptions and bookings.",
       {"who": "agent type(s) making the commitments", "currency": SHARED["currency"], "tools": SHARED["tools"]})
family("decision", "Collective choice: ballots and structured deliberation with motions and votes.",
       {"who": "agent type that decides", "ties": SHARED["ties"], "private": SHARED["private"],
        "stage": SHARED["stage"], "when": SHARED["when"], "tools": SHARED["tools"]})
family("game", "Game equipment: boards with enforced rules, cards, betting pots and worker-placement slots.",
       {"who": "agent type that plays", "stage": SHARED["stage"], "views": SHARED["views"], "qty": SHARED["qty"],
        "tools": SHARED["tools"]})
family("flow", "Who acts when and how it ends: turn order, procedures with phases, victory conditions.",
       {"who": "agent type whose turns or victory it governs", "views": SHARED["views"], "ties": SHARED["ties"],
        "tools": SHARED["tools"]})
family("operations", "Service operations: customers arriving on channels and served by staffed server pools — contact "
                     "centres, clinics, counters, repair crews — with queues, patience, callbacks and service levels.",
       {"unit": "the time unit of every duration and threshold (second, minute, hour)"})
family("groups",
       "Who belongs with whom: hidden roles and teams, factions and alliances, relationships, stable matching.",
       {"who": "agent type that belongs to groups", "views": SHARED["views"], "phase": SHARED["phase"],
        "tools": SHARED["tools"]})
family("social", "Talking and spreading: channels (rooms, direct messages), a social feed, diffusion over a network.",
       {"who": "agent type that communicates", "max_chars": SHARED["max_chars"], "tools": SHARED["tools"]})
family("mind", "What agents know and remember: beliefs with confidence, memory with recall, generated personas.",
       {"who": "agent type whose mind it models", "phase": SHARED["phase"], "views": SHARED["views"]})
family("conditions", "Effects on entities over time: statuses, cooldowns, channeled actions and terrain.",
       {"who": "entity type(s) the conditions apply to", "phase": SHARED["phase"], "views": SHARED["views"]})
family("host",
       "Services the host provides during a run: an LLM judge, a game master, recaps and tools such as web search.",
       {"who": "agent type(s) served", "max_chars": SHARED["max_chars"], "private": SHARED["private"]})
