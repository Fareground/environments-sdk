"""Run results on disk: one JSON document, or JSON lines that stream a long trace.

``result.save("run.json")`` writes ``{"fg_env_result": 1, ...result}``. ``result.save("run.jsonl")`` writes
the same content one piece per line — a header line with everything but the event log and the exposure
log, then ``{"event": ...}``, ``{"text": [hash, text]}``, ``{"wake": ...}`` and ``{"chance": ...}`` lines (and a
fork's ``{"start": ...}``) — so a run with
thousands of wakes can be read line by line. ``RunResult.load(path)`` reads either, and also the plain
JSON ``fg-env run --json`` prints.

A run that knew assets also writes their bytes into a folder beside the file — ``run.json`` → ``run.assets/``, one
file per asset named by its content hash — and loading the result provides that folder again, so the saved run
replays on any machine.
"""
from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Union

if TYPE_CHECKING:
    from .measure import RunResult

__all__ = ["RESULT_FORMAT", "save_result", "load_result", "result_from_dict", "asset_folder"]

#: The version of the saved-result format.
RESULT_FORMAT = 1
_REQUIRED = ("status", "ended_by", "rounds", "seed", "arm", "inputs", "outputs", "metrics", "series")

PathLike = Union[str, "os.PathLike[str]"]


def _jsonl(path: PathLike) -> bool:
    return str(path).endswith(".jsonl")


def asset_folder(path: PathLike) -> Path:
    """Where a saved result keeps its asset files: ``run.json`` → ``run.assets``."""
    target = Path(path)
    return target.with_name(target.name.rsplit(".", 1)[0] + ".assets")


def _save_assets(index: Mapping[str, Any], path: PathLike) -> None:
    from .assets import blobs

    rows = (index or {}).get("assets") or []
    if not rows:
        return
    folder = asset_folder(path)
    folder.mkdir(exist_ok=True)
    for row in rows:
        target = folder / (row["hash"] + Path(str(row.get("path") or row["name"])).suffix.lower())
        if target.exists():
            continue
        try:
            target.write_bytes(blobs.read(row["hash"]))
        except blobs.BlobMissing as exc:
            raise ValueError(f"cannot save asset '{row['id']}' with the result: {exc}") from None


def save_result(result: "RunResult", path: PathLike) -> None:
    data = result.to_dict()
    _save_assets(data.get("assets") or {}, path)
    with open(path, "w", encoding="utf-8") as handle:
        if not _jsonl(path):
            json.dump({"fg_env_result": RESULT_FORMAT, **data}, handle, ensure_ascii=False, default=str)
            handle.write("\n")
            return
        for line in _lines(data):
            handle.write(json.dumps(line, ensure_ascii=False, default=str))
            handle.write("\n")


def _lines(data: Mapping[str, Any]) -> Iterator[Dict[str, Any]]:
    exposures = data["exposures"]
    header = {key: value for key, value in data.items() if key not in ("events", "exposures")}
    yield {"fg_env_result": RESULT_FORMAT, **header, "exposures": bool(exposures)}
    for event in data["events"]:
        yield {"event": event}
    for digest, text in (exposures.get("texts") or {}).items():
        yield {"text": [digest, text]}
    for wake in exposures.get("wakes") or []:
        yield {"wake": wake}
    for pick in exposures.get("chance") or []:
        yield {"chance": pick}
    if exposures.get("start") is not None:
        yield {"start": exposures["start"]}


def load_result(path: PathLike) -> "RunResult":
    shown = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read the result file '{shown}': {exc.strerror or exc}") from None
    except UnicodeDecodeError:
        raise ValueError(f"'{shown}' is not UTF-8 text; pass a file written by result.save()") from None
    try:
        data = _from_lines(text, shown) if _jsonl(path) else json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"'{shown}' is not valid JSON ({exc.msg} at line {exc.lineno}); "
                         "pass a file written by result.save()") from None
    folder = asset_folder(path)
    if folder.is_dir():
        from .assets import blobs

        blobs.provide(folder)
    return result_from_dict(data, shown)


def _from_lines(text: str, shown: str) -> Dict[str, Any]:
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.strip():
            try:
                lines.append((number, json.loads(raw)))
            except json.JSONDecodeError as exc:
                raise ValueError(f"'{shown}' line {number} is not valid JSON ({exc.msg})") from None
    if not lines or not isinstance(lines[0][1], dict) or "fg_env_result" not in lines[0][1]:
        raise ValueError(f"'{shown}' does not start with a result header; pass a .jsonl file written by result.save()")
    data = {key: value for key, value in lines[0][1].items() if key != "exposures"}
    recorded = lines[0][1].get("exposures", False)
    events: List[Any] = []
    texts: Dict[str, str] = {}
    wakes: List[Any] = []
    chance: List[Any] = []
    start: Any = None
    for number, line in lines[1:]:
        if isinstance(line, dict) and "event" in line:
            events.append(line["event"])
        elif isinstance(line, dict) and isinstance(line.get("text"), list) and len(line["text"]) == 2:
            texts[line["text"][0]] = line["text"][1]
        elif isinstance(line, dict) and "wake" in line:
            wakes.append(line["wake"])
        elif isinstance(line, dict) and "chance" in line:
            chance.append(line["chance"])
        elif isinstance(line, dict) and "start" in line:
            start = line["start"]
        else:
            raise ValueError(f"'{shown}' line {number} is not an event, text, wake, chance or start line")
    data["events"] = events
    exposures = {"texts": texts, "wakes": wakes, "chance": chance, **({"start": start} if start is not None else {})}
    data["exposures"] = exposures if recorded else {}
    return data


def result_from_dict(data: Any, source: str = "the result") -> "RunResult":
    """A :class:`RunResult` from its ``to_dict()`` form (a saved file's content)."""
    from .measure import RunResult

    if not isinstance(data, Mapping):
        raise ValueError(f"{source} is not a run result (a JSON object from result.save()), got {type(data).__name__}")
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise ValueError(f"{source} is not a run result: it has no {', '.join(missing)}")
    version = data.get("fg_env_result", RESULT_FORMAT)
    if version != RESULT_FORMAT:
        raise ValueError(f"{source} uses result format {version!r}; this engine reads format {RESULT_FORMAT}")
    known = {field.name for field in fields(RunResult)}
    return RunResult(**{key: value for key, value in data.items() if key in known})
