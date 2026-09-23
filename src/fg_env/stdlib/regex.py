"""A small regular-expression engine that runs in linear time.

Patterns compile to a Thompson NFA and run as a Pike VM: every text position advances a set of
at most ``len(program)`` threads, so matching costs O(len(text) × len(program)) whatever the
pattern — nested quantifiers like ``(a+)+`` cannot backtrack catastrophically. There are no
captures, so the engine answers one question: does the pattern occur in the text?

Supported: literals, ``.`` (any character), classes ``[a-z]`` / ``[^0-9]`` with ranges and the
``\\d \\w \\s`` shorthands, ``\\d \\D \\w \\W \\s \\S`` outside classes (ASCII digits, word
characters and whitespace), escaped punctuation (``\\.``), ``\\n \\t``, anchors ``^ $``, groups
``( )`` and ``(?: )``, alternation ``|``, and the quantifiers ``* + ?`` and ``{m}``, ``{m,}``,
``{m,n}``. Lazy or possessive quantifiers, backreferences, lookaround and flags are refused.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable, List, Optional, Sequence, Tuple

__all__ = ["PatternError", "Program", "compile_pattern", "search", "MAX_PATTERN", "MAX_PROGRAM", "MAX_REPEAT"]

#: Longest pattern accepted, in characters.
MAX_PATTERN = 512
#: Most instructions a compiled pattern may have (counted repetition expands copies).
MAX_PROGRAM = 4_096
#: Largest count in ``{m,n}``.
MAX_REPEAT = 1_000

Ranges = Tuple[Tuple[str, str], ...]
Instruction = Tuple  # ("char", ranges, negated) | ("split", a, b) | ("jmp", a) | ("bol",) | ("eol",) | ("match",)

_DIGIT: Ranges = (("0", "9"),)
_WORD: Ranges = (("a", "z"), ("A", "Z"), ("0", "9"), ("_", "_"))
_SPACE: Ranges = tuple((c, c) for c in " \t\n\r\f\v")
_SHORTHAND = {"d": (_DIGIT, False), "D": (_DIGIT, True), "w": (_WORD, False), "W": (_WORD, True),
              "s": (_SPACE, False), "S": (_SPACE, True)}
_CONTROL = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v"}
_SPECIAL = set("\\.^$|?*+()[]{}-/")


class PatternError(ValueError):
    """The pattern is malformed or uses a feature outside the supported subset."""


class Program(tuple):
    """A compiled pattern: a tuple of instructions ending in ``("match",)``."""


class _Parser:
    def __init__(self, pattern: str):
        self.p = pattern
        self.i = 0

    def error(self, message: str) -> PatternError:
        return PatternError(f"{message} at position {self.i} of the pattern")

    def peek(self) -> Optional[str]:
        return self.p[self.i] if self.i < len(self.p) else None

    def parse(self) -> tuple:
        node = self.alternation()
        if self.i < len(self.p):
            raise self.error("unmatched ')'")
        return node

    def alternation(self) -> tuple:
        branches = [self.concat()]
        while self.peek() == "|":
            self.i += 1
            branches.append(self.concat())
        return branches[0] if len(branches) == 1 else ("alt", branches)

    def concat(self) -> tuple:
        items: List[tuple] = []
        while self.peek() not in (None, "|", ")"):
            items.append(self.quantified())
        return ("cat", items)

    def quantified(self) -> tuple:
        node = self.atom()
        quantified = False
        while self.peek() in ("*", "+", "?", "{"):
            if self.peek() == "{" and not self._counted_ahead():
                break
            if quantified:
                raise self.error("a quantifier cannot follow another quantifier (lazy and possessive forms are not supported)")
            if node[0] in ("bol", "eol"):
                raise self.error("an anchor cannot be repeated")
            node = self.quantifier(node)
            quantified = True
        return node

    def _counted_ahead(self) -> bool:
        j = self.p.find("}", self.i)
        body = self.p[self.i + 1:j] if j > 0 else ""
        return bool(body) and all(c.isdigit() or c == "," for c in body) and body[0].isdigit() and body.count(",") <= 1

    def quantifier(self, node: tuple) -> tuple:
        ch = self.p[self.i]
        self.i += 1
        if ch == "*":
            return ("star", node)
        if ch == "+":
            return ("plus", node)
        if ch == "?":
            return ("opt", node)
        end = self.p.index("}", self.i)
        body, self.i = self.p[self.i:end], end + 1
        low_text, _, high_text = body.partition(",")
        low = int(low_text)
        high = low if "," not in body else (int(high_text) if high_text else None)
        if low > MAX_REPEAT or (high is not None and high > MAX_REPEAT):
            raise self.error(f"repetition counts are limited to {MAX_REPEAT}")
        if high is not None and high < low:
            raise self.error(f"{{{body}}} has its maximum below its minimum")
        return ("rep", node, low, high)

    def atom(self) -> tuple:
        ch = self.peek()
        if ch == "(":
            self.i += 1
            if self.p.startswith("?:", self.i):
                self.i += 2
            elif self.peek() == "?":
                raise self.error("lookaround and group flags are not supported; use ( ) or (?: )")
            node = self.alternation()
            if self.peek() != ")":
                raise self.error("missing ')'")
            self.i += 1
            return node
        if ch == "[":
            return self.char_class()
        if ch in ("*", "+", "?"):
            raise self.error(f"'{ch}' has nothing to repeat")
        self.i += 1
        if ch == ".":
            return ("set", (), True)
        if ch == "^":
            return ("bol",)
        if ch == "$":
            return ("eol",)
        if ch == "\\":
            return self.escape(in_class=False)
        return ("set", ((ch, ch),), False)

    def escape(self, in_class: bool) -> tuple:
        if self.i >= len(self.p):
            raise self.error("the pattern ends with a lone backslash")
        ch = self.p[self.i]
        self.i += 1
        if ch in _SHORTHAND:
            ranges, negated = _SHORTHAND[ch]
            if in_class and negated:
                raise self.error(f"\\{ch} is not supported inside [ ]; use [^...] instead")
            return ("set", ranges, negated)
        if ch in _CONTROL:
            literal = _CONTROL[ch]
            return ("set", ((literal, literal),), False)
        if ch in _SPECIAL or not ch.isalnum():
            return ("set", ((ch, ch),), False)
        raise self.error(f"\\{ch} is not supported (no backreferences, word boundaries or other escapes)")

    def char_class(self) -> tuple:
        self.i += 1
        negated = self.peek() == "^"
        if negated:
            self.i += 1
        ranges: List[Tuple[str, str]] = []
        first = True
        while True:
            ch = self.peek()
            if ch is None:
                raise self.error("missing ']'")
            if ch == "]" and not first:
                self.i += 1
                break
            first = False
            low = self._class_char(ranges)
            if low is None:
                continue
            if self.peek() == "-" and self.i + 1 < len(self.p) and self.p[self.i + 1] != "]":
                self.i += 1
                high = self._class_char(ranges)
                if high is None:
                    raise self.error("a range cannot end in a shorthand like \\d")
                if high < low:
                    raise self.error(f"range {low}-{high} is out of order")
                ranges.append((low, high))
            else:
                ranges.append((low, low))
        return ("set", tuple(ranges), negated)

    def _class_char(self, ranges: List[Tuple[str, str]]) -> Optional[str]:
        """One class member: a character (returned) or a shorthand (added to ``ranges``, None returned)."""
        ch = self.p[self.i]
        self.i += 1
        if ch != "\\":
            return ch
        shorthand = self.peek() in _SHORTHAND
        node = self.escape(in_class=True)
        if shorthand:
            ranges.extend(node[1])
            return None
        return str(node[1][0][0])


class _Emitter:
    def __init__(self) -> None:
        self.code: List[list] = []

    def emit(self, *instruction: object) -> int:
        if len(self.code) >= MAX_PROGRAM:
            raise PatternError(f"the pattern is too large once repetitions are expanded (limit {MAX_PROGRAM:,} steps)")
        self.code.append(list(instruction))
        return len(self.code) - 1

    def node(self, node: tuple) -> None:
        kind = node[0]
        if kind == "set":
            self.emit("char", node[1], node[2])
        elif kind in ("bol", "eol"):
            self.emit(kind)
        elif kind == "cat":
            for item in node[1]:
                self.node(item)
        elif kind == "alt":
            self._alternation(node[1])
        elif kind == "star":
            split = self.emit("split", 0, 0)
            self.node(node[1])
            self.emit("jmp", split)
            self.code[split][1:] = [split + 1, len(self.code)]
        elif kind == "plus":
            start = len(self.code)
            self.node(node[1])
            self.emit("split", start, len(self.code) + 1)
        elif kind == "opt":
            split = self.emit("split", 0, 0)
            self.node(node[1])
            self.code[split][1:] = [split + 1, len(self.code)]
        else:
            self._counted(node[1], node[2], node[3])

    def _alternation(self, branches: Sequence[tuple]) -> None:
        jumps: List[int] = []
        for branch in branches[:-1]:
            split = self.emit("split", 0, 0)
            self.node(branch)
            jumps.append(self.emit("jmp", 0))
            self.code[split][1:] = [split + 1, len(self.code)]
        self.node(branches[-1])
        for jump in jumps:
            self.code[jump][1] = len(self.code)

    def _counted(self, item: tuple, low: int, high: Optional[int]) -> None:
        for _ in range(low):
            self.node(item)
        if high is None:
            self.node(("star", item))
            return
        for _ in range(high - low):
            self.node(("opt", item))


@lru_cache(maxsize=512)
def compile_pattern(pattern: str) -> Program:
    """Compile ``pattern`` (cached). Raises :class:`PatternError` naming what is wrong."""
    if len(pattern) > MAX_PATTERN:
        raise PatternError(f"the pattern is {len(pattern)} characters; the limit is {MAX_PATTERN}")
    emitter = _Emitter()
    emitter.node(_Parser(pattern).parse())
    emitter.emit("match")
    return Program(tuple(tuple(step) for step in emitter.code))


def _accepts(step: Instruction, ch: str) -> bool:
    inside = any(low <= ch <= high for low, high in step[1])
    return inside != step[2]


def search(program: Program, text: str, spend: Callable[[int], None]) -> bool:
    """True when the pattern occurs anywhere in ``text``. ``spend(n)`` is told the work done."""
    size = len(text)

    def add(threads: List[int], seen: set, pc: int, pos: int) -> None:
        stack = [pc]
        while stack:
            pc = stack.pop()
            if pc in seen:
                continue
            seen.add(pc)
            step = program[pc]
            kind = step[0]
            if kind == "jmp":
                stack.append(step[1])
            elif kind == "split":
                stack.append(step[2])
                stack.append(step[1])
            elif kind == "bol":
                if pos == 0:
                    stack.append(pc + 1)
            elif kind == "eol":
                if pos == size:
                    stack.append(pc + 1)
            else:
                threads.append(pc)

    threads: List[int] = []
    seen: set = set()
    for pos in range(size + 1):
        add(threads, seen, 0, pos)  # a match may start at any position
        spend(1 + len(seen))
        if any(program[pc][0] == "match" for pc in threads):
            return True
        if pos == size:
            break
        ch = text[pos]
        following: List[int] = []
        following_seen: set = set()
        for pc in threads:
            step = program[pc]
            if step[0] == "char" and _accepts(step, ch):
                add(following, following_seen, pc + 1, pos + 1)
        threads, seen = following, following_seen
    return False
