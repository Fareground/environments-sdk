"""Native card scoring: poker hands, blackjack totals, rummy sets and runs, trick-taking.

Every function accepts cards as card entities (``rank`` and ``suit`` props), as short text
(``"AS"``, ``"10h"``, ``"Td"``, ``"A♠"``) or as maps ``{rank, suit}``. Standard ranks are whole
numbers 2–14 (jack 11, queen 12, king 13, ace 14); suits are ``spades hearts diamonds clubs``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from ..expr import Call, ExprError, function

__all__ = ["RANK_LABELS", "SUITS", "SUIT_SYMBOLS", "parse_card", "poker_rank", "poker_hand", "blackjack", "sets",
           "runs", "trick_winner", "follow_suit", "suit_name"]

SUITS = ("spades", "hearts", "diamonds", "clubs")
SUIT_SYMBOLS = {"spades": "♠", "hearts": "♥", "diamonds": "♦", "clubs": "♣"}
SUIT_LETTERS = {"spades": "S", "hearts": "H", "diamonds": "D", "clubs": "C"}
RANK_LABELS = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K",
               14: "A"}
_RANK_WORDS = {2: "twos", 3: "threes", 4: "fours", 5: "fives", 6: "sixes", 7: "sevens", 8: "eights", 9: "nines",
               10: "tens", 11: "jacks", 12: "queens", 13: "kings", 14: "aces"}
_RANK_NAMES = {11: "jack", 12: "queen", 13: "king", 14: "ace"}
_PARSE_RANKS = {**{label: rank for rank, label in RANK_LABELS.items()}, "T": 10, "1": 14}
_PARSE_SUITS = {**{letter: suit for suit, letter in SUIT_LETTERS.items()},
                **{symbol: suit for suit, symbol in SUIT_SYMBOLS.items()}}
CATEGORIES = ("high card", "pair", "two pair", "three of a kind", "straight", "flush", "full house",
              "four of a kind", "straight flush")
#: Base of the positional score: bigger than any rank, so a score compares like its tuple.
_BASE = 15

Card = tuple[Any, str, Any]  # (rank, suit, the original value)


def parse_card(value: Any) -> Card:
    """``(rank, suit, value)`` for an entity, a map or short text; raises ValueError when unreadable."""
    if hasattr(value, "entity_type"):
        props = value.properties
        return props.get("rank"), suit_name(props.get("suit")), value
    if isinstance(value, Mapping):
        return value.get("rank"), suit_name(value.get("suit")), value
    if isinstance(value, str):
        text = value.strip().upper()
        if len(text) >= 2:
            rank, suit = _PARSE_RANKS.get(text[:-1]), _PARSE_SUITS.get(text[-1])
            if rank is not None and suit is not None:
                return rank, suit, value
    raise ValueError(f"cannot read {value!r} as a card (a card entity, {{rank, suit}} or text like 'AS', '10h')")


def suit_name(value: Any) -> str:
    """A suit as every card function compares it: a standard suit however it is spelled — its name, letter or symbol,
    any case (``"H"``, ``"♥"``, ``"Hearts"`` are ``hearts``) — and any other suit (a deck of its own) as written."""
    text = str(value or "").strip()
    return text.lower() if text.lower() in SUITS else _PARSE_SUITS.get(text.upper(), text)


def _given_suit(value: Any, cards: Sequence[Card], what: str) -> str | None:
    """A suit an author gives (a lead suit, a trump) as :func:`suit_name` reads it; one that is no standard suit is
    refused where every card in play is of the standard four (it could match none of them)."""
    if value is None or value == "":
        return None
    suit = suit_name(value)
    if suit not in SUITS and cards and all(card[1] in SUITS for card in cards):
        raise ValueError(f"{what} {value!r} is not a suit: write spades, hearts, diamonds or clubs (or S, H, D, C)")
    return suit


def _cards(values: Any) -> list[Card]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    return [parse_card(v) for v in values]


def _rank(card: Card) -> int:
    rank = card[0]
    if isinstance(rank, bool) or not isinstance(rank, int):
        raise ValueError(f"this needs whole-number ranks (2–14), got rank {rank!r}")
    return rank


# ---------------------------------------------------------------------------
# Poker
# ---------------------------------------------------------------------------


def _five(cards: Sequence[Card]) -> tuple[int, tuple[int, ...]]:
    """``(category, tiebreak ranks)`` of up to five cards."""
    ranks = sorted((_rank(c) for c in cards), reverse=True)
    counts = Counter(ranks)
    grouped = sorted(counts, key=lambda r: (counts[r], r), reverse=True)
    shape = sorted(counts.values(), reverse=True)
    flush = len(cards) == 5 and len({c[1] for c in cards}) == 1
    straight_high = 0
    if len(cards) == 5 and len(counts) == 5:
        if ranks[0] - ranks[4] == 4:
            straight_high = ranks[0]
        elif ranks == [14, 5, 4, 3, 2]:
            straight_high = 5
    if straight_high and flush:
        return 8, (straight_high,)
    if shape[0] == 4:
        return 7, tuple(grouped)
    if shape[:2] == [3, 2]:
        return 6, tuple(grouped)
    if flush:
        return 5, tuple(ranks)
    if straight_high:
        return 4, (straight_high,)
    if shape[0] == 3:
        return 3, tuple(grouped)
    if shape[:2] == [2, 2]:
        return 2, tuple(grouped)
    if shape[0] == 2:
        return 1, tuple(grouped)
    return 0, tuple(ranks)


def _hand_name(category: int, order: tuple[int, ...]) -> str:
    word, name = _RANK_WORDS, _RANK_NAMES
    high = lambda r: f"{name.get(r, str(r))} high"  # noqa: E731
    if category == 8:
        return "royal flush" if order[0] == 14 else f"straight flush, {high(order[0])}"
    if category == 7:
        return f"four of a kind, {word[order[0]]}"
    if category == 6:
        return f"full house, {word[order[0]]} over {word[order[1]]}"
    if category == 5:
        return f"flush, {high(order[0])}"
    if category == 4:
        return f"straight, {high(order[0])}"
    if category == 3:
        return f"three of a kind, {word[order[0]]}"
    if category == 2:
        return f"two pair, {word[order[0]]} and {word[order[1]]}"
    if category == 1:
        return f"pair of {word[order[0]]}"
    return f"high card, {name.get(order[0], str(order[0]))}" if order else "no cards"


def poker_rank(values: Any) -> dict[str, Any]:
    """The best five-card poker hand in ``values`` (any number of cards; fewer than five are ranked as they are).

    Returns ``score`` (a whole number: a higher score is a better hand, equal scores tie),
    ``category`` (e.g. ``"full house"``), ``level`` (0 high card … 8 straight flush), ``name``
    (``"full house, kings over fives"``), ``ranks`` (the tiebreak ranks) and ``best`` (the five cards).
    """
    cards = _cards(values)
    if not cards:
        return {"score": 0, "category": CATEGORIES[0], "level": 0, "name": "no cards", "ranks": [], "best": []}
    (category, order), best_cards = _best(cards)
    padded = list(order) + [0] * (5 - len(order))
    score = category
    for rank in padded[:5]:
        score = score * _BASE + rank
    return {"score": score, "category": CATEGORIES[category], "level": category, "name": _hand_name(category, order),
            "ranks": list(order), "best": [c[2] for c in best_cards]}


def _best(cards: Sequence[Card]) -> tuple[tuple[int, tuple[int, ...]], Sequence[Card]]:
    """``((category, tiebreak ranks), the five cards)`` of the best hand in ``cards`` (not empty)."""
    groups = combinations(cards, 5) if len(cards) > 5 else [tuple(cards)]
    best_key: tuple[int, tuple[int, ...]] | None = None
    best_cards: Sequence[Card] = ()
    for group in groups:
        key = _five(group)
        if best_key is None or key > best_key:
            best_key, best_cards = key, group
    assert best_key is not None
    return best_key, best_cards


#: Leading tiebreak ranks that make each hand what it is (the rest are kickers): two pair and a full house take two.
_MADE_RANKS = {0: 1, 1: 1, 2: 2, 3: 1, 6: 2, 7: 1}


def poker_hand(hole: Any, board: Any) -> str:
    """What a player's private cards make with the shared board, in words a player reads: ``"pair of nines on the
    board — shared by everyone"``, ``"two pair, kings and sevens, using both hole cards"``. The board alone making
    the same hand means the player holds nothing better than everyone still in."""
    mine, shared = _cards(hole), _cards(board)
    if not mine:
        return "no cards"
    (category, order), best = _best(mine + shared)
    name = _hand_name(category, order)
    if not shared:
        return name
    made = _MADE_RANKS.get(category, len(order))
    board_category, board_order = _best(shared)[0]
    if board_category == category and board_order[:made] == order[:made]:
        return f"{name} on the board — shared by everyone"
    used = sum(1 for card in best if any(card is own for own in mine))
    if used == len(mine) and used > 1:
        return f"{name}, using {'both' if used == 2 else 'all your'} hole cards"
    return f"{name}, using {'one hole card' if used == 1 else f'{used} of your hole cards'}"


# ---------------------------------------------------------------------------
# Blackjack
# ---------------------------------------------------------------------------


def blackjack(values: Any) -> dict[str, Any]:
    """``{total, soft, bust, blackjack}``: aces count 11 unless that busts; faces count 10."""
    cards = _cards(values)
    total, aces = 0, 0
    for card in cards:
        rank = _rank(card)
        if rank == 14:
            aces += 1
            total += 11
        else:
            total += min(rank, 10)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return {"total": total, "soft": aces > 0, "bust": total > 21, "blackjack": len(cards) == 2 and total == 21}


# ---------------------------------------------------------------------------
# Rummy: sets and runs
# ---------------------------------------------------------------------------


def sets(values: Any, size: int = 3) -> list[list[Any]]:
    """Groups of at least ``size`` cards of the same rank, highest rank first."""
    by_rank: dict[Any, list[Any]] = {}
    for rank, _suit, original in _cards(values):
        by_rank.setdefault(rank, []).append(original)
    groups = [cards for cards in by_rank.values() if len(cards) >= size]
    return sorted(groups, key=lambda g: (len(g), _sortable(parse_card(g[0])[0])), reverse=True)


def runs(values: Any, size: int = 3) -> list[list[Any]]:
    """Longest runs of at least ``size`` consecutive ranks in one suit (an ace is high or low), longest first."""
    by_suit: dict[str, dict[int, Any]] = {}
    for card in _cards(values):
        rank = _rank(card)
        by_suit.setdefault(card[1], {}).setdefault(rank, card[2])
        if rank == 14:
            by_suit[card[1]].setdefault(1, card[2])
    found: list[tuple[int, int, list[Any]]] = []
    for ranked in by_suit.values():
        present = sorted(ranked)
        start = 0
        for i in range(1, len(present) + 1):
            if i == len(present) or present[i] != present[i - 1] + 1:
                block = present[start:i]
                if len(block) >= size:
                    found.append((len(block), block[-1], [ranked[r] for r in block]))
                start = i
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [cards for _, _, cards in found]


def _sortable(value: Any) -> tuple[int, Any]:
    return (0, value) if isinstance(value, (int, float)) and not isinstance(value, bool) else (1, str(value))


# ---------------------------------------------------------------------------
# Trick-taking
# ---------------------------------------------------------------------------


def trick_winner(values: Any, lead_suit: str | None = None, trump: str | None = None) -> Any:
    """The card that wins a trick: the highest trump played, else the highest card of the suit led
    (the first card's suit unless ``lead_suit`` is given). None for an empty trick."""
    cards = _cards(values)
    if not cards:
        return None
    lead = _given_suit(lead_suit, cards, "the lead suit") or cards[0][1]
    trump = _given_suit(trump, cards, "the trump")
    trumps = [c for c in cards if trump and c[1] == trump]
    candidates = trumps or [c for c in cards if c[1] == lead]
    if not candidates:
        return None
    return max(candidates, key=lambda c: _sortable(c[0]))[2]


def follow_suit(hand: Any, lead_suit: str | None) -> list[Any]:
    """The cards of ``hand`` that may legally be played: those of the suit led when there are any, else all."""
    cards = _cards(hand)
    lead = _given_suit(lead_suit, cards, "the lead suit")
    if not lead:
        return [c[2] for c in cards]
    matching = [c[2] for c in cards if c[1] == lead]
    return matching or [c[2] for c in cards]


# ---------------------------------------------------------------------------
# Expression functions
# ---------------------------------------------------------------------------


def _guard(call: Call, run: Any) -> Any:
    try:
        return run()
    except ValueError as exc:
        raise ExprError(f"${call.name}: {exc}", call.source) from None


def _size(call: Call, index: int) -> int:
    value = call.arg(index, 3)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExprError(f"${call.name}: size must be a whole number ≥ 1, got {value!r}", call.source)
    return value


@function("poker_rank(cards)",
          "The best 5-card poker hand among the cards (e.g. 2 hole cards + 5 on the board): {score, category, "
          "level, name, ranks, best}. A higher score is a better hand; equal scores split.",
          min_args=1, max_args=1, family="game")
def _poker_rank_function(call: Call) -> dict[str, Any]:
    return _guard(call, lambda: poker_rank(call.arg(0)))


@function("poker_hand(hole, board)",
          "What a player's hole cards make with the board, in words: the hand, and whether it uses the hole cards "
          "(\"two pair, kings and sevens, using both hole cards\") or is on the board, shared by everyone.",
          min_args=2, max_args=2, family="game")
def _poker_hand_function(call: Call) -> str:
    return str(_guard(call, lambda: poker_hand(call.arg(0), call.arg(1))))


@function("blackjack_value(cards)", "Blackjack total of the cards: aces 11 unless that busts, faces 10.",
          min_args=1, max_args=1, family="game")
def _blackjack_value_function(call: Call) -> int:
    return int(_guard(call, lambda: blackjack(call.arg(0)))["total"])


@function("blackjack_soft(cards)", "True when the blackjack total counts an ace as 11 (a soft total).",
          min_args=1, max_args=1, family="game")
def _blackjack_soft_function(call: Call) -> bool:
    return bool(_guard(call, lambda: blackjack(call.arg(0)))["soft"])


@function("sets(cards, size?)",
          "Groups (lists) of at least `size` (default 3) cards of one rank, for rummy-like games.",
          min_args=1, max_args=2, family="game")
def _sets_function(call: Call) -> list[list[Any]]:
    size = _size(call, 1)
    return _guard(call, lambda: sets(call.arg(0), size))


@function("runs(cards, size?)",
          "Runs (lists) of at least `size` (default 3) consecutive ranks in one suit, longest first; an ace is high or "
          "low.",
          min_args=1, max_args=2, family="game")
def _runs_function(call: Call) -> list[list[Any]]:
    size = _size(call, 1)
    return _guard(call, lambda: runs(call.arg(0), size))


@function("trick_winner(cards, lead_suit?, trump?)",
          "The card winning a trick (cards in play order): highest trump, else highest of the suit led "
          "(default: the first card's suit). Its `played_by` is the player who played it.",
          min_args=1, max_args=3, family="game")
def _trick_winner_function(call: Call) -> Any:
    return _guard(call, lambda: trick_winner(call.arg(0), call.arg(1) or None, call.arg(2) or None))


@function("follow_suit(hand, lead_suit)",
          "The cards of `hand` that follow the suit led, or the whole hand when it has none of that suit "
          "(or nothing was led).", min_args=2, max_args=2, family="game")
def _follow_suit_function(call: Call) -> list[Any]:
    return _guard(call, lambda: follow_suit(call.arg(0), call.arg(1) or None))
