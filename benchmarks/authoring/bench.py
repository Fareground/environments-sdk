"""Authoring benchmark: how easily does an agent get from a plain-language brief to a correct running environment?

    PYTHONPATH=src python benchmarks/authoring/bench.py                        # every brief, default model
    PYTHONPATH=src python benchmarks/authoring/bench.py --briefs beer_game --model deepseek/deepseek-chat
    PYTHONPATH=src python benchmarks/authoring/bench.py --replay benchmarks/authoring/results/baseline-2026-09-22

A live run needs the ``openai`` package and OPENROUTER_API_KEY (or the fareground .env line); the agent is
:func:`fg_env.author` itself. It writes ``<out>.md`` and ``<out>.json`` (the
scorecard) and ``<out>/<brief>.json`` (one transcript per brief). ``--replay`` re-scores recorded transcripts with
the current SDK and no network: the same contracts, re-checked, re-run and re-asserted.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import fg_env

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from author import api_key, author  # noqa: E402
from checks import BRIEFS  # noqa: E402
from evaluate import evaluate  # noqa: E402

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


def score(slug: str, transcript: Dict[str, Any]) -> Dict[str, Any]:
    """Metrics for one brief from its transcript: re-checks every contract written and evaluates the last one that parsed."""
    errors_per_write: List[List[str]] = []
    for written in transcript["writes"]:
        if not isinstance(written, dict):
            errors_per_write.append(["(contract): not valid JSON"])
            continue
        errors_per_write.append([str(i) for i in fg_env.check(written) if i.severity == "error"])
    clean_at = next((n + 1 for n, errors in enumerate(errors_per_write) if not errors), None)
    final = next((w for w in reversed(transcript["writes"]) if isinstance(w, dict)), None)
    required, checks = BRIEFS[slug]
    if final is None:
        evaluation = {"runs": [], "checks": [{"check": "outputs_present", "passed": False, "why": "no contract"}]}
    else:
        evaluation = evaluate(final, required, checks)
    passed = sum(c["passed"] for c in evaluation["checks"])
    saved = _saved(transcript["writes"], errors_per_write)
    saved_passed = passed if saved is final else (
        sum(c["passed"] for c in evaluate(saved, required, checks)["checks"]) if saved is not None else 0)
    usage = transcript["usage"]
    return {
        "brief": slug, "model": transcript["model"], "stop": transcript["stop"],
        "writes": len(transcript["writes"]), "clean_at": clean_at,
        "final_clean": bool(errors_per_write) and not errors_per_write[-1] and isinstance(transcript["writes"][-1], dict),
        "runs_ok": sum(r["ok"] for r in evaluation["runs"]), "runs": len(evaluation["runs"]),
        "checks_passed": passed, "checks": len(evaluation["checks"]),
        "saved_checks_passed": saved_passed,
        "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
        "cost": round(usage.get("cost", 0.0), 4), "wall_seconds": transcript["wall_seconds"],
        "contract_lines": len(json.dumps(final, indent=2).splitlines()) if final else 0,
        "guide_parts": transcript["guide_parts"], "guide_chars": transcript.get("guide_chars"),
        "check_errors": errors_per_write,
        "failed_checks": [c for c in evaluation["checks"] if not c["passed"]],
        "run_errors": [r for r in evaluation["runs"] if not r["ok"]],
    }


def _saved(writes: List[Any], errors_per_write: List[List[str]]) -> Optional[Dict[str, Any]]:
    """The contract ``fg_env.author`` keeps: the latest write that checks clean and runs once (seed 1)."""
    for written, errors in zip(reversed(writes), reversed(errors_per_write)):
        if isinstance(written, dict) and not errors:
            try:
                fg_env.load(written, seed=1).run(None, budget={"seconds": 60})
            except Exception:
                continue
            return written
    return None


def friction(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check errors grouped by their message with names and numbers blanked, most frequent first."""
    groups: Counter = Counter()
    example: Dict[str, str] = {}
    for row in rows:
        for errors in row["check_errors"]:
            for error in errors:
                message = error.split(": ", 1)[-1]
                key = re.sub(r"\d+", "N", re.sub(r"'[^']*'", "'…'", message.split(" → ")[0]))
                groups[key] += 1
                example.setdefault(key, error)
    return [{"count": n, "message": key, "example": example[key]} for key, n in groups.most_common()]


def scorecard(rows: List[Dict[str, Any]], title: str) -> str:
    sdk = Path(fg_env.__file__).parent  # the SDK being measured, wherever PYTHONPATH points
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=sdk, capture_output=True, text=True).stdout
    lines = [f"# {title}", "", f"SDK commit `{commit.strip() or '?'}` · model `{rows[0]['model'] if rows else '-'}`", "",
             "| brief | clean check (writes) | runs ok | fidelity | tokens in/out | cost | wall s | lines | guide tokens | guide parts |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        clean = f"yes ({r['clean_at']})" if r["clean_at"] else f"no ({r['writes']})"
        guide_tokens = f"{r['guide_chars'] // 4:,}" if r.get("guide_chars") else "-"  # ≈ 4 characters a token
        lines.append(f"| {r['brief']} | {clean} | {r['runs_ok']}/{r['runs']} | {r['checks_passed']}/{r['checks']} | "
                     f"{r['input_tokens']:,}/{r['output_tokens']:,} | ${r['cost']:.3f} | {r['wall_seconds']:.0f} | "
                     f"{r['contract_lines']} | {guide_tokens} | {', '.join(r['guide_parts']) or '-'} |")
    passed, total = sum(r["checks_passed"] for r in rows), sum(r["checks"] for r in rows)
    saved = sum(r.get("saved_checks_passed", r["checks_passed"]) for r in rows)
    lines += ["", f"**Aggregate:** clean check {sum(bool(r['clean_at']) for r in rows)}/{len(rows)} · "
              f"all runs ok {sum(r['runs'] > 0 and r['runs_ok'] == r['runs'] for r in rows)}/{len(rows)} · "
              f"fidelity {passed}/{total} ({passed / max(total, 1):.0%}) · "
              f"fidelity of the contract `fg_env.author` saves {saved}/{total} ({saved / max(total, 1):.0%}) · "
              f"tokens {sum(r['input_tokens'] for r in rows):,} in / {sum(r['output_tokens'] for r in rows):,} out · "
              f"${sum(r['cost'] for r in rows):.3f}", "", "## Check errors authors hit (most frequent first)", ""]
    lines += [f"- {f['count']}× `{f['message']}` — e.g. `{f['example'][:220]}`" for f in friction(rows)[:25]]
    lines += ["", "## Failed fidelity checks", ""]
    for r in rows:
        for c in r["failed_checks"]:
            lines.append(f"- **{r['brief']}** `{c['check']}`: {c['why']}")
        for e in r["run_errors"]:
            lines.append(f"- **{r['brief']}** run {e['participants']} seed {e['seed']} failed: {e['error']}")
    return "\n".join(lines) + "\n"


def compact(transcript: Dict[str, Any]) -> Dict[str, Any]:
    """The transcript without texts that are stored elsewhere or regenerate: the system guide, guide-tool results
    and the contract text of each write_contract call (``writes`` holds it)."""
    guide_ids = {c["id"] for m in transcript["messages"] for c in m.get("tool_calls") or []
                 if c["function"]["name"] == "guide"}
    messages = []
    for m in transcript["messages"]:
        if m["role"] == "system" or m.get("tool_call_id") in guide_ids:
            m = {**m, "content": "(guide text)"}
        if m.get("tool_calls"):
            m = {**m, "tool_calls": [{**c, "function": {**c["function"], "arguments": "(in writes)"}}
                                     if c["function"]["name"] == "write_contract" else c for c in m["tool_calls"]]}
        messages.append(m)
    return {**transcript, "messages": messages}


def record(slugs: List[str], args: argparse.Namespace, out: Path) -> Dict[str, Dict[str, Any]]:
    """Author each brief live (``--jobs`` at a time) and save its transcript. A brief starts only while the run is
    under ``--total-tokens``, so the whole run stays within that cap plus at most ``jobs`` briefs' caps."""
    key, lock, spent = api_key(), threading.Lock(), [0]

    def one(slug: str) -> Optional[Dict[str, Any]]:
        with lock:
            if spent[0] >= args.total_tokens:
                print(f"total token cap reached; skipping {slug}", file=sys.stderr)
                return None
        brief = (HERE / "briefs" / f"{slug}.md").read_text()
        transcript = author(brief, args.model, key, args.brief_tokens, slug)
        with lock:
            spent[0] += transcript["usage"]["input_tokens"] + transcript["usage"]["output_tokens"]
        (out / f"{slug}.json").write_text(json.dumps(compact(transcript), indent=1, default=str))
        return transcript

    with ThreadPoolExecutor(args.jobs) as pool:
        done = dict(zip(slugs, pool.map(one, slugs)))
    return {slug: t for slug, t in done.items() if t is not None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--briefs", help="comma-separated brief names (default: all)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--replay", type=Path, help="folder of recorded transcripts to re-score offline")
    parser.add_argument("--out", type=Path, help="output stem (default: results/run-<date>)")
    parser.add_argument("--jobs", type=int, default=6, help="briefs authored at once")
    parser.add_argument("--brief-tokens", type=int, default=600_000, help="token cap per brief (input + output)")
    parser.add_argument("--total-tokens", type=int, default=5_000_000, help="token cap for the whole run")
    args = parser.parse_args()
    slugs = args.briefs.split(",") if args.briefs else sorted(BRIEFS)
    if args.replay:
        out = args.out or HERE / "results" / f"replay-{datetime.date.today()}"
        transcripts = {s: json.loads((args.replay / f"{s}.json").read_text()) for s in slugs
                       if (args.replay / f"{s}.json").exists()}
    else:
        out = args.out or HERE / "results" / f"run-{datetime.date.today()}"
        out.mkdir(parents=True, exist_ok=True)
        transcripts = record(slugs, args, out)
    rows = []
    for slug, transcript in transcripts.items():
        row = score(slug, transcript)
        rows.append(row)
        print(f"{slug}: clean={row['clean_at']} runs={row['runs_ok']}/{row['runs']} "
              f"fidelity={row['checks_passed']}/{row['checks']} tokens={row['input_tokens']}+{row['output_tokens']}",
              file=sys.stderr)
    title = f"Authoring benchmark — {'replay of ' + args.replay.name if args.replay else out.name}"
    out.with_suffix(".md").write_text(scorecard(rows, title))
    out.with_suffix(".json").write_text(json.dumps(rows, indent=1, default=str))
    print(out.with_suffix(".md"))


if __name__ == "__main__":
    main()
