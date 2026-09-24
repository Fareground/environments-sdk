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
family("economy", "Money, goods, making things and serving customers: ledgers (currencies, taxes, loans), "
                  "inventories, production, supply chains, customers' demand for stocked items and the policies that "
                  "replenish them, and service queues (customers arriving on channels, served by staffed server "
                  "pools).",
       {"who": "agent type(s) holding money or goods", "tools": SHARED["tools"]})
family("agreements", "Commitments between agents over time: negotiated deals, subscriptions and bookings.",
       {"who": "agent type(s) making the commitments", "currency": SHARED["currency"], "tools": SHARED["tools"]})
family("decision", "Collective choice: ballots, structured deliberation with motions and votes, and procedures with "
                   "phases and objections.",
       {"who": "agent type that decides", "ties": SHARED["ties"], "private": SHARED["private"],
        "stage": SHARED["stage"], "when": SHARED["when"], "views": SHARED["views"], "tools": SHARED["tools"]})
family("game", "Game equipment: boards with enforced rules, cards, betting pots, and statuses on pieces (timed "
               "conditions that tick, modify properties and block actions).",
       {"who": "agent type that plays (for statuses: the types that carry them)", "stage": SHARED["stage"],
        "views": SHARED["views"], "qty": SHARED["qty"], "phase": SHARED["phase"], "tools": SHARED["tools"]})
family("groups", "Who belongs with whom: hidden roles and teams, stable matching.",
       {"who": "agent type that belongs to groups", "views": SHARED["views"], "tools": SHARED["tools"]})
family("social", "Spreading: a social feed, and diffusion over a network.",
       {"who": "agent type that communicates", "max_chars": SHARED["max_chars"], "tools": SHARED["tools"]})
family("host",
       "Services the host provides during a run: an LLM judge, a game master, recaps, tools such as web search, "
       "agents' memory with recall, and generated personas.",
       {"who": "agent type(s) served", "max_chars": SHARED["max_chars"], "private": SHARED["private"],
        "phase": SHARED["phase"], "views": SHARED["views"]})
