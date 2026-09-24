"""Discovery, cloning, and loading for reusable behavioral engines."""
from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from importlib.resources import files
from pathlib import Path
from typing import Any


class EngineNotFound(KeyError):
    """An engine id is absent from the installed SDK catalog."""


class EngineUnavailable(RuntimeError):
    """An engine is defined but its reusable implementation is not shipped yet."""


@dataclass(frozen=True)
class EngineSpec:
    """One reusable human-interaction engine."""

    id: str
    title: str
    summary: str
    description: str
    status: str
    path: str | None = None
    resources: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """Whether this SDK version ships a cloneable implementation."""
        return self.path is not None

    def source(self) -> dict[str, Any]:
        """Read this engine's reusable starter contract."""
        if self.path is None:
            raise EngineUnavailable(
                f"engine {self.id!r} has no starter contract in this SDK version; "
                "list the ones that do with fg_env.engines.list_engines(available=True) (fg-env engines)"
            )
        resource = files("fg_env.engines").joinpath(self.path)
        return json.loads(resource.read_text(encoding="utf-8"))

    def materialized_source(self) -> dict[str, Any]:
        """Return a self-contained contract with bundled files inlined."""
        if self.path is None:
            return self.source()
        from ..api import expand, parse
        from ..contract.inputs import resolve_inputs

        resource = Path(str(files("fg_env.engines").joinpath(self.path)))
        contract = parse(resource)
        result = expand(resource)
        resolved = resolve_inputs(contract, data_dir=resource.parent)
        for name, spec in result.get("inputs", {}).items():
            if isinstance(spec, dict) and spec.get("source") is not None:
                spec.pop("source", None)
                spec["default"] = resolved[name]
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "description": self.description,
            "status": self.status,
            "available": self.available,
        }


class EngineCatalog:
    """Immutable view of the behavioral engines shipped in this SDK version."""

    def __init__(self, raw: Mapping[str, Any]):
        self.schema_version = int(raw.get("schema_version", 1))
        self.source = str(raw.get("source") or "")
        self._engines = tuple(_engine(item) for item in raw.get("engines") or ())
        self._by_id = {engine.id: engine for engine in self._engines}

    def __iter__(self) -> Iterator[EngineSpec]:
        return iter(self._engines)

    def __len__(self) -> int:
        return len(self._engines)

    def get(self, engine_id: str) -> EngineSpec:
        try:
            return self._by_id[engine_id]
        except KeyError:
            hint = get_close_matches(str(engine_id), list(self._by_id), n=1)
            fix = f"did you mean {hint[0]!r}? " if hint else ""
            raise EngineNotFound(f"unknown engine {engine_id!r}; {fix}engines: {', '.join(self._by_id)}") from None

    def list(self, *, available: bool | None = None) -> list[EngineSpec]:
        engines = list(self._engines)
        if available is not None:
            engines = [engine for engine in engines if engine.available is available]
        return engines

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "engines": [engine.to_dict() for engine in self._engines],
        }


def _engine(raw: Mapping[str, Any]) -> EngineSpec:
    return EngineSpec(
        id=str(raw["id"]),
        title=str(raw.get("title") or raw["id"]),
        summary=str(raw.get("summary") or ""),
        description=str(raw.get("description") or ""),
        status=str(raw.get("status") or "planned"),
        path=str(raw["path"]) if raw.get("path") else None,
        resources=tuple(str(value) for value in raw.get("resources") or ()),
    )


def _read_catalog() -> EngineCatalog:
    raw = json.loads(files("fg_env.engines").joinpath("manifest.json").read_text(encoding="utf-8"))
    return EngineCatalog(raw)


_CATALOG: EngineCatalog | None = None


def catalog() -> EngineCatalog:
    """Return the engine catalog shipped with the installed SDK version."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _read_catalog()
    return _CATALOG


def list_engines(*, available: bool | None = None) -> list[EngineSpec]:
    """List behavioral engines, optionally filtered by implementation availability."""
    return catalog().list(available=available)


def get(engine_id: str) -> EngineSpec:
    """Resolve one behavioral engine by its stable id."""
    return catalog().get(engine_id)


def _named(source: dict[str, Any], name: str | None) -> dict[str, Any]:
    result = copy.deepcopy(source)
    if name:
        result["name"] = name
    return result


def clone(engine_id: str, destination: str | Path, *, name: str | None = None,
          overwrite: bool = False) -> Path:
    """Clone a reusable engine contract into a project-owned JSON file."""
    engine = get(engine_id)
    path = Path(destination).expanduser()
    resources = [Path(resource) for resource in engine.resources]
    for relative in resources:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe bundled resource path: {relative}")
    existing = [target for target in (path, *(path.parent / r for r in resources)) if target.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to replace existing file: {existing[0]} → pass overwrite=True to replace it, "
                              "or clone to another path")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_named(engine.source(), name), indent=2) + "\n")
    root = files("fg_env.engines").joinpath("starters")
    for relative in resources:
        target = path.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(root.joinpath(*relative.parts).read_bytes())
    return path


def load(engine_id: str, *, inputs: Mapping[str, Any] | None = None, seed: int = 0) -> Any:
    """Load a reusable engine as :class:`fg_env.Env`."""
    engine = get(engine_id)
    if engine.path is None:
        engine.source()  # raise the engine-specific availability error
    from ..api import load as load_contract
    resource = files("fg_env.engines").joinpath(engine.path or "")
    return load_contract(Path(str(resource)), inputs=inputs, seed=seed)
