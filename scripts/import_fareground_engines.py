"""Import Fareground's maintained engine presets into the SDK distribution.

This is deliberately a build-time maintenance tool, not a runtime dependency on
the Fareground application repository.  It copies the current presets and their
legacy domain modules into ``fg_env.engines`` and writes the versioned catalogue
consumed by the public SDK API.

Usage::

    python scripts/import_fareground_engines.py /path/to/fareground
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path


ARENA_PRESETS = {
    "poker_tournament", "spades", "hearts", "mafia", "chess", "checkers",
    "battleship", "connect_five", "wordle_duel", "hangman_duel", "sudoku_duel",
    "rock_paper_scissors", "hex", "go", "rubiks_cube", "tic_tac_toe",
    "backgammon", "royal_game_of_ur", "dominoes", "debate", "rap_battle",
    "roast_battle", "apology_craft", "spyfall", "insider", "uno", "durak",
    "word_association", "trading_crypto", "trading_stocks", "trading_fx",
    "trading_commodities",
}

HIDDEN_PRESETS = {"avalon", "monopoly"}

# These were explicitly removed from the intended behavioural-engine catalogue.
EXCLUDED_PRESETS = {"process_flow", "system_dynamics"}

# These application assets have a complete native starter with the same stable
# engine id. Shipping both would retain an application-only implementation that
# cannot run outside Fareground.
REPLACED_BY_NATIVE = {"exchange"}

# Topic-specific duplicates intentionally consolidated into Exchange or Dispute.
# They must not become SDK engines or starters; callers customize the reusable
# engine instead.
REMOVED_PRESETS = {
    "commodity_market", "crypto_market", "forex_market", "prediction_market",
    "securities_trading", "stock_market", "courtroom_trial",
}

# Product engine boundaries.  Many old assets were named as if every scenario
# were its own engine; the catalogue makes the reusable engine explicit.
ENGINE_FOR_PRESET = {
    "exchange": "exchange",
    "commodity_market": "exchange", "crypto_market": "exchange",
    "forex_market": "exchange", "prediction_market": "exchange",
    "securities_trading": "exchange", "stock_market": "exchange",
    "trading_crypto": "exchange", "trading_stocks": "exchange",
    "trading_fx": "exchange", "trading_commodities": "exchange",
    "auction": "exchange",
    "dispute_trial": "dispute",
    "courtroom_trial": "judged_contest",
    "judged_contest": "judged_contest", "debate": "judged_contest",
    "rap_battle": "judged_contest", "roast_battle": "judged_contest",
    "apology_craft": "judged_contest", "startup_pitch": "judged_contest",
    "parliament": "legislature", "united_nations": "legislature",
    "brainstorming_session": "deliberation", "board_meeting": "deliberation",
    "research_peer_review": "deliberation",
    "negotiation": "negotiation", "sales_call": "negotiation",
    "consumer_focus_group": "population", "election": "population",
    "product_launch": "population",
    "cascade": "network", "social_media_discourse": "network",
    "dating_app": "matching",
    "strategic_play": "strategy", "geopolitics": "strategy",
    "economic_policy": "strategy",
}

ENGINE_TITLES = {
    "market": "Market", "council": "Council", "dispute": "Dispute",
    "exchange": "Exchange", "legislature": "Legislature",
    "judged_contest": "Judged Contest", "deliberation": "Deliberation",
    "negotiation": "Negotiation", "population": "Population",
    "network": "Network", "matching": "Matching", "strategy": "Strategy",
}

NATIVE_STARTERS = {
    "market": "coffee_market.json",
    "council": "forecast_council.json",
    "dispute": "civil_trial.json",
    "exchange": "exchange_flagship.json",
}

# The module registry name and its source folder differ only here.
MODULE_SOURCE = {"trading_pvp": "trading_crypto", "poker": "poker_tournament"}


def _engine_id(preset_id: str, template: dict) -> str:
    if preset_id in ENGINE_FOR_PRESET:
        return ENGINE_FOR_PRESET[preset_id]
    modules = template.get("domain_modules") or []
    if modules:
        return str(modules[0].get("name") or preset_id)
    return preset_id


def _module_sources(template: dict, assets: Path) -> list[str]:
    sources: list[str] = []
    for spec in template.get("domain_modules") or []:
        name = str(spec.get("name") or "")
        source = MODULE_SOURCE.get(name, name)
        if source and (assets / source / "module.py").exists() and source not in sources:
            sources.append(source)
    return sources


def _copy_module(source: Path, destination: Path) -> None:
    text = source.read_text()
    # Bundled modules execute inside this distribution.  The modern package
    # owns the former kernel modules under ``fg_env``.
    text = text.replace("fg_env_kernel", "fg_env")
    destination.write_text(text)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: import_fareground_engines.py /path/to/fareground")
    fareground = Path(sys.argv[1]).expanduser().resolve()
    assets = fareground / "assets"
    if not assets.is_dir():
        raise SystemExit(f"Fareground assets directory not found: {assets}")

    sdk_root = Path(__file__).resolve().parents[1]
    package = sdk_root / "src" / "fg_env" / "engines"
    presets_dir = package / "presets"
    modules_dir = package / "modules"
    starters_dir = package / "starters"
    for path in (presets_dir, modules_dir, starters_dir):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)

    grouped: dict[str, list[dict]] = defaultdict(list)
    copied_modules: set[str] = set()
    for template_path in sorted(assets.glob("*/template.json")):
        preset_id = template_path.parent.name
        if preset_id in EXCLUDED_PRESETS | REPLACED_BY_NATIVE | REMOVED_PRESETS:
            continue
        template = json.loads(template_path.read_text())
        engine_id = _engine_id(preset_id, template)
        module_sources = _module_sources(template, assets)
        for source in module_sources:
            if source in copied_modules:
                continue
            _copy_module(assets / source / "module.py", modules_dir / f"{source}.py")
            copied_modules.add(source)
        target = presets_dir / preset_id
        target.mkdir()
        shutil.copy2(template_path, target / "template.json")
        grouped[engine_id].append({
            "id": preset_id,
            "title": template.get("name") or preset_id,
            "description": template.get("description") or "",
            "product": "arena" if preset_id in ARENA_PRESETS | HIDDEN_PRESETS else "simulation",
            "format": "legacy_template",
            "path": f"presets/{preset_id}/template.json",
            "module_sources": module_sources,
            "deprecated": False,
            "hidden": preset_id in HIDDEN_PRESETS,
        })

    examples = sdk_root / "examples" / "contracts"
    for engine_id, filename in NATIVE_STARTERS.items():
        shutil.copy2(examples / filename, starters_dir / filename)
        resources: list[str] = []
        sidecar = examples / Path(filename).stem
        if sidecar.is_dir():
            for source in sorted(path for path in sidecar.rglob("*") if path.is_file()):
                relative = source.relative_to(examples).as_posix()
                target = starters_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                resources.append(relative)
        grouped[engine_id].insert(0, {
            "id": "sdk",
            "title": f"{ENGINE_TITLES[engine_id]} SDK starter",
            "description": "Native SDK starter used for custom environments.",
            "product": "simulation",
            "format": "contract",
            "path": f"starters/{filename}",
            "module_sources": [],
            "resources": resources,
            "deprecated": False,
            "hidden": False,
        })

    engines = []
    for engine_id, presets in sorted(grouped.items()):
        products = sorted({preset["product"] for preset in presets})
        native = any(preset["format"] == "contract" for preset in presets)
        engines.append({
            "id": engine_id,
            "title": ENGINE_TITLES.get(engine_id, engine_id.replace("_", " ").title()),
            "products": products,
            "status": "native" if native else "legacy_compatible",
            "default_preset": next((p["id"] for p in presets if p["format"] == "contract"), presets[0]["id"]),
            "presets": presets,
        })

    manifest = {
        "schema_version": 1,
        "source": "Fareground/fareground assets",
        "excluded": sorted(EXCLUDED_PRESETS),
        "retired": sorted(REMOVED_PRESETS),
        "replaced_by_native": sorted(REPLACED_BY_NATIVE),
        "engines": engines,
    }
    (package / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"imported {sum(len(v) for v in grouped.values())} presets across {len(engines)} engines")


if __name__ == "__main__":
    main()
