"""The expression standard library: values, edge cases, errors, provenance, determinism and budget."""
import math
import random
import re
import time

import pytest

from fg_env.entity import Entity
from fg_env.sdk.expr import FUNCTIONS, ExprError, Scope, Untrusted, World, evaluate
from fg_env.sdk.guide import guide
from fg_env.sdk.stdlib import regex

APPROX = 1e-9


class _World(World):
    def __init__(self, seed=7, entities=()):
        self.rng = random.Random(seed)
        self._entities = list(entities)

    def entities_of(self, type_name):
        return [e for e in self._entities if e.entity_type == type_name]

    def is_type(self, name):
        return any(e.entity_type == name for e in self._entities)


PEOPLE = [
    Entity("a", "Ann", "person", {"wealth": 1, "score": 3}),
    Entity("b", "Bo", "person", {"wealth": 2, "score": None}),
    Entity("c", "Cy", "person", {"wealth": 3, "score": 9}),
    Entity("d", "Di", "person", {"wealth": 4, "score": 3}),
]


def ev(source, seed=7, **roots):
    return evaluate(source, Scope(roots, _World(seed, PEOPLE)))


def same(actual, expected):
    if isinstance(expected, float):
        return actual == pytest.approx(expected, abs=APPROX)
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(same(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and list(actual) == list(expected) and all(same(actual[k], v) for k, v in expected.items())
    return actual == expected and type(actual) is type(expected) or (actual is None and expected is None)


VALUES = [
    # strings
    ("$split('a, b, c', ', ')", ["a", "b", "c"]),
    ("$split('  two   words ')", ["two", "words"]),
    ("$split('', ',')", [""]),
    ("$chars('héllo')", ["h", "é", "l", "l", "o"]),
    ("$chars('')", []),
    ("$words(\"It's a well-known fact, isn't it?\")", ["It's", "a", "well-known", "fact", "isn't", "it"]),
    ("$upper('abc')", "ABC"),
    ("$title('the big lebowski')", "The Big Lebowski"),
    ("$trim('  x y \\n')", "x y"),
    ("$replace('a-b-c', '-', '+')", "a+b+c"),
    ("$replace('aaa', 'a', '')", ""),
    ("$substr('abcdef', 1, 3)", "bc"),
    ("$substr('abcdef', -2)", "ef"),
    ("$substr('abc', 5)", ""),
    ("$char_at('abc', -1)", "c"),
    ("$starts_with('Hello', 'He')", True),
    ("$starts_with('Hello', 'he')", False),
    ("$ends_with('Hello', 'lo')", True),
    ("$index_of('banana', 'na')", 2),
    ("$index_of('banana', 'x')", -1),
    ("$count_text('banana', 'ana')", 1),
    ("$pad('7', 3, '0')", "007"),
    ("$pad('ab', 5, '.', right)", "ab..."),
    ("$pad('ab', 6, '*', both)", "**ab**"),
    ("$pad('long text', 3)", "long text"),
    ("$repeat_text('ab', 3)", "ababab"),
    ("$repeat_text('ab', 0)", ""),
    ("$matches('crane', '^[a-z]{5}$')", True),
    ("$matches('cranes', '^[a-z]{5}$')", False),
    ("$matches('order #1234', '#\\\\d+')", True),
    ("$matches('', '^$')", True),
    ("$similar('kitten', 'sitting')", 1 - 3 / 7),
    ("$similar('', '')", 1.0),
    ("$similar('abc', 'abc')", 1.0),
    ("$similar('abc', '')", 0.0),
    # word games
    ("$wordle_feedback('speed', 'abide')", ["gray", "gray", "yellow", "gray", "yellow"]),
    ("$wordle_feedback('allee', 'apple')", ["green", "yellow", "gray", "gray", "green"]),
    ("$wordle_feedback('lolly', 'hello')", ["gray", "yellow", "green", "green", "gray"]),
    ("$wordle_feedback('EERIE', 'there')", ["yellow", "gray", "yellow", "gray", "green"]),
    ("$wordle_feedback('crane', 'crane')", ["green"] * 5),
    ("$mask('Ice-cream Sandwich', ['c', 'A'])", "_c_-c__a_ _a____c_"),
    ("$mask('hello', 'LO', '*')", "**llo"),
    ("$mask('hello', [])", "_____"),
    ("$mask('abc', null)", "___"),
    ("$anagram('Dormitory', 'dirty room!')", True),
    ("$anagram('abc', 'abd')", False),
    # sets and maps
    ("$union([1, 2, 2], [3, 1])", [1, 2, 3]),
    ("$union(null, [1])", [1]),
    ("$intersect([3, 1, 2, 3], [2, 3])", [3, 2]),
    ("$difference([1, 2, 3, 1], [2])", [1, 3]),
    ("$is_subset([1, 2], [2, 1, 5])", True),
    ("$is_subset([1, 4], [2, 1])", False),
    ("$is_subset([], [])", True),
    ("$union([[1, 2]], [[1, 2], [2]])", [[1, 2], [2]]),
    ("$merge({a: 1, b: 2}, {b: 3, c: 4})", {"a": 1, "b": 3, "c": 4}),
    ("$without({a: 1, b: 2}, a)", {"b": 2}),
    ("$without({a: 1, b: 2}, [a, b, z])", {}),
    ("$pick_keys({a: 1, b: 2, c: 3}, [c, a, z])", {"c": 3, "a": 1}),
    ("$items({a: 1, b: 2})", [["a", 1], ["b", 2]]),
    # lists
    ("$zip([1, 2, 3], [a, b])", [[1, "a"], [2, "b"]]),
    ("$zip([1], [2], [3])", [[1, 2, 3]]),
    ("$enumerate([x, y], 1)", [[1, "x"], [2, "y"]]),
    ("$chunk([1, 2, 3, 4, 5], 2)", [[1, 2], [3, 4], [5]]),
    ("$window([1, 2, 3, 4], 2)", [[1, 2], [2, 3], [3, 4]]),
    ("$window([1, 2, 3, 4, 5], 3, 2)", [[1, 2, 3], [3, 4, 5]]),
    ("$window([1], 2)", []),
    ("$flatten_deep([1, [2, [3, [4]]], []])", [1, 2, 3, 4]),
    ("$index([a, b, c], c)", 2),
    ("$index([1, 2], 2.0)", 1),
    ("$index([], a)", -1),
    ("$count_of([a, b, a], a)", 2),
    ("$argmax([3, 9, null, 9])", 1),
    ("$argmin([3, 9, null, 1])", 3),
    ("$argmax([])", None),
    ("$argmax(person, $it.score)", 2),
    ("$argmin(person, $it.score)", 0),
    ("$rank([10, 30, 20, 30])", [4, 1, 3, 1]),
    ("$rank(person, $it.score)", [2, None, 1, 2]),
    ("$cumsum([1, 2, 3.5])", [1, 3, 6.5]),
    ("$diff([1, 4, 9])", [3, 5]),
    ("$diff([5])", []),
    ("$rotate([1, 2, 3, 4], 1)", [2, 3, 4, 1]),
    ("$rotate([1, 2, 3, 4], -5)", [4, 1, 2, 3]),
    ("$rotate([], 3)", []),
    ("$insert([1, 3], 1, 2)", [1, 2, 3]),
    ("$insert([1, 2], 2, 3)", [1, 2, 3]),
    ("$insert([], 0, x)", ["x"]),
    ("$remove_at([1, 2, 3], -1)", [1, 2]),
    ("$set_at([1, 2, 3], 0, 9)", [9, 2, 3]),
    # math
    ("$sin($pi() / 2)", 1.0),
    ("$cos(0)", 1.0),
    ("$tan(0)", 0.0),
    ("$atan2(1, 1)", math.pi / 4),
    ("$asin(1)", math.pi / 2),
    ("$acos(1)", 0.0),
    ("$atan(1)", math.pi / 4),
    ("$tanh(0)", 0.0),
    ("$erf(0)", 0.0),
    ("$hypot(3, 4)", 5.0),
    ("$pow(2, 10)", 1024),
    ("$pow(4, 0.5)", 2.0),
    ("$log_base(8, 2)", 3.0),
    ("$log_base(1000, 10)", 3.0),
    ("$sigmoid(0)", 0.5),
    ("$sigmoid(-800)", 0.0),
    ("$sigmoid(2)", 1 / (1 + math.exp(-2))),
    ("$logit(0.5)", 0.0),
    ("$logit(0.8)", math.log(4)),
    ("$sign(-3)", -1),
    ("$sign(0)", 0),
    ("$sign(0.1)", 1),
    ("$lerp(10, 20, 0.25)", 12.5),
    ("$interp(2.5, [1, 2, 3], [10, 20, 40])", 30.0),
    ("$interp(0, [1, 2, 3], [10, 20, 40])", 10),
    ("$interp(9, [1, 2, 3], [10, 20, 40])", 40),
    ("$interp(2, [1, 2, 3], [10, 20, 40])", 20.0),
    ("$gcd(12, 18, 30)", 6),
    ("$gcd(0, 0)", 0),
    ("$lcm(4, 6)", 12),
    ("$lcm(4, 0)", 0),
    ("$factorial(0)", 1),
    ("$factorial(5)", 120),
    ("$comb(5, 2)", 10),
    ("$comb(3, 5)", 0),
    ("$e()", math.e),
    ("$softmax([1, 2, 3])", [math.exp(k) / sum(math.exp(j) for j in (1, 2, 3)) for k in (1, 2, 3)]),
    ("$softmax([1000, 1000])", [0.5, 0.5]),
    ("$softmax([0, 2], 2)", [1 / (1 + math.e), math.e / (1 + math.e)]),
    ("$softmax([])", []),
    ("$logsumexp([1000, 1000])", 1000 + math.log(2)),
    ("$logsumexp([])", None),
    # statistics
    ("$variance([2, 4, 4, 4, 5, 5, 7, 9])", 32 / 7),
    ("$variance([2, null, 4])", 2.0),
    ("$variance([1])", None),
    ("$variance(person, $it.wealth)", 5 / 3),
    ("$variance(person, $it.wealth, $it.wealth > 1)", 1.0),
    ("$cov([1, 2, 3, 4, 5], [2, 4, 5, 4, 5])", 1.5),
    ("$corr([1, 2, 3, 4, 5], [2, 4, 5, 4, 5])", 6 / math.sqrt(60)),
    ("$corr([1, 2, null, 3], [2, 4, 7, 6])", 1.0),
    ("$corr([1, 2, 3], [5, 5, 5])", None),
    ("$cov([1], [2])", None),
    ("$linreg([1, 2, 3, 4, 5], [2, 4, 5, 4, 5])", {"slope": 0.6, "intercept": 2.2, "r2": 0.6, "n": 5}),
    ("$linreg([1, 2], [3, 3])", {"slope": 0.0, "intercept": 3.0, "r2": 1.0, "n": 2}),
    ("$linreg([1, 1], [2, 3])", None),
    ("$gini([1, 2, 3, 4])", 0.25),
    ("$gini([5, 5, 5])", 0.0),
    ("$gini([0, 0, 10])", 2 / 3),
    ("$gini([0, 0])", 0.0),
    ("$gini([])", None),
    ("$gini(person, $it.wealth)", 0.25),
    ("$hhi([50, 30, 20])", 0.38),
    ("$hhi([])", None),
    ("$entropy([0.5, 0.25, 0.25])", 1.5),
    ("$entropy([2, 1, 1, 0])", 1.5),
    ("$entropy({h: 1, t: 1}, $e())", math.log(2)),
    ("$entropy([1])", 0.0),
    ("$entropy([0, 0])", None),
    ("$percentile_rank([1, 2, 3, 4], 3)", 0.625),
    ("$percentile_rank([1, null, 2], 5)", 1.0),
    ("$percentile_rank([], 5)", None),
    ("$zscore(9, [2, 4, 4, 4, 5, 5, 7, 9])", 4 / math.sqrt(32 / 7)),
    ("$zscore(1, [3, 3])", None),
    ("$autocorr([1, 2, 3, 4, 5], 1)", 0.4),
    ("$autocorr([1, 2, 3, 4, 5], 0)", 1.0),
    ("$autocorr([1, 2], 2)", None),
    ("$autocorr([3, 3, 3], 1)", None),
    ("$returns([100, 110, 99])", [0.1, -0.1]),
    ("$returns([100, 110, 99], log)", [math.log(1.1), math.log(0.9)]),
    ("$returns([5])", []),
    ("$ema([1, 2, 3], 0.5)", [1.0, 1.5, 2.25]),
    ("$ema([], 0.5)", []),
    ("$sma([1, 2, 3, 4], 2)", [None, 1.5, 2.5, 3.5]),
    ("$sma([1, 2], 3)", [None, None]),
    ("$drawdown([100, 120, 90, 130, 104])", 0.25),
    ("$drawdown([1, 2, 3])", 0.0),
    ("$histogram([1, 2, 2, 3, 4], 3)", {"edges": [1.0, 2.0, 3.0, 4], "counts": [1, 2, 2]}),
    ("$histogram([1, 5, null, 11], 2, 0, 10)", {"edges": [0.0, 5.0, 10], "counts": [1, 1]}),
    ("$histogram([2, 2], 2)", {"edges": [1.5, 2.0, 2.5], "counts": [0, 2]}),
    ("$histogram([], 2)", {"edges": [0.0, 0.5, 1.0], "counts": [0, 0]}),
    # scoring
    ("$brier(0.7, true)", 0.09),
    ("$brier(0.7, 0)", 0.49),
    ("$brier([0.7, 0.2, 0.1], 0)", 0.14),
    ("$brier({yes: 0.6, no: 0.4}, no)", 0.72),
    ("$log_loss(0.8, true)", -math.log(0.8)),
    ("$log_loss(0.8, false)", -math.log(0.2)),
    ("$log_loss([0.7, 0.2, 0.1], 2)", -math.log(0.1)),
    ("$log_loss({yes: 1, no: 0}, no)", -math.log(1e-15)),
    ("$log_loss(1, false, 0.01)", -math.log(0.01)),
    ("$crps([1, 2, 3], 2)", 2 / 9),
    ("$crps([1, null, 3], 3)", 0.5),
    ("$crps(5, 3)", 2),
    ("$crps([], 3)", None),
    ("$abs_error(3, 5.5)", 2.5),
    ("$abs_error([1, 2, null], [2, 4, 9])", 1.5),
    ("$abs_error([null], [1])", None),
    ("$elo(1500, 1500, 1)", {"a": 1516.0, "b": 1484.0, "expected_a": 0.5}),
    ("$elo(1600, 1400, 0.5)", {"a": 1600 + 32 * (0.5 - 1 / (1 + 10 ** -0.5)),
                               "b": 1400 - 32 * (0.5 - 1 / (1 + 10 ** -0.5)),
                               "expected_a": 1 / (1 + 10 ** -0.5)}),
    ("$elo(1500, 1500, 0, 10)", {"a": 1495.0, "b": 1505.0, "expected_a": 0.5}),
    ("$pool([0.6, 0.8])", 0.7),
    ("$pool([0.6, 0.8], linear, null, [3, 1])", 0.65),
    ("$pool([0.6, 0.8], log)", math.sqrt(0.48) / (math.sqrt(0.48) + math.sqrt(0.08))),
    ("$pool([0.6, 0.8], extremized, 2)", 0.48 / (0.48 + 0.08)),
    ("$pool([0.6, 0.8], extremized)", 0.48 ** 1.25 / (0.48 ** 1.25 + 0.08 ** 1.25)),
    ("$pool([[0.5, 0.3, 0.2], [0.3, 0.3, 0.4]])", [0.4, 0.3, 0.3]),
    ("$pool([{a: 0.2, b: 0.8}, {a: 0.4, b: 0.6}])", {"a": 0.3, "b": 0.7}),
    ("$pool([[1, 0], [0.5, 0.5]], log)", [1.0, 0.0]),
    ("$pool([0.9], log)", 0.9),
]


@pytest.mark.parametrize("source, expected", VALUES, ids=[v[0] for v in VALUES])
def test_values(source, expected):
    actual = ev(source)
    assert same(actual, expected), f"{source} gave {actual!r}, expected {expected!r}"


ERRORS = [
    ("$split(5)", r"\$split: argument 1 must be text"),
    ("$split(null)", r"must be text, got null"),
    ("$split('abc', '')", r"separator cannot be empty"),
    ("$upper([1])", r"\$upper: argument 1 must be text"),
    ("$replace('abc', '', 'x')", r"text to find cannot be empty"),
    ("$substr('abc', 1.5)", r"character position"),
    ("$char_at('abc', 3)", r"out of range"),
    ("$count_text('abc', '')", r"cannot be empty"),
    ("$pad('a', 3, 'ab')", r"exactly one character"),
    ("$pad('a', 3, ' ', middle)", r"left, right or both"),
    ("$pad('a', -1)", r"at least 0"),
    ("$repeat_text('ab', 600000)", r"limit is 1,000,000"),
    ("$repeat_text('ab', -1)", r"at least 0"),
    ("$matches('a', '(ab')", r"missing '\)'"),
    ("$matches('a', 'a**')", r"cannot follow another quantifier"),
    ("$matches('a', 'a*?')", r"cannot follow another quantifier"),
    ("$matches('a', '(?=a)')", r"lookaround"),
    ("$matches('aa', '(a)\\\\1')", r"backreferences"),
    ("$matches('a', '[z-a]')", r"out of order"),
    ("$matches('a', '*a')", r"nothing to repeat"),
    ("$matches('a', 'a{5,2}')", r"maximum below its minimum"),
    ("$matches('a', 'a{2000}')", r"limited to 1000"),
    ("$matches('a', '(((a{1000}){10}){10})')", r"too large"),
    ("$matches('a', '[abc')", r"missing '\]'"),
    ("$matches('a', '^*')", r"anchor cannot be repeated"),
    ("$matches('a', 'a)')", r"unmatched"),
    ("$similar($repeat_text('a', 1001), 'b')", r"up to 1,000 characters"),
    ("$wordle_feedback('abc', 'abcd')", r"3 letters but the answer has 4"),
    ("$wordle_feedback(5, 'abcd')", r"guess text"),
    ("$mask('abc', 5)", r"list of letters"),
    ("$mask('abc', [1])", r"must be text"),
    ("$mask('abc', [], '--')", r"exactly one character"),
    ("$union(5, [1])", r"\$union: argument 1 must be a list"),
    ("$merge({a: 1}, [1])", r"must be a map"),
    ("$without([1], a)", r"must be a map"),
    ("$pick_keys({a: 1}, 5)", r"key or a list of keys"),
    ("$zip([1])", r"wrong number of arguments"),
    ("$chunk([1], 0)", r"at least 1"),
    ("$window([1], 1, 0)", r"at least 1"),
    ("$argmax([1, a])", r"must be numbers"),
    ("$cumsum([1, null])", r"item 1 is null"),
    ("$diff(5)", r"must be a series"),
    ("$insert([1], 3, 0)", r"out of range"),
    ("$remove_at([], 0)", r"out of range"),
    ("$set_at([], 0, 1)", r"empty list"),
    ("$sin(a)", r"must be a number"),
    ("$asin(2)", r"not defined for 2"),
    ("$pow(10, 5000)", r"exponent too large"),
    ("$pow(0, -1)", r"power failed"),
    ("$log_base(0, 2)", r"above 0"),
    ("$log_base(8, 1)", r"not 1"),
    ("$logit(1)", r"strictly between 0 and 1"),
    ("$interp(1, [1, 1], [2, 3])", r"strictly increasing"),
    ("$interp(1, [1, 2], [2])", r"same length"),
    ("$gcd(1.5, 2)", r"whole number"),
    ("$factorial(-1)", r"at least 0"),
    ("$factorial(1000)", r"4,096 bits"),
    ("$comb(10000, 5000)", r"4,096 bits"),
    ("$pi(1)", r"wrong number of arguments"),
    ("$softmax([1], 0)", r"above 0"),
    ("$softmax([1, null])", r"cannot contain nulls"),
    ("$softmax([1, 1000], 1e-310)", r"too small"),
    ("$window($range(3000), 1500)", r"limit is 1,000,000"),
    ("$cov([1, 2], [1])", r"same length"),
    ("$gini([1, -1])", r"must be ≥ 0"),
    ("$entropy([1], 1)", r"not 1"),
    ("$entropy(5)", r"list or map"),
    ("$autocorr([1, 2], -1)", r"at least 0"),
    ("$returns([0, 1])", r"cannot take a simple return"),
    ("$returns([1, -1], log)", r"cannot take a log return"),
    ("$returns([1, 2], weird)", r"simple or log"),
    ("$ema([1], 0)", r"alpha must be in"),
    ("$sma([1], 0)", r"at least 1"),
    ("$drawdown([0, 1])", r"positive peaks"),
    ("$histogram([1], 0)", r"at least 1"),
    ("$histogram([1], 2, 5, 1)", r"above high"),
    ("$brier(1.2, true)", r"between 0 and 1"),
    ("$brier(0.5, 2)", r"binary outcome"),
    ("$brier([0.5, 0.4], 0)", r"sum to 1"),
    ("$brier([0.5, 0.5], 2)", r"position 0..1"),
    ("$brier({a: 1}, b)", r"one of the forecast's keys"),
    ("$brier(yes, true)", r"must be a probability, a list"),
    ("$log_loss(0.5, true, 0)", r"epsilon"),
    ("$crps(a, 1)", r"list of sample values"),
    ("$abs_error([1], [1, 2])", r"same length"),
    ("$elo(1500, 1500, 2)", r"at most 1"),
    ("$pool([])", r"no forecasts"),
    ("$pool([0.5], median)", r"method must be one of"),
    ("$pool([0.5], linear, 2)", r"only to the extremized"),
    ("$pool([0.5, 0.5], linear, null, [1])", r"weights must be 2"),
    ("$pool([[1, 0], [0, 1]], log)", r"undefined"),
    ("$pool([[0.5, 0.5], [1]])", r"same shape"),
    ("$pool([{a: 1}, {b: 1}])", r"same shape"),
    ("$pool([a, b])", r"must all be probabilities"),
    ("$binomial(-1, 0.5)", r"at least 0"),
    ("$binomial(10, 1.5)", r"between 0 and 1"),
    ("$geometric(0)", r"above 0"),
    ("$gamma(0, 1)", r"above 0"),
    ("$weibull(1, -1)", r"above 0"),
    ("$triangular(0, 1, 2)", r"low ≤ mode ≤ high"),
    ("$dirichlet([1, 0])", r"above 0"),
    ("$dirichlet([])", r"non-empty"),
    ("$multinomial(5, [0, 0])", r"at least one weight"),
    ("$multinomial(5, [1, null])", r"without nulls"),
    ("$zipf(0, 1)", r"at least 1"),
    ("$zipf(100001, 1)", r"at most 100,000"),
    ("$truncnormal(0, 0, -1, 1)", r"sd must be above 0"),
    ("$truncnormal(0, 1, 2, 1)", r"above high"),
    ("$dice('3x6')", r"cannot read"),
    ("$dice('2d6 3')", r"joined by \+ or -"),
    ("$dice('')", r"empty"),
    ("$dice('1d0')", r"1 to 1,000,000 sides"),
    ("$dice('2d6kh3')", r"cannot keep 3 of 2"),
    ("$dice('10001d6')", r"at most 10,000 dice"),
    ("$dice(5)", r"dice notation text"),
]


@pytest.mark.parametrize("source, match", ERRORS, ids=[e[0] for e in ERRORS])
def test_errors_name_the_function_and_the_fix(source, match):
    with pytest.raises(ExprError, match=match) as info:
        ev(source)
    assert source in str(info.value)


def test_every_function_is_registered_documented_and_in_the_guide():
    text = guide("all")
    names = {re.match(r"\$(\w+)\(", source).group(1) for source, _ in VALUES + ERRORS}
    names |= {"shuffle", "binomial", "geometric", "gamma", "weibull", "triangular", "dirichlet", "multinomial",
              "zipf", "truncnormal", "dice"}
    for name in names:
        spec = FUNCTIONS[name]
        assert spec.signature.startswith(name + "(") and spec.doc
        assert f"`${spec.signature}`" in text


# --- the safe regular-expression engine ------------------------------------------------------

PATTERNS = [
    "abc", "^abc$", "a.c", "a|b|cd", "(ab)+c", "(?:ab)*$", "colou?r", "x{2,3}", "x{2,}", "^x{3}$",
    "[a-c]+", "[^a-c]", "\\d\\d", "\\w+@\\w+\\.com", "\\s", "\\S+", "\\D", "\\W", "[\\d_]+$", "(a|ab)(c|bcd)",
    "^(a+)+$", "(a*)*b", "a\\.b", "[.]", "[]a]", "[a-]", "\\{", "a{", "^$", "(|a)b", "\\n", "[\\s,]+",
]
TEXTS = ["", "abc", "xabcx", "ac", "abbc", "colour", "color", "xxxx", "xx", "a_1", "me@site.com", "a.b",
         "aaaaaaaaaaaaaaaaaaaaaaaa!", "ababc", "abcd", "]", "-", "{", "tab\there", "line\nbreak", "a, b", "ab"]


@pytest.mark.parametrize("pattern", PATTERNS)
def test_regex_agrees_with_python_re(pattern):
    program = regex.compile_pattern(pattern)
    for text in TEXTS:
        assert regex.search(program, text, lambda n: None) == bool(re.search(pattern, text)), (pattern, text)


def test_regex_has_no_catastrophic_backtracking():
    started = time.perf_counter()
    for pattern in ("^(a+)+$", "^(a|a)*$", "^(a*)*b$", "(x+x+)+y"):
        assert ev(f"$matches($t, '{pattern}')", t="a" * 5000 + "!") is False
    assert time.perf_counter() - started < 5


def test_regex_and_similarity_charge_the_budget():
    with pytest.raises(ExprError, match="work budget"):
        ev("$matches($t, '(a|b|c|d|e|f)*z')", t="abcdef" * 60000)
    with pytest.raises(ExprError, match="work budget"):
        ev("$map($range(4), $len($window($range(1500), 900)))")
    with pytest.raises(ExprError, match="work budget"):
        ev("$sum($map($range(3000), $similar($repeat_text('a', 1000), 'b')))")


def test_flatten_deep_is_bounded():
    nested = [1]
    for _ in range(70):
        nested = [nested]
    with pytest.raises(ExprError, match="nested deeper than 64"):
        ev("$flatten_deep($n)", n=nested)


# --- provenance --------------------------------------------------------------------------------

TAINTED = [
    "$split($t)", "$chars($t)", "$words($t)", "$upper($t)", "$title($t)", "$trim($t)", "$substr($t, 1)",
    "$char_at($t, 0)", "$pad($t, 20)", "$repeat_text($t, 2)", "$replace($t, 'o', '0')", "$replace('plain', 'a', $t)",
    "$mask($t, [o])", "$mask('secret', [], $c)", "$pad('x', 3, $c)", "$union([$t], ['Hello World'])",
    "$merge({a: $t}, {b: 1})", "$zip([$t], [1])", "$set_at([1], 0, $t)",
]


def _all_tainted(value):
    if isinstance(value, str):
        return isinstance(value, Untrusted)
    if isinstance(value, list):
        return all(_all_tainted(v) for v in value if isinstance(v, (str, list, dict)))
    if isinstance(value, dict):
        return any(_all_tainted(v) for v in value.values())
    return False


@pytest.mark.parametrize("source", TAINTED)
def test_text_derived_from_participant_text_stays_marked(source):
    assert _all_tainted(ev(source, t=Untrusted("Hello World"), c=Untrusted("#")))


def test_plain_text_stays_plain_and_labels_are_not_participant_text():
    for source in ("$upper('abc')", "$split('a b')[0]", "$mask('abc', [a])"):
        value = ev(source)
        assert not isinstance(value, Untrusted)
    marks = ev("$wordle_feedback($t, 'crane')", t=Untrusted("CRATE"))
    assert marks == ["green", "green", "green", "gray", "green"] and not any(isinstance(m, Untrusted) for m in marks)


def test_map_keys_keep_their_participant_marker():
    key = Untrusted("alias")
    picked = ev("$pick_keys($m, [alias])", m={key: 1})
    assert isinstance(next(iter(picked)), Untrusted)
    merged = ev("$merge({alias: 0}, $m)", m={key: 1})
    assert merged == {"alias": 1} and isinstance(next(iter(merged)), Untrusted)
    assert ev("$union([alias], $l)", l=[key]) == ["alias"] and isinstance(ev("$union([alias], $l)", l=[key])[0], Untrusted)


# --- distributions -------------------------------------------------------------------------------

DRAWS = [
    "$binomial(20, 0.3)", "$binomial(5000, 0.7)", "$geometric(0.25)", "$gamma(2, 3)", "$weibull(2, 1)",
    "$triangular(0, 10, 5)", "$dirichlet([1, 2, 3])", "$multinomial(10, [1, 1, 2])", "$multinomial(4, {x: 1, y: 3})",
    "$zipf(50, 1.1)", "$truncnormal(0, 1, -1, 2)", "$truncnormal(0, 1, 40, 41)", "$dice('3d6+2')", "$dice('4d6kh3 - d4')",
]


@pytest.mark.parametrize("source", DRAWS)
def test_draws_are_deterministic_under_a_seed(source):
    assert ev(source, seed=3) == ev(source, seed=3)
    assert len({repr(ev(source, seed=s)) for s in range(12)}) > 1


def _mean(source, n=4000):
    scope = Scope({}, _World(11, PEOPLE))
    return sum(evaluate(source, scope) for _ in range(n)) / n


@pytest.mark.parametrize("source, mean, sd", [
    ("$binomial(20, 0.3)", 6.0, math.sqrt(20 * 0.3 * 0.7)),
    ("$binomial(40, 0.9)", 36.0, math.sqrt(40 * 0.9 * 0.1)),
    ("$binomial(100000, 0.5)", 50000.0, math.sqrt(25000)),
    ("$geometric(0.25)", 4.0, math.sqrt(0.75) / 0.25),
    ("$gamma(2, 3)", 6.0, math.sqrt(18)),
    ("$weibull(2, 1)", 2.0, 2.0),
    ("$triangular(0, 10, 5)", 5.0, math.sqrt(25 / 6)),
    ("$zipf(3, 1)", (1 + 2 * 0.5 + 3 / 3) / (1 + 0.5 + 1 / 3), 0.8),
    ("$truncnormal(5, 2, 3, 7)", 5.0, 1.1),
    ("$truncnormal(0, 1, 0, 50)", math.sqrt(2 / math.pi), math.sqrt(1 - 2 / math.pi)),
    ("$dice('3d6+2')", 12.5, math.sqrt(3 * 35 / 12)),
    ("$dice('2d20kh1')", 13.825, 4.7),
])
def test_draws_have_the_right_mean(source, mean, sd):
    assert abs(_mean(source) - mean) < 5 * sd / math.sqrt(4000)


def test_draw_supports_and_shapes():
    for seed in range(40):
        assert 0 <= ev("$binomial(7, 0.5)", seed) <= 7
        assert ev("$geometric(1)", seed) == 1
        probs = ev("$dirichlet([0.5, 1, 4])", seed)
        assert len(probs) == 3 and abs(sum(probs) - 1) < 1e-12 and min(probs) >= 0
        counts = ev("$multinomial(25, [1, 0, 3])", seed)
        assert sum(counts) == 25 and counts[1] == 0
        assert 40 <= ev("$truncnormal(0, 1, 40, 41)", seed) <= 41
        assert -41 <= ev("$truncnormal(0, 1, -41, -40)", seed) <= -40
        assert 1 <= ev("$zipf(10, 2)", seed) <= 10
        assert 3 <= ev("$dice('4d6kl3')", seed) <= 18
    assert ev("$binomial(0, 0.5)") == 0 and ev("$binomial(9, 1)") == 9 and ev("$binomial(9, 0)") == 0
    assert ev("$triangular(2, 2, 2)") == 2.0 and ev("$truncnormal(0, 1, 3, 3)") == 3.0
    assert ev("$dice('7')") == 7 and ev("$dice('d1 + 1d1 - 2')") == 0
    assert set(ev("$multinomial(6, {x: 1, y: 1})")) == {"x", "y"}
    assert ev("$dirichlet([0.0001, 0.0001])", seed=5) in ([1.0, 0.0], [0.0, 1.0]) or \
        abs(sum(ev("$dirichlet([0.0001, 0.0001])", seed=5)) - 1) < 1e-12


def test_randomness_needs_the_run_generator():
    with pytest.raises(ExprError, match="needs randomness"):
        evaluate("$dice('d6')")
