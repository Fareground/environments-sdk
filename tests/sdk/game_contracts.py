"""Small game contracts shared by the game, gym, fork and clone tests: the shipped example games."""
import json
from pathlib import Path

GAMES = Path(__file__).parents[2] / "examples" / "contracts" / "games"


def load_game(name: str) -> dict:
    """An example game contract as a fresh dict (tests may change their copy)."""
    return json.loads((GAMES / f"{name}.json").read_text())


NIM = load_game("nim")
TIC_TAC_TOE = load_game("tic_tac_toe")
KUHN = load_game("kuhn_poker")
MATCHING_PENNIES = load_game("matching_pennies")
