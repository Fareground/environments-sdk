"""Wordle Duel — sealed-tick competitive Wordle.

Both players race to guess the same target word of the configured length. Each round
both players SUBMIT a guess (sealed — neither sees the other's guess
until both have submitted), then the engine reveals each player's
own per-letter feedback (green / yellow / gray) on their private grid.

Win conditions:
  - First to solve their grid wins.
  - If BOTH players solve in the same round → DRAW.
  - If neither solves after `max_guesses` rounds → DRAW.

Each player only sees THEIR OWN grid (their guesses + feedback). They
DO know how many guesses the opponent has used and whether the opponent
has submitted this round, but never see the opponent's actual guesses
or feedback — otherwise they'd piggyback off the opponent's deductions.

Configurable in pre-match:
  - `max_guesses`: 4–10 (default 6, matching standard Wordle)

Word length is configurable: 4, 5, or 6 letters. Targets are picked
from the corresponding embedded pool; guesses are accepted when they
contain the configured number of alphabetic characters
(we don't enforce dictionary membership on guesses — saves the
LLM-vs-vocabulary headache and matches "hard mode" Wordle's spirit).

Events:
  - `wd_init`           { word_length, max_guesses }                 one-shot
  - `wd_pending`        { player, round }                            guess accepted, waiting on opponent
  - `wd_round`          { round, white_feedback, black_feedback,    both submitted; reveal feedback
                           white_solved, black_solved }
  - `wd_win`            { winner, loser, target_word, rounds_used } terminal
  - `wd_draw`           { reason, target_word, rounds_used }        terminal
  - `wd_resign`         { loser, winner, target_word }               terminal
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


DEFAULT_WORD_LENGTH = 5
MIN_WORD_LENGTH = 4
MAX_WORD_LENGTH = 6
DEFAULT_MAX_GUESSES = 6
MIN_MAX_GUESSES = 4
MAX_MAX_GUESSES = 10

# Legacy alias — many internal helpers still reference WORD_LENGTH.
# Instance-aware helpers use self._word_length.
WORD_LENGTH = DEFAULT_WORD_LENGTH

# Wins-to-clinch is the user-facing knob (matching tic-tac-toe convention).
# wins_needed=1 ⇒ best-of-1, 2 ⇒ best-of-3, 3 ⇒ best-of-5, etc.
DEFAULT_WINS_NEEDED = 2
MIN_WINS_NEEDED = 1
MAX_WINS_NEEDED = 5


# ---------------------------------------------------------------------------
# Word pool — curated 5-letter common English words. Used as both the
# target pool (random pick at seating) and an implicit suggestion that
# the LLM should produce real words (we don't enforce dictionary
# membership on guesses; length + alphabetic only).
# ---------------------------------------------------------------------------

WORD_POOL: List[str] = [
    # A
    "above", "abide", "abuse", "actor", "acute", "adept", "adore", "adult",
    "after", "agent", "agile", "agree", "aisle", "alarm", "alert", "alien",
    "align", "allay", "allot", "allow", "alloy", "alone", "along", "alpha",
    "amber", "ample", "amuse", "angel", "anger", "angle", "ankle", "anvil",
    "apart", "apple", "apply", "argue", "arise", "armor", "array", "arrow",
    "ashes", "aside", "asset", "audio", "audit", "avoid", "awake", "award",
    "aware", "awful",
    # B
    "badge", "baker", "balmy", "banjo", "basic", "batch", "beach", "beard",
    "begin", "below", "bench", "berry", "billy", "binge", "birch", "birth",
    "black", "blade", "blame", "blank", "blast", "blaze", "bleak", "blend",
    "bless", "blind", "blink", "block", "blood", "bloom", "blunt", "blush",
    "board", "boast", "bonus", "boost", "booth", "boxer", "brain", "brake",
    "brand", "brass", "brave", "bread", "break", "breed", "brick", "bride",
    "brief", "briny", "brink", "brisk", "broad", "broil", "broke", "brook",
    "broom", "brown", "brush", "buggy", "build", "built", "bunch", "burst",
    "buyer",
    # C
    "cabin", "cable", "cache", "cameo", "candy", "canoe", "cargo", "carve",
    "catch", "cause", "cease", "chain", "chair", "chalk", "chant", "chaos",
    "charm", "chart", "chase", "cheap", "cheat", "check", "chess", "chest",
    "chief", "child", "chill", "chime", "china", "chirp", "chock", "choir",
    "choke", "chord", "chose", "chunk", "cider", "cigar", "civic", "civil",
    "claim", "clamp", "clang", "clash", "clasp", "class", "clean", "clear",
    "cleft", "click", "cliff", "climb", "cling", "clink", "cloak", "clock",
    "clone", "close", "cloth", "cloud", "clown", "coach", "coast", "cobra",
    "color", "comet", "comic", "could", "couch", "count", "court", "cover",
    "covet", "crack", "craft", "crane", "crash", "crate", "crawl", "craze",
    "crazy", "cream", "creed", "creek", "creep", "crepe", "crept", "crest",
    "crick", "crime", "crisp", "crock", "crook", "cross", "crowd", "crown",
    "crude", "cruel", "crumb", "crunch", "crush", "crust", "curry", "curve",
    "cycle",
    # D
    "daily", "dance", "death", "debit", "decay", "delay", "depth", "diary",
    "dicey", "diner", "ditch", "dizzy", "dodge", "doing", "donor", "doubt",
    "dough", "draft", "drain", "drake", "drama", "drank", "draft", "drawl",
    "dread", "dream", "dress", "drift", "drink", "drive", "drone", "drown",
    "drunk", "dryly", "dunce", "dusty", "dwarf", "dwell",
    # E
    "eager", "early", "earth", "easel", "easy", "eaten", "ebony", "edify",
    "elbow", "elect", "elite", "email", "emote", "empty", "endow", "enemy",
    "enjoy", "enter", "entry", "envoy", "epoch", "equal", "erase", "erupt",
    "evict", "every", "exact", "exalt", "exile", "exist", "extra",
    # F
    "fable", "faint", "fairy", "faith", "fake", "false", "fancy", "fault",
    "favor", "feast", "feign", "field", "fiend", "fifth", "fifty", "fight",
    "final", "first", "fjord", "flair", "flame", "flank", "flare", "flash",
    "flask", "fleck", "fleet", "flesh", "flick", "flier", "flint", "flock",
    "flood", "floor", "floss", "flour", "flown", "fluid", "flung", "flush",
    "flyer", "focus", "foggy", "force", "forge", "forte", "forth", "forty",
    "forum", "found", "frame", "frank", "fraud", "fresh", "fried", "frill",
    "frock", "front", "frost", "frown", "froze", "fruit", "fudge", "fugue",
    "funny",
    # G
    "gable", "gamer", "gauge", "gaunt", "gavel", "ghost", "giant", "girth",
    "given", "glade", "gland", "glare", "glass", "glaze", "gleam", "glean",
    "globe", "gloom", "glory", "gloss", "glove", "glyph", "godly", "going",
    "grace", "grade", "grain", "grand", "grant", "grape", "graph", "grasp",
    "grass", "grate", "grave", "gravy", "graze", "great", "greed", "green",
    "greet", "grief", "grill", "grime", "grind", "gripe", "groan", "groin",
    "groom", "gross", "group", "grout", "grove", "growl", "grown", "gruel",
    "gruff", "grunt", "guard", "guess", "guest", "guide", "guild", "guilt",
    "gusto",
    # H
    "habit", "halve", "handy", "happy", "harsh", "haunt", "havoc", "heart",
    "heath", "heavy", "hedge", "hefty", "helix", "hello", "hence", "henna",
    "herald", "heron", "hippo", "hoard", "hobby", "hoist", "honey", "honor",
    "horde", "horse", "hotel", "hound", "house", "hover", "human", "humid",
    "humor", "hyena", "hyper", "hyrax",
    # I
    "ideal", "image", "imply", "index", "inert", "inlet", "input", "irony",
    "issue", "ivory",
    # J
    "jelly", "jewel", "joint", "joist", "joker", "jolly", "joust", "judge",
    "juice", "juicy", "jumbo", "jumpy", "junky",
    # K
    "karma", "kayak", "kebab", "kelp", "knack", "knead", "knee", "kneel",
    "knelt", "knife", "knock", "knoll", "known", "kudzu", "kyrie",
    # L
    "label", "labor", "ladle", "lake", "lance", "lapse", "large", "laser",
    "later", "laugh", "layer", "leach", "lease", "leash", "least", "leave",
    "ledge", "leech", "legal", "lemon", "lemur", "level", "lever", "light",
    "liken", "liner", "lipid", "liver", "loamy", "loath", "lobby", "local",
    "logic", "loose", "lousy", "lower", "loyal", "lucid", "lucky", "lunar",
    "lunge", "lupus", "lurch", "lurid", "lusty", "lying",
    # M
    "macaw", "macro", "madam", "magic", "major", "maker", "mango", "mania",
    "march", "marsh", "match", "maxim", "maybe", "mayor", "meant", "medal",
    "media", "melon", "merit", "merry", "metal", "meter", "metro", "midst",
    "might", "milky", "mince", "minor", "minus", "mirth", "mixer", "model",
    "modem", "moist", "molar", "money", "month", "moral", "moron", "moths",
    "motif", "motor", "motto", "mount", "mourn", "mouse", "mouth", "movie",
    "moxie", "muddy", "music", "myrrh",
    # N
    "nadir", "naive", "naval", "needy", "nerve", "never", "newly", "niche",
    "night", "ninja", "noble", "nobly", "noise", "noisy", "north", "notch",
    "novel", "nudge", "nurse", "nylon", "nymph",
    # O
    "oasis", "ocean", "octet", "often", "ogled", "olden", "olive", "onset",
    "opera", "opine", "orbit", "order", "organ", "other", "otter", "ought",
    "ounce", "outer", "owner", "ozone",
    # P
    "pace", "pager", "paint", "paler", "panda", "panel", "panic", "paper",
    "parka", "parts", "party", "pasta", "patch", "patio", "pause", "peace",
    "peach", "pearl", "pedal", "penal", "perch", "petal", "phase", "phone",
    "photo", "piano", "picky", "piece", "piety", "pilot", "pinch", "pithy",
    "pivot", "pixel", "pizza", "place", "plain", "plait", "plane", "plank",
    "plant", "plate", "plaza", "plead", "pleat", "pluck", "plumb", "plume",
    "plump", "point", "poise", "poker", "polar", "polka", "ponder", "pouch",
    "pound", "power", "prank", "preen", "price", "pride", "prime", "primp",
    "print", "prior", "prism", "prize", "probe", "prone", "prong", "proof",
    "prose", "proud", "prove", "prowl", "proxy", "prude", "prune", "psalm",
    "pulse", "punch", "pupil", "puree", "purge", "purse", "putty", "pygmy",
    # Q
    "quack", "quail", "quake", "qualm", "quark", "quart", "quash", "queen",
    "query", "quest", "queue", "quick", "quiet", "quill", "quilt", "quirk",
    "quite", "quota", "quote",
    # R
    "rabid", "radar", "radio", "rainy", "raise", "rally", "ranch", "range",
    "rapid", "ratio", "ravel", "reach", "react", "ready", "realm", "reign",
    "relax", "relay", "relic", "remit", "renew", "repay", "rerun", "reset",
    "resin", "retry", "rhino", "rhyme", "rider", "ridge", "rifle", "right",
    "rigid", "rinse", "ripen", "rival", "river", "roach", "roast", "robin",
    "robot", "rocky", "rogue", "rouge", "rough", "round", "rouse", "route",
    "rover", "royal", "rugby", "ruler", "rumor", "runic", "rural", "rusty",
    # S
    "saber", "sable", "sadly", "safer", "sales", "salty", "satin", "sauce",
    "saucy", "sauna", "savor", "savvy", "scald", "scale", "scalp", "scamp",
    "scant", "scare", "scarf", "scary", "scene", "scent", "scion", "scoff",
    "scold", "scoop", "scope", "score", "scorn", "scour", "scout", "scowl",
    "scram", "scrap", "screw", "scrub", "scuba", "scuff", "scull", "seedy",
    "seize", "sense", "sepia", "serve", "setup", "seven", "sever", "shade",
    "shaft", "shake", "shaky", "shale", "shall", "sham", "shame", "shape",
    "share", "shark", "sharp", "sheep", "sheer", "sheet", "shelf", "shell",
    "shied", "shift", "shine", "shiny", "shire", "shirk", "shirt", "shoal",
    "shock", "shone", "shook", "shoot", "shore", "short", "shout", "shove",
    "shown", "shrew", "shrub", "shrug", "shyly", "sieve", "sight", "sigma",
    "silky", "since", "sinew", "siren", "sissy", "sixth", "sixty", "skate",
    "skein", "sketch", "skewer", "skiff", "skill", "skimp", "skirt", "skulk",
    "skull", "slack", "slain", "slang", "slant", "slash", "slate", "sleek",
    "sleep", "sleet", "slept", "slice", "slick", "slime", "slimy", "slope",
    "sloth", "smack", "small", "smart", "smash", "smear", "smelt", "smile",
    "smirk", "smith", "smoke", "smoky", "snack", "snake", "snare", "snarl",
    "sneak", "sneer", "snore", "snort", "snout", "snowy", "soggy", "solar",
    "solid", "solve", "sonic", "sorry", "sound", "south", "space", "spade",
    "spank", "spare", "spark", "spawn", "speak", "spear", "speck", "speed",
    "spell", "spelt", "spend", "spent", "sperm", "spice", "spicy", "spied",
    "spike", "spill", "spine", "spire", "spite", "spoil", "spoke", "spook",
    "spoon", "sport", "spout", "spray", "spree", "spurn", "stack", "staff",
    "stage", "staid", "stain", "stake", "stall", "stamp", "stand", "stank",
    "stare", "stark", "start", "stash", "state", "stave", "stead", "steak",
    "steal", "steam", "steed", "steel", "steep", "steer", "stein", "stern",
    "stick", "stiff", "still", "stilt", "sting", "stink", "stoic", "stole",
    "stomp", "stone", "stood", "stool", "stoop", "store", "stork", "storm",
    "story", "stout", "stove", "strap", "straw", "stray", "strip", "strut",
    "stuck", "study", "stuff", "stung", "stunt", "style", "suave", "sugar",
    "suite", "sulky", "sully", "sumac", "sunny", "super", "surer", "surge",
    "swamp", "swarm", "swash", "swath", "swear", "sweat", "sweep", "sweet",
    "swept", "swift", "swill", "swine", "swing", "swipe", "swirl", "swish",
    "swoon", "swoop", "sword", "swore", "sworn", "swung", "synod", "syrup",
    # T
    "table", "tacit", "taken", "taker", "tally", "tango", "tapir", "tardy",
    "taste", "taunt", "teach", "teary", "teddy", "teeth", "tempo", "tenor",
    "tense", "tepid", "terra", "thank", "theft", "their", "theme", "there",
    "these", "thick", "thief", "thigh", "thing", "think", "third", "thong",
    "thorn", "those", "three", "threw", "throb", "throw", "thumb", "thump",
    "tiara", "tibia", "tidal", "tiger", "tight", "tilde", "timer", "tinge",
    "tipsy", "title", "toast", "today", "token", "tonic", "topaz", "topic",
    "torch", "torso", "total", "totem", "touch", "tough", "towel", "tower",
    "toxic", "trace", "track", "trade", "trail", "train", "trait", "tramp",
    "trash", "trawl", "tread", "treat", "trend", "tress", "trial", "tribe",
    "trick", "tried", "trike", "trill", "trite", "troll", "troop", "trout",
    "truce", "truck", "truly", "trump", "trunk", "trust", "truth", "tubby",
    "tulip", "tunic", "turbo", "tutor", "twang", "tweak", "twice", "twine",
    "twirl", "twist", "tying", "typed",
    # U
    "udder", "ulcer", "ultra", "umbra", "uncle", "under", "undue", "unify",
    "union", "unite", "unity", "until", "unwed", "upper", "upset", "urban",
    "usage", "usher", "usual", "utter",
    # V
    "vague", "valet", "valid", "value", "valve", "vapor", "vault", "vegan",
    "venom", "venue", "verge", "verse", "verso", "video", "vigil", "villa",
    "vinyl", "viola", "viper", "viral", "virus", "visit", "visor", "vital",
    "vivid", "vixen", "vocal", "vodka", "vogue", "voice", "vomit", "voted",
    "voter", "vouch", "vowel",
    # W
    "wager", "wagon", "waist", "waltz", "warty", "waste", "watch", "water",
    "waver", "weary", "weave", "wedge", "weedy", "weigh", "weird", "welsh",
    "wharf", "wheat", "wheel", "where", "which", "while", "whirl", "whisk",
    "white", "whole", "whoop", "whose", "widen", "wield", "wight", "wince",
    "winch", "windy", "wiser", "witch", "witty", "woken", "woman", "women",
    "wordy", "world", "worry", "worse", "worst", "worth", "would", "wound",
    "woven", "wreak", "wreck", "wrest", "wring", "wrist", "write", "wrong",
    "wrote", "wrung", "wryly",
    # X / Y / Z
    "yacht", "yearn", "yeast", "yield", "young", "youth",
    "zebra", "zesty", "zonal",
]

# De-duplicate + length-validate at import time; if the curated list has
# any non-5-letter entries (typos), drop them with a warning.
_VALIDATED_POOL = sorted({
    w for w in WORD_POOL if len(w) == 5 and w.isalpha() and w.islower()
})
if len(_VALIDATED_POOL) != len(WORD_POOL):
    logger.warning(
        "[wd] word pool had %d entries, %d valid after filter — fix typos",
        len(WORD_POOL), len(_VALIDATED_POOL),
    )
WORD_POOL = _VALIDATED_POOL

# 4-letter pool — common English 4-letter words.
WORD_POOL_4: List[str] = sorted({
    w for w in [
        "able", "acid", "aged", "also", "area", "army", "away", "baby", "back",
        "ball", "band", "bank", "base", "bath", "bear", "beat", "been", "beer",
        "bell", "belt", "best", "bike", "bill", "bird", "blow", "blue", "boat",
        "body", "bomb", "bond", "bone", "book", "boom", "born", "boss", "both",
        "bowl", "bulk", "burn", "bush", "busy", "cake", "call", "calm", "came",
        "camp", "card", "care", "case", "cash", "cast", "cell", "chat", "chip",
        "city", "club", "coal", "coat", "code", "cold", "come", "cook", "cool",
        "cope", "copy", "core", "cost", "crew", "crop", "dark", "data", "date",
        "dawn", "days", "dead", "deal", "dean", "dear", "debt", "deep", "deny",
        "desk", "dial", "dice", "diet", "dirt", "dish", "disk", "does", "dome",
        "done", "door", "dose", "down", "draw", "drew", "drop", "drug", "duck",
        "duke", "dull", "duty", "dust", "each", "earn", "ease", "east", "easy",
        "edge", "else", "even", "ever", "evil", "exit", "face", "fact", "fail",
        "fair", "fall", "farm", "fast", "fate", "fear", "feed", "feel", "feet",
        "fell", "felt", "file", "fill", "film", "find", "fine", "fire", "firm",
        "fish", "five", "flag", "flat", "flee", "flew", "flow", "folk", "food",
        "foot", "ford", "form", "fort", "four", "free", "from", "fuel", "full",
        "fund", "gain", "game", "gate", "gave", "gear", "gene", "gift", "girl",
        "give", "glad", "goal", "goes", "gold", "golf", "gone", "good", "gray",
        "grew", "grey", "grip", "grow", "guy", "hail", "hair", "half", "hall",
        "hand", "hang", "hard", "harm", "hate", "have", "head", "hear", "heat",
        "held", "hell", "help", "here", "hero", "hide", "high", "hill", "hint",
        "hire", "hold", "hole", "holy", "home", "hood", "hope", "horn", "host",
        "hour", "huge", "hung", "hunt", "hurt", "icon", "idea", "inch", "into",
        "iron", "item", "join", "joke", "july", "jump", "june", "jury", "just",
        "keen", "keep", "kept", "kick", "kill", "kind", "king", "knee", "knew",
        "know", "lack", "lady", "laid", "lake", "lamp", "land", "lane", "last",
        "late", "lava", "lawn", "lazy", "lead", "lean", "leap", "left", "lend",
        "less", "life", "lift", "like", "limb", "line", "link", "lion", "lips",
        "list", "live", "load", "loan", "lock", "long", "look", "loop", "lord",
        "lose", "loss", "lost", "loud", "love", "luck", "lump", "lung", "made",
        "mail", "main", "make", "male", "many", "mark", "mass", "mate", "math",
        "meal", "mean", "meat", "meet", "memo", "menu", "milk", "mill", "mind",
        "mine", "mint", "miss", "mode", "mood", "moon", "more", "moss", "most",
        "moth", "move", "much", "must", "myth", "nail", "name", "navy", "near",
        "neat", "neck", "need", "nest", "news", "next", "nice", "nine", "none",
        "noon", "nose", "note", "noun", "okay", "once", "only", "onto", "open",
        "oven", "over", "pace", "pack", "page", "paid", "pain", "pair", "pale",
        "palm", "park", "part", "pass", "past", "path", "peak", "pear", "peer",
        "pick", "pile", "pill", "pine", "pink", "pipe", "plan", "play", "plot",
        "plug", "plus", "poem", "poet", "poll", "pond", "pool", "poor", "port",
        "post", "pour", "pray", "prep", "prey", "pull", "pump", "punk", "pure",
        "push", "quit", "quiz", "race", "rage", "rail", "rain", "rank", "rare",
        "rate", "read", "real", "rear", "rely", "rent", "rest", "rice", "rich",
        "ride", "ring", "rise", "risk", "road", "roar", "rock", "role", "roll",
        "roof", "room", "root", "rope", "rose", "rude", "ruin", "rule", "rung",
        "rush", "safe", "said", "sake", "sale", "salt", "same", "sand", "sane",
        "save", "scan", "seal", "seat", "seed", "seek", "seem", "seen", "self",
        "sell", "send", "sent", "shed", "ship", "shoe", "shop", "shot", "show",
        "shut", "sick", "side", "sigh", "sign", "silk", "sing", "sink", "site",
        "size", "skin", "slip", "slow", "snap", "snow", "soap", "soft", "soil",
        "sold", "sole", "some", "song", "sort", "soul", "soup", "spam", "spin",
        "spit", "spot", "star", "stay", "stem", "step", "stir", "stop", "such",
        "suck", "suit", "sung", "sure", "swim", "tail", "take", "tale", "talk",
        "tall", "tame", "tank", "tape", "task", "team", "tear", "tend", "tent",
        "term", "test", "text", "than", "that", "thaw", "thee", "them", "then",
        "they", "thin", "this", "thou", "thus", "tide", "tied", "tile", "till",
        "time", "tiny", "tire", "told", "toll", "tomb", "tone", "took", "tool",
        "torn", "toss", "tour", "town", "tree", "trim", "trip", "true", "tuna",
        "tune", "turn", "twin", "type", "ugly", "unit", "upon", "used", "user",
        "vary", "vast", "vein", "very", "vice", "view", "vine", "void", "vote",
        "wage", "wait", "wake", "walk", "wall", "want", "ward", "warm", "warn",
        "wash", "wave", "ways", "weak", "wear", "weed", "week", "well", "went",
        "were", "west", "what", "when", "whip", "whom", "wide", "wife", "wild",
        "will", "wind", "wine", "wing", "wink", "wipe", "wire", "wise", "wish",
        "with", "wolf", "wood", "wool", "word", "wore", "work", "worm", "worn",
        "wrap", "yard", "yarn", "year", "your", "zero", "zest", "zone",
    ] if len(w) == 4 and w.isalpha() and w.islower()
})

# 6-letter pool — common English 6-letter words.
WORD_POOL_6: List[str] = sorted({
    w for w in [
        "accept", "access", "across", "acting", "action", "active", "actor",
        "actual", "adjust", "admire", "adopt", "advice", "advise", "affair",
        "affect", "afford", "afraid", "agency", "agenda", "agent", "agree",
        "ahead", "almost", "always", "amazed", "amount", "anchor", "animal",
        "answer", "appeal", "appear", "around", "arrest", "arrive", "artist",
        "aspect", "assess", "assist", "assume", "attack", "attend", "august",
        "author", "autumn", "avenue", "awaken", "aware", "awful", "babies",
        "backed", "backup", "ballot", "banana", "banker", "barely", "barrel",
        "basket", "bath", "battle", "beauty", "became", "become", "before",
        "behalf", "behave", "behind", "belief", "belong", "beside", "better",
        "beyond", "binary", "birth", "bishop", "blamed", "blanks", "blocks",
        "blonde", "bloody", "blunt", "boards", "bodies", "bombs", "border",
        "bottle", "bottom", "bought", "bounce", "bound", "branch", "brand",
        "brave", "breach", "bread", "break", "breath", "brick", "bridge",
        "bright", "broken", "bronze", "brutal", "bubble", "bucket", "budget",
        "buyer", "cables", "cactus", "called", "calmly", "camera", "campus",
        "cancel", "cancer", "candle", "canyon", "carbon", "career", "carpet",
        "carrot", "carved", "casino", "casket", "castle", "casual", "caught",
        "ceased", "celery", "cement", "censor", "census", "center", "ceramic",
        "cereal", "chairs", "chalet", "chalky", "chance", "change", "chapel",
        "charge", "charm", "chart", "chased", "cheek", "cheese", "chemist",
        "cherry", "chimps", "choice", "chosen", "church", "cigar", "cinema",
        "circle", "client", "cloack", "closed", "closer", "cloth", "clouds",
        "clover", "clumsy", "coffee", "coffin", "coiled", "coined", "colder",
        "collar", "colony", "column", "combat", "comedy", "comfort", "comic",
        "coming", "comma", "common", "compete", "complain", "complete", "concept",
        "confess", "confine", "confirm", "conform", "confuse", "connect", "conquer",
        "consent", "consider", "constant", "contain", "content", "contest", "context",
        "continue", "contract", "control", "convict", "convince", "cooked", "cooler",
        "copies", "corner", "corona", "corpse", "cosmic", "cotton", "cougar",
        "county", "couple", "course", "courts", "cousin", "covers", "coward",
        "crafty", "cranky", "crater", "crayon", "crazed", "crease", "create",
        "credit", "crewman", "criers", "crimes", "cringe", "crisis", "critic",
        "crouch", "crowds", "crowns", "cruise", "crunchy", "crusty", "crystal",
        "cuddle", "cupids", "curfew", "curl", "curry", "cursed", "cursor",
        "curve", "custom", "cuteness", "cutter", "cycle", "cyclic", "dagger",
        "dahlia", "daily", "daisy", "danced", "danger", "darken", "dared",
        "dashed", "datum", "daughters", "dawn", "dazzle", "dealer", "debate",
        "debris", "debtor", "decade", "decent", "decide", "decoy", "deduct",
        "defeat", "defect", "defend", "defer", "define", "deform", "delays",
        "deluge", "delve", "demand", "demise", "demote", "denial", "denote",
        "depart", "depend", "depict", "deploy", "depth", "deputy", "derive",
        "desert", "design", "desire", "detail", "detect", "detour", "device",
        "devote", "devour", "differ", "digit", "dilate", "dimer", "dimple",
        "dinner", "dipped", "direct", "disarm", "dismal", "diving", "divine",
        "doctor", "dodged", "doggie", "domain", "donate", "double", "dragon",
        "draped", "drawer", "dreamy", "dreary", "driver", "duplex", "duress",
        "during", "easily", "easter", "echoed", "edible", "effect", "effort",
        "eggnog", "either", "elapse", "elastic", "elbows", "elders", "eldest",
        "eleven", "elicit", "embark", "embers", "emerge", "employ", "empty",
        "endure", "energy", "engage", "engine", "enigma", "enjoy", "enroll",
        "ensure", "entire", "entity", "envoys", "epochs", "equal", "equate",
        "equity", "erased", "errand", "escape", "estate", "ethics", "evenly",
        "events", "exalts", "exceed", "except", "excess", "excite", "excuse",
        "exempt", "exhale", "exists", "exotic", "expand", "expect", "expert",
        "expire", "export", "expose", "extend", "extort", "extras", "fabled",
        "fabric", "facets", "facing", "factor", "failed", "fairly", "falcon",
        "fallen", "famine", "famous", "fanned", "farmer", "fasten", "father",
        "faulty", "fearful", "feeble", "feline", "fellow", "female", "fencer",
        "fender", "ferret", "ferry", "fetch", "feudal", "fewer", "fierce",
        "figure", "filing", "filled", "filter", "filthy", "finale", "fiance",
        "finely", "finest", "finger", "finish", "fiscal", "fitted", "fixate",
        "fizzle", "flake", "flamed", "flares", "flask", "fleece", "fleet",
        "flesh", "flicks", "flight", "flimsy", "flinch", "flirty", "floats",
        "flocks", "flooded", "floors", "florist", "flower", "fluent", "fluffy",
        "fluids", "flunky", "fluted", "flying", "fodder", "folder", "follow",
        "fondue", "forage", "forbid", "forced", "forced", "forest", "forge",
        "forget", "formal", "format", "former", "fossil", "fought", "foul",
        "fourth", "framed", "freaks", "freezy", "freight", "french", "frenzy",
        "fresco", "fretted", "fridge", "friend", "fright", "frigid", "frills",
        "fringe", "frisky", "frolic", "frosty", "frothy", "frowny", "frozen",
        "frugal", "fruity", "fudges", "fueled", "fueling", "fuhrer", "fulfil",
        "fully", "fumble", "fumed", "funded", "funeral", "fungus", "funnel",
        "funny", "furious", "furlong", "furrow", "fusion", "futile", "future",
        "fuzzy", "gables", "gadget", "gaffer", "galaxy", "gallon", "gallop",
        "gambit", "gambol", "gander", "garage", "garbed", "garden", "garish",
        "garlic", "garner", "garnet", "gasket", "gasped", "gather", "gauge",
        "gawked", "gazed", "gazebo", "geared", "geckos", "geared", "gender",
        "genius", "gentle", "gently", "gentry", "germs", "gerund", "ghetto",
        "ghosts", "ghoul", "giants", "giddy", "gifted", "giggle", "gilded",
        "ginger", "girdle", "girlie", "glance", "glands", "glassy", "gleeful",
        "glided", "glider", "glimpse", "global", "globes", "gloomy", "gloops",
        "gloves", "glowed", "gluten", "gnarly", "golden", "golfer", "gondola",
        "gondolier", "gorged", "gorges", "gospel", "gossip", "gothic", "gouged",
        "gouges", "gourd", "govern", "graced", "grades", "graphs", "grasps",
        "grassy", "grated", "grater", "graves", "gravel", "grease", "great",
        "greens", "greets", "grids", "griefs", "grilled", "grimace", "grimes",
        "grimly", "grimy", "grinds", "grinned", "grippe", "grisly", "grocer",
        "groove", "groped", "grouch", "ground", "groups", "grouse", "grovel",
        "grower", "growls", "grudge", "gruesome", "grumble", "grungy", "grunts",
        "guards", "guess", "guests", "guides", "guilds", "guilty", "guitar",
        "gunner", "gunshot", "gushed", "gusher", "gusted", "gutter",
    ] if len(w) == 6 and w.isalpha() and w.islower()
})

# Length → pool registry. Used by the module to pick a target.
WORD_POOLS: Dict[int, List[str]] = {
    4: WORD_POOL_4,
    5: WORD_POOL,
    6: WORD_POOL_6,
}


# ---------------------------------------------------------------------------
# Feedback computation
# ---------------------------------------------------------------------------

def compute_feedback(guess: str, target: str) -> List[str]:
    """Return a list of N feedback codes, one per letter:
        'G' = green  (correct letter, correct position)
        'Y' = yellow (correct letter, wrong position)
        '-' = gray   (letter not in word, or duplicate already accounted for)

    Standard Wordle rule for duplicates: count letters greedily — greens
    take their target letters first, then yellows consume the remainder.
    Example: target=APPLE, guess=POPPY
      Position-by-position: P vs A (no), O vs P (no), P vs P (yes—G),
        P vs L (no), Y vs E (no)
      Remaining target letters after greens: A, P, L, E
      Yellows: P[0] → P available → Y. O[1] → not in remaining → -. P[3] → no P left → -. Y[4] → not in remaining → -.
      Result: Y - G - -
    """
    n = len(target)
    fb: List[str] = ["-"] * n
    # Pass 1 — mark greens, track remaining target letters.
    remaining: List[Optional[str]] = list(target)
    for i in range(n):
        if guess[i] == target[i]:
            fb[i] = "G"
            remaining[i] = None
    # Pass 2 — mark yellows from remaining.
    for i in range(n):
        if fb[i] == "G":
            continue
        try:
            j = remaining.index(guess[i])
        except ValueError:
            continue
        fb[i] = "Y"
        remaining[j] = None
    return fb


def is_solved(feedback: List[str]) -> bool:
    return all(c == "G" for c in feedback)


# ---------------------------------------------------------------------------
# Human-readable history + deduced knowledge
#
# LLMs consistently misread compact codes like "--GY-" — they flip
# G/green vs Y/yellow, lose track of position indices, and produce
# downstream guesses that contradict their own prior feedback. We do
# the deduction in the engine and hand the LLM the already-derived
# facts, leaving no room for misinterpretation.
# ---------------------------------------------------------------------------

def _summarize_knowledge(
    history: List[Dict[str, Any]],
) -> Tuple[Dict[int, str], Dict[str, set], set]:
    """Walk a player's guess history and derive three things:

      - `confirmed_positions`: dict mapping position-index → letter for
         every letter that received a GREEN result at any prior guess.
      - `known_in_word`: dict mapping letter → set of position-indices
         where that letter was RULED OUT (i.e. got a yellow elsewhere or
         a gray when other copies are present). A letter is in this
         dict if it received a YELLOW at any point.
      - `known_not_in_word`: set of letters that received only GRAYs
         AND were never seen as green/yellow anywhere in history.

    Returns (confirmed_positions, known_in_word, known_not_in_word).
    """
    confirmed: Dict[int, str] = {}
    in_word: Dict[str, set] = {}
    ever_seen_positive: set = set()  # letters that hit G or Y
    grays_seen: set = set()           # letters that hit '-' anywhere

    for entry in history:
        guess = entry["guess"]
        fb = entry["feedback"]
        for i, (letter, code) in enumerate(zip(guess, fb)):
            if code == "G":
                confirmed[i] = letter
                ever_seen_positive.add(letter)
                # Letter is confirmed at this position; doesn't add to in_word.
                # But it might also be present at another position; we don't
                # speculate that here.
            elif code == "Y":
                ever_seen_positive.add(letter)
                in_word.setdefault(letter, set()).add(i)
            elif code == "-":
                grays_seen.add(letter)

    # known_not_in_word: letters that were gray AND never green/yellow.
    not_in_word = {
        letter for letter in grays_seen
        if letter not in ever_seen_positive
    }
    # If a letter is confirmed at SOME position via green, also drop it
    # from in_word (the LLM only needs "must include but at which pos").
    for letter in list(in_word.keys()):
        if letter in confirmed.values():
            # Letter has a green somewhere — still useful to know other
            # positions it's been ruled out at, BUT we want to convey
            # "this letter MIGHT appear AGAIN at another position too".
            # Conservative: keep it; the LLM should use both signals.
            pass
    return confirmed, in_word, not_in_word


def _result_word(code: str) -> str:
    if code == "G":
        return "GREEN — correct letter at this position"
    if code == "Y":
        return "YELLOW — letter is in the word but NOT at this position"
    return "GRAY — letter not in the word"


def _format_history_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One guess's worth of history, expanded into a per-letter detail
    array the LLM can't misread."""
    guess = entry["guess"]
    fb = entry["feedback"]
    return {
        "guess": guess.upper(),
        "letters": [
            {
                "position": i + 1,                      # 1-indexed for humans
                "letter": guess[i].upper(),
                "result": _result_word(fb[i]),
            }
            for i in range(len(guess))
        ],
    }


def _render_grid_ascii(history: List[Dict[str, Any]], guesses_left: int,
                       word_length: int = DEFAULT_WORD_LENGTH) -> str:
    """A simple visual grid the LLM can scan. One line per guess; ★ for
    green, * for yellow, . for gray. Empty rows shown as `_ _ _ _ _`."""
    blank_row = " ".join("_" for _ in range(word_length))
    lines: List[str] = []
    for i, entry in enumerate(history):
        letter_row = " ".join(entry["guess"][j].upper() for j in range(word_length))
        result_row = " ".join(
            "★" if entry["feedback"][j] == "G"
            else "*" if entry["feedback"][j] == "Y"
            else "."
            for j in range(word_length)
        )
        lines.append(f"  Guess {i + 1}: {letter_row}   →  {result_row}")
    for j in range(guesses_left):
        lines.append(f"  Guess {len(history) + j + 1}: {blank_row}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Input coercion
# ---------------------------------------------------------------------------

def clean_guess(raw: Any) -> Optional[str]:
    """Normalize a guess to a lowercase alphabetic string, or
    None if it can't be coerced into one. Strips spaces / punctuation
    LLMs sometimes include."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    # Strip surrounding quotes the LLM sometimes adds.
    if s and s[0] in "\"'" and s[-1] in "\"'":
        s = s[1:-1].strip()
    # Drop anything non-alphabetic.
    s = "".join(ch for ch in s if ch.isalpha())
    # Length check is performed by the caller (handler uses its
    # instance word_length). Module-level helper only normalizes.
    if not s:
        return None
    return s


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class WordleDuelModule(DomainModule):
    """Sealed-tick competitive Wordle. Both players race the same
    target; first to solve wins; same-round double-solve = draw; neither
    solves after max_guesses = draw."""

    # Engine-level flag: actions in this module's `custom_actions` MUST
    # NOT broadcast speech / reasoning to other agents or the chat
    # panel. Otherwise an LLM's reasoning ("I'm trying CRANE to probe
    # C/R/N") would tell the opponent which letters are confirmed or
    # eliminated — total deduction leak. The engine's resolve path
    # checks this via `_action_suppresses_chat()` and scrubs speech
    # from every downstream event + skips agent_message emission.
    suppress_chat = True

    def __init__(self, name: str = "wordle_duel",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        # Configurable: max guesses (default 6, range 4–10).
        mg = int(p.get("max_guesses") or DEFAULT_MAX_GUESSES)
        self._max_guesses: int = max(MIN_MAX_GUESSES, min(MAX_MAX_GUESSES, mg))
        # Configurable: match length via wins-needed (default 2 = best of 3).
        # Matches tic-tac-toe's convention: wins_needed maps to a slider
        # that displays "best of (N*2-1)" — so 1=best-of-1, 2=best-of-3,
        # 3=best-of-5, etc.
        wn = int(p.get("wins_needed") or DEFAULT_WINS_NEEDED)
        wn = max(MIN_WINS_NEEDED, min(MAX_WINS_NEEDED, wn))
        self._wins_to_clinch: int = wn
        self._best_of: int = wn * 2 - 1             # for narratives + viz
        # Configurable: word length (default 5; supported 4/5/6).
        wl = int(p.get("word_length") or DEFAULT_WORD_LENGTH)
        wl = max(MIN_WORD_LENGTH, min(MAX_WORD_LENGTH, wl))
        if wl not in WORD_POOLS or not WORD_POOLS[wl]:
            wl = DEFAULT_WORD_LENGTH
        self._word_length: int = wl
        self._word_pool: List[str] = WORD_POOLS[wl]
        seed = p.get("rng_seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._target: str = self._rng.choice(self._word_pool)

        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        # Match-level score.
        self._white_games: int = 0
        self._black_games: int = 0
        self._game_number: int = 1
        # Log of completed games — each entry is {target, winner, reason, rounds}.
        self._games_log: List[Dict[str, Any]] = []
        # Per-game state (resets between games).
        self._white_history: List[Dict[str, Any]] = []
        self._black_history: List[Dict[str, Any]] = []
        self._pending_white: Optional[str] = None
        self._pending_black: Optional[str] = None

        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        logger.info(
            "[wd] init max_guesses=%d wins_needed=%d (best-of-%d) target=*****",
            self._max_guesses, self._wins_to_clinch, self._best_of,
        )

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return (f"Wordle Duel — {self._word_length}-letter word race, {self._max_guesses} "
                "guesses max. Sealed-tick rounds: both submit, both reveal.")

    @property
    def custom_actions(self) -> List[str]:
        # No `resign` here — Wordle's submit_guess takes a parameter,
        # which the agent_brain's snap-fallback filter strips out,
        # leaving resign as the ONLY no-param action it can snap to.
        # That caused accidental resignations on any malformed LLM
        # output. Removing resign closes that failure mode entirely;
        # players play until win or max-guesses-exhausted = draw.
        return ["submit_guess"]

    @property
    def required_properties(self) -> List[str]:
        return ["color"]

    # ------------------------------------------------------------------ #
    # Seat assignment
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if len(agents) < 2:
            return
        self._white_id = agents[0].id
        self._black_id = agents[1].id
        for ent, color in ((agents[0], "white"), (agents[1], "black")):
            if hasattr(ent, "properties"):
                ent.properties["color"] = color
                ent.properties["guesses_used"] = 0
                ent.properties["solved"] = False
        self._initialized = True
        logger.info(
            "[wd] seated white=%s black=%s target=%s",
            agents[0].name, agents[1].name, self._target,
        )

    # ------------------------------------------------------------------ #
    # tick — emit init event once
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_seeded = self._initialized
        self._seed_from_state(state)
        if not was_seeded and self._initialized:
            bo_text = (f"best-of-{self._best_of}" if self._best_of > 1
                       else "single game")
            return [{
                "type": "wd_init",
                "word_length": self._word_length,
                "max_guesses": self._max_guesses,
                "best_of": self._best_of,
                "wins_to_clinch": self._wins_to_clinch,
                "match_score": {"white": 0, "black": 0},
                "game_number": 1,
                "narrative": (
                    f"Wordle Duel — {bo_text}, both players race to guess "
                    f"the same {self._word_length}-letter word in "
                    f"{self._max_guesses} tries per game."
                ),
            }]
        return []

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        self._seed_from_state(state)
        if self._terminal is not None:
            logger.info("[wd] filter: terminal — entity=%s returning []", entity_id)
            return []
        if entity_id not in (self._white_id, self._black_id):
            logger.info(
                "[wd] filter: NOT-a-player entity=%s (seated white=%s black=%s)",
                entity_id, self._white_id, self._black_id,
            )
            return [a for a in valid_actions if a not in self.custom_actions]
        out: List[str] = [a for a in valid_actions if a not in self.custom_actions]
        side = self._side_for_entity(entity_id)
        pending = self._has_pending(side)
        if not pending:
            out.append("submit_guess")
        logger.info(
            "[wd] filter: entity=%s side=%s pending=%s pending_w=%r pending_b=%r → %s",
            entity_id, side, pending, self._pending_white, self._pending_black, out,
        )
        return out

    def _side_for_entity(self, entity_id: str) -> str:
        return "white" if entity_id == self._white_id else "black"

    def _has_pending(self, side: str) -> bool:
        return (self._pending_white if side == "white" else self._pending_black) is not None

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        self._seed_from_state(state)
        actor_id = getattr(actor, "id", None)
        if actor_id not in (self._white_id, self._black_id):
            return "Not a player"
        if action_name == "submit_guess":
            side = self._side_for_entity(actor_id)
            if self._has_pending(side):
                return "You already submitted this round — waiting on opponent"
        return None

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", None) or {}
        # Wipe speech so opponents NEVER see this player's chain-of-thought
        # reasoning in their perception. Wordle is a deduction race —
        # leaking "I'm trying CRANE to test C/R/N" would tell the opponent
        # which letters are confirmed/eliminated. The action_resolved
        # event reads result.details["speech"]; clearing it here makes the
        # broadcast event carry no chat content.
        if isinstance(raw, dict):
            raw["speech"] = None
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "submit_guess":
            return self._handle_submit(actor_id, details, state)
        return []

    def _handle_submit(self, actor_id: str, details: Dict[str, Any],
                       state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        raw_guess = details.get("guess") or details.get("word")
        guess = clean_guess(raw_guess)
        logger.info(
            "[wd] submit: actor=%s side=%s raw=%r cleaned=%r details_keys=%s",
            actor_id, side, raw_guess, guess, list(details.keys())[:8],
        )
        if guess is None or len(guess) != self._word_length:
            return [self._invalid(
                actor_id, actor_name, "invalid_guess",
                f"Guess rejected: provide exactly {self._word_length} letters; no guess was played",
            )]
        # Stash as pending.
        if side == "white":
            self._pending_white = guess
        else:
            self._pending_black = guess

        events: List[Dict[str, Any]] = [{
            "type": "wd_pending",
            "player": actor_id,
            "side": side,
            # Spectator viz uses this to render the pending letters live.
            # The OPPONENT agent never sees this — their perception is
            # built from get_perception_data, which only exposes their
            # own grid, not events.
            "guess": guess,
            "round": self._current_round_number(),
            "narrative": (
                f"{actor_name} submits a guess (sealed — waiting on opponent)."
            ),
        }]
        # If both submitted, resolve the round.
        if self._pending_white is not None and self._pending_black is not None:
            events.extend(self._resolve_round(state))
        return events

    def _resolve_round(self, state: Any) -> List[Dict[str, Any]]:
        wg = self._pending_white or ""
        bg = self._pending_black or ""
        w_fb = compute_feedback(wg, self._target)
        b_fb = compute_feedback(bg, self._target)
        self._white_history.append({"guess": wg, "feedback": w_fb})
        self._black_history.append({"guess": bg, "feedback": b_fb})
        self._pending_white = None
        self._pending_black = None

        self._refresh_player_props(state)
        w_solved = is_solved(w_fb)
        b_solved = is_solved(b_fb)
        rounds_used = len(self._white_history)
        white_name = self._name_of(state, self._white_id)
        black_name = self._name_of(state, self._black_id)
        events: List[Dict[str, Any]] = [{
            "type": "wd_round",
            "round": rounds_used,
            "game_number": self._game_number,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "white_guess": wg,
            "white_feedback": w_fb,
            "white_solved": w_solved,
            "black_guess": bg,
            "black_feedback": b_fb,
            "black_solved": b_solved,
            "narrative": (
                f"Round {rounds_used}: {white_name} guessed '{wg}' "
                f"({_fb_str(w_fb)}); "
                f"{black_name} guessed '{bg}' ({_fb_str(b_fb)})."
            ),
        }]
        # Inner-game end check — figure out who (if anyone) won THIS game.
        # In a best-of-N match we accumulate wins; only the match-level
        # terminal events stop play.
        game_winner: Optional[str] = None
        game_reason: Optional[str] = None
        if w_solved and b_solved:
            game_reason = "double_solve"     # tied this game
        elif w_solved:
            game_winner = self._white_id
            game_reason = "solved"
        elif b_solved:
            game_winner = self._black_id
            game_reason = "solved"
        elif rounds_used >= self._max_guesses:
            game_reason = "exhausted"        # tied this game (neither solved)
        else:
            return events                     # game continues — next round

        # ------------------------------------------------------------- #
        # Inner game has ended. Score it and decide whether the MATCH
        # also ends, or whether we reset for the next game.
        # ------------------------------------------------------------- #
        if game_winner == self._white_id:
            self._white_games += 1
        elif game_winner == self._black_id:
            self._black_games += 1
        # (game_winner None on draws — no point awarded.)
        self._games_log.append({
            "game_number": self._game_number,
            "target": self._target,
            "winner": game_winner,
            "reason": game_reason,
            "rounds": rounds_used,
        })

        events.append({
            "type": "wd_game_end",
            "game_number": self._game_number,
            "target_word": self._target,
            "winner": game_winner,
            "reason": game_reason,
            "rounds_used": rounds_used,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "narrative": (
                f"Game {self._game_number} complete (target '{self._target}'). "
                + (f"{self._name_of(state, game_winner)} wins the game."
                   if game_winner else
                   f"Game tied — {game_reason}.")
                + f" Match score: {white_name} {self._white_games}–"
                f"{self._black_games} {black_name}."
            ),
        })

        # Match-level terminal check — first to clinch wins.
        match_winner: Optional[str] = None
        if self._white_games >= self._wins_to_clinch:
            match_winner = self._white_id
        elif self._black_games >= self._wins_to_clinch:
            match_winner = self._black_id
        # Also: ran out of games (best_of cap reached) with no clinch.
        games_played = self._white_games + self._black_games + sum(
            1 for g in self._games_log if g["winner"] is None
        )
        cap_reached = self._game_number >= self._best_of

        if match_winner is not None:
            self._terminal = {"reason": "match_won", "winner": match_winner}
            events.append({
                "event_type": "wd_win",
                "type": "wd_win",
                "winner": match_winner,
                "loser": self._opp_id(match_winner),
                "match_score": {"white": self._white_games, "black": self._black_games},
                "best_of": self._best_of,
                "narrative": (
                    f"{self._name_of(state, match_winner)} clinches the "
                    f"best-of-{self._best_of} match {self._white_games}–"
                    f"{self._black_games}."
                ),
            })
            return events
        # NOTE: best-of-N must produce a winner. If the scheduled N
        # games have been played with the score still tied (e.g. a
        # best-of-3 finished 1–1 because one game was a draw), we keep
        # playing sudden-death extra games until someone is ahead. The
        # match only ends via `wd_win`.

        # Otherwise — reset for the next inner game.
        self._target = self._rng.choice(self._word_pool)
        self._white_history = []
        self._black_history = []
        self._pending_white = None
        self._pending_black = None
        self._game_number += 1
        logger.info(
            "[wd] next game #%d (best_of=%d, score=%d–%d, target=*****)",
            self._game_number, self._best_of, self._white_games, self._black_games,
        )
        # Sudden-death extension if the scheduled best-of-N is already
        # exhausted but the score is tied. The match cannot end on a
        # draw — keep playing extra games until someone leads.
        is_sudden_death = self._game_number > self._best_of
        if is_sudden_death:
            narrative = (
                f"Sudden-death game {self._game_number} — match score "
                f"{self._white_games}–{self._black_games} (best-of-"
                f"{self._best_of} ran tied; play continues until a "
                f"winner emerges)."
            )
        else:
            narrative = (
                f"Starting game {self._game_number} of {self._best_of}. "
                "(New target word; histories reset.)"
            )
        events.append({
            "type": "wd_next_game",
            "game_number": self._game_number,
            "match_score": {"white": self._white_games, "black": self._black_games},
            "best_of": self._best_of,
            "sudden_death": is_sudden_death,
            "narrative": narrative,
        })
        return events

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {
                "role": "spectator",
                "target_word": self._target if self._terminal else None,
                "white_history": list(self._white_history),
                "black_history": list(self._black_history),
                "max_guesses": self._max_guesses,
            }
        side = self._side_for_entity(entity_id)
        own_history = self._white_history if side == "white" else self._black_history
        opp_history = self._black_history if side == "white" else self._white_history
        opp_pending = self._pending_black if side == "white" else self._pending_white
        own_pending = self._pending_white if side == "white" else self._pending_black
        guesses_left = self._max_guesses - len(own_history)

        # Pre-compute the deductions LLMs consistently get wrong if asked
        # to derive them from raw feedback codes themselves.
        confirmed_positions, known_in_word, known_not_in_word = \
            _summarize_knowledge(own_history)
        history_verbose = [_format_history_entry(h) for h in own_history]
        ascii_grid = _render_grid_ascii(own_history, guesses_left,
                                          self._word_length)

        if self._terminal is not None:
            instructions = "Game over."
        elif own_pending is not None:
            instructions = (
                f"You submitted '{own_pending}' this round — sealed. "
                "Waiting on opponent."
                if opp_pending is None else
                f"You submitted '{own_pending}' this round — sealed. "
                "Opponent has also submitted; round about to resolve."
            )
        else:
            constraint_lines: List[str] = []
            if confirmed_positions:
                pos_str = ", ".join(
                    f"position {p + 1} = '{ch.upper()}'"
                    for p, ch in sorted(confirmed_positions.items())
                )
                constraint_lines.append(f"  • CONFIRMED LETTERS: {pos_str}")
            if known_in_word:
                # Letters known in-word but unknown-position. For each,
                # also list which positions you've RULED OUT for them.
                parts = []
                for letter, ruled_out in sorted(known_in_word.items()):
                    if ruled_out:
                        ruled = ", ".join(
                            f"pos {p + 1}" for p in sorted(ruled_out)
                        )
                        parts.append(f"'{letter.upper()}' (NOT at {ruled})")
                    else:
                        parts.append(f"'{letter.upper()}'")
                constraint_lines.append(
                    "  • LETTERS IN WORD (position unknown): " + ", ".join(parts)
                )
            if known_not_in_word:
                constraint_lines.append(
                    "  • LETTERS NOT IN WORD: "
                    + ", ".join(sorted(c.upper() for c in known_not_in_word))
                )
            constraint_block = "\n".join(constraint_lines) if constraint_lines else \
                "  (no constraints yet — first guess.)"

            example = {4: "LION", 5: "STARE", 6: "PLANET"}[self._word_length]

            instructions = (
                f"IT IS YOUR TURN. You are {side.upper()} in Wordle Duel.\n"
                f"\n"
                f"Your goal: guess the secret {self._word_length}-letter "
                f"word in the fewest tries. You have {guesses_left} "
                f"guesses left.\n"
                f"\n"
                f"WHAT YOU KNOW SO FAR (carefully read this before guessing):\n"
                f"{constraint_block}\n"
                f"\n"
                f"YOUR GRID (top = first guess, ★ = correct letter and "
                f"position, * = in word but WRONG position, . = letter not "
                f"in word):\n"
                f"{ascii_grid}\n"
                f"\n"
                f"Pick a {self._word_length}-letter word that:\n"
                f"  1. PLACES every CONFIRMED letter at its KNOWN position\n"
                f"  2. INCLUDES every letter known to be in the word\n"
                f"  3. AVOIDS every letter known NOT to be in the word\n"
                f"  4. AVOIDS placing a 'letters in word' letter at a "
                f"ruled-out position\n"
                f"\n"
                f"YOUR TOOL CALL — output exactly this shape:\n"
                f'  submit_guess(guess="{example}")     # any {self._word_length}-letter word\n'
                f"\n"
                f"The `guess` parameter is REQUIRED and must be a "
                f"{self._word_length}-letter alphabetic string. If you "
                f"omit it or use the wrong length, your guess is rejected. "
                f"Choose your own word, including your first guess. Both players "
                f"submit sealed; feedback is revealed after both have "
                f"submitted."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "match_best_of": self._best_of,
            "match_wins_to_clinch": self._wins_to_clinch,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "game_number": self._game_number,
            "your_history": history_verbose,
            "your_confirmed_positions": {
                str(p + 1): ch.upper()
                for p, ch in confirmed_positions.items()
            },
            "your_letters_in_word": sorted(c.upper() for c in known_in_word),
            "your_letters_not_in_word": sorted(c.upper() for c in known_not_in_word),
            "your_guesses_used": len(own_history),
            "your_guesses_left": guesses_left,
            "your_pending_guess": own_pending,
            "opponent_guesses_used": len(opp_history),
            "opponent_has_submitted_this_round": opp_pending is not None,
            "max_guesses": self._max_guesses,
            "word_length": self._word_length,
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _current_round_number(self) -> int:
        """Returns the 1-indexed round currently being played."""
        return len(self._white_history) + 1

    def _refresh_player_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side in (
            (self._white_id, "white"), (self._black_id, "black"),
        ):
            if not entity_id:
                continue
            ent = state.entities.get(entity_id)
            if not ent or not hasattr(ent, "properties"):
                continue
            hist = self._white_history if side == "white" else self._black_history
            ent.properties["guesses_used"] = len(hist)
            ent.properties["solved"] = (
                len(hist) > 0 and is_solved(hist[-1]["feedback"])
            )

    def _invalid(self, actor_id: str, actor_name: str, reason: str,
                  narrative: str) -> Dict[str, Any]:
        return {
            "type": "wd_invalid",
            "player": actor_id,
            "reason": reason,
            "narrative": f"{actor_name} — {narrative}.",
        }

    def _name_of(self, state: Any, player_id: Optional[str]) -> str:
        if not player_id:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(player_id)
            if ent is not None:
                return getattr(ent, "name", player_id)
        return player_id

    def _opp_id(self, player_id: str) -> Optional[str]:
        if player_id == self._white_id:
            return self._black_id
        if player_id == self._black_id:
            return self._white_id
        return None

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "max_guesses": self._max_guesses,
            "wins_needed": self._wins_to_clinch,
            "best_of": self._best_of,
            "target": self._target,
            "white_id": self._white_id,
            "black_id": self._black_id,
            "white_games": self._white_games,
            "black_games": self._black_games,
            "game_number": self._game_number,
            "games_log": list(self._games_log),
            "white_history": list(self._white_history),
            "black_history": list(self._black_history),
            "pending_white": self._pending_white,
            "pending_black": self._pending_black,
            "terminal": dict(self._terminal) if self._terminal else None,
            "initialized": self._initialized,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "WordleDuelModule":
        params = data.get("params", {}) or {}
        s = data.get("state", {})
        if "max_guesses" in s and "max_guesses" not in params:
            params = {**params, "max_guesses": s["max_guesses"]}
        if "wins_needed" in s and "wins_needed" not in params:
            params = {**params, "wins_needed": s["wins_needed"]}
        elif "best_of" in s and "wins_needed" not in params:
            # Back-compat: older serialized states used best_of.
            bo = int(s["best_of"])
            params = {**params, "wins_needed": (bo // 2) + 1}
        mod = cls(name=data.get("name", "wordle_duel"), params=params)
        if s.get("target"):
            mod._target = str(s["target"])
        mod._white_id = s.get("white_id")
        mod._black_id = s.get("black_id")
        mod._white_games = int(s.get("white_games") or 0)
        mod._black_games = int(s.get("black_games") or 0)
        mod._game_number = int(s.get("game_number") or 1)
        mod._games_log = list(s.get("games_log") or [])
        mod._white_history = list(s.get("white_history") or [])
        mod._black_history = list(s.get("black_history") or [])
        mod._pending_white = s.get("pending_white")
        mod._pending_black = s.get("pending_black")
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._initialized = bool(s.get("initialized", False))
        return mod


def _fb_str(fb: List[str]) -> str:
    """Render a feedback list as a compact 5-char glyph string for narratives."""
    return "".join(fb)
