"""Small, valid media files and a trial contract carrying them, for the asset tests."""
import copy
import json
import struct
import zlib
from pathlib import Path

from fg_env.sdk.participants import RandomAgent


def png(width=4, height=4, color=(200, 30, 30)):
    """A valid PNG of one colour."""
    rows = b"".join(b"\x00" + bytes(color) * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def wav(samples=64):
    """A valid, silent 8 kHz mono WAV."""
    data = b"\x80" * samples
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
            + b"data" + struct.pack("<I", len(data)) + data)


PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
REPORT = "# Inspection\n\n118 of 120 bays passed.\n"

#: A two-sided trial: each attorney sees only its own sealed exhibit until trial opens in round 2, when offered
#: exhibits are posted to the evidence record for everyone; the agreement PDF is in every brief.
TRIAL = {
    "name": "Trial with exhibits",
    "brief": {"rules": "Offer exhibits, then argue.", "attach": "agreement"},
    "clock": {"rounds": 3},
    "assets": {
        "agreement": {"file": "files/agreement.pdf", "caption": "The signed agreement"},
        "report": {"file": "files/report.md", "caption": "Inspection report"},
        "photos": {"folder": "files/photos", "type": "image", "caption": "Photo {name}", "alt": "A weld photo"},
    },
    "world": {"trial_open": False},
    "types": {
        "attorney": {"agent": True, "props": {"side": {"type": "enum", "values": ["plaintiff", "defense"],
                                                       "default": "plaintiff"}}},
        "judge": {"agent": True},
        "exhibit": {"props": {"side": {"type": "enum", "values": ["plaintiff", "defense"], "default": "plaintiff"},
                              "file": {"type": "asset", "private": True}, "offered": False, "title": ""}},
    },
    "entities": {
        "pat": {"type": "attorney"},
        "dana": {"type": "attorney", "props": {"side": "defense"}},
        "ito": {"type": "judge"},
        "p1": {"type": "exhibit", "props": {"file": "report", "title": "Report"}},
        "d1": {"type": "exhibit", "props": {"side": "defense", "file": "photos/seam.png", "title": "Seam"}},
    },
    "records": {"evidence": {"fields": {"text": "text", "file": "asset"}, "show": "{text}"}},
    "actions": {
        "offer": {"by": "attorney", "params": {"exhibit": {"type": "entity", "of": "exhibit",
                                                          "where": "$it.side == $actor.side and not $it.offered"}},
                  "do": "$params.exhibit.offered = true", "private": True, "attach": "$params.exhibit.file"},
        "argue": {"by": "attorney", "params": {"text": {"type": "text", "max_len": 200}}, "do": []},
        "note": {"by": "judge", "do": []},
    },
    "stages": [
        {"name": "preparation", "when": "$round == 1", "actions": ["offer", "note"]},
        {"name": "trial", "when": "$round >= 2", "actions": ["argue", "note"],
         "on_enter": [{"if": "not $world.trial_open", "then": [
             "$world.trial_open = true",
             {"each": "exhibit", "where": "$it.offered", "do": [{"post": "evidence", "text": "$it.title", "file": "$it.file"}]}]}]},
    ],
    "views": {"own": {"for": "attorney", "stages": ["preparation"], "of": "exhibit", "where": "$it.side == $actor.side",
                      "show": "{title}", "attach": "$it.file"}},
}


def trial(tmp_path: Path, contract=None, name="trial.json") -> Path:
    """The trial contract (or ``contract``) written beside its files; returns the contract path."""
    files = tmp_path / "files"
    (files / "photos").mkdir(parents=True, exist_ok=True)
    (files / "agreement.pdf").write_bytes(PDF)
    (files / "report.md").write_text(REPORT, encoding="utf-8")
    (files / "photos" / "seam.png").write_bytes(png(color=(10, 200, 30)))
    (files / "photos" / "dock.png").write_bytes(png(color=(30, 30, 200)))
    path = tmp_path / name
    path.write_text(json.dumps(contract if contract is not None else TRIAL), encoding="utf-8")
    return path


def patched(**changes):
    """A deep copy of the trial contract with top-level sections replaced or merged."""
    data = copy.deepcopy(TRIAL)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return data


class Reader:
    """Reads its brief and update (so files are delivered), then acts at random."""

    concurrent = False

    def __init__(self, seed=0):
        self.random = RandomAgent(seed)

    def __call__(self, wake):
        wake.brief
        wake.update
        self.random(wake)


def hashes(wake_record, where=None):
    return {item["hash"] for item in wake_record.get("assets", []) if where is None or item["in"] == where}


__all__ = ["png", "wav", "PDF", "REPORT", "TRIAL", "trial", "patched", "Reader", "hashes"]
