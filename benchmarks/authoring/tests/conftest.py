import sys
from pathlib import Path

# The benchmark's modules import each other by name, as they do when bench.py runs as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
