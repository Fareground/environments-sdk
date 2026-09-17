"""Discovery, cloning, and loading for reusable behavioral engines."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple, Union


class EngineNotFound(KeyError):
    """An engine id is absent from the installed SDK catalog."""


class EngineUnavailable(RuntimeError):
    """An engine is defined but its reusable implementation is not shipped yet."""


@dataclass(frozen=True)
class EngineSpec:
    """One reusable human-interaction engine."""

    id: str
    title: str
    description: str
    status: str
    path: Optional[str] = None
    resources: Tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """Whether this SDK version ships a cloneable implementation."""
        return self.path is not None

    def source(self) -> Dict[str, Any]:
        """Read this engine's reusable starter contract."""
        if self.path is None:
            raise EngineUnavailable(
                f"engine {self.id!r} is planned for Phase 2 and has no reusable "
                "implementation in this SDK version"
            )
        resource = files("fg_env.engines").joinpath(self.path)
        return json.loads(resource.read_text(encoding="utf-8"))

    def materialized_source(self) -> Dict[str, Any]:
        """Return a self-contained contract with bundled files inlined."""
        if self.path is None:
            return self.source()
        from ..sdk.api import expand, parse
        from ..sdk.inputs import resolve_inputs

        resource = Path(str(files("fg_env.engines").joinpath(self.path)))
        contract = parse(resource)
        result = expand(resource)
        resolved = resolve_inputs(contract, data_dir=resource.parent)
        for name, spec in result.get("inputs", {}).items():
            if isinstance(spec, dict) and spec.get("source") is not None:
                spec.pop("source", None)
                spec["default"] = resolved[name]
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
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
            choices = ", ".join(self._by_id)
            raise EngineNotFound(f"unknown engine {engine_id!r}; choose: {choices}") from None

    def list(self, *, available: Optional[bool] = None) -> list[EngineSpec]:
        engines = list(self._engines)
        if available is not None:
            engines = [engine for engine in engines if engine.available is available]
        return engines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "engines": [engine.to_dict() for engine in self._engines],
        }


def _engine(raw: Mapping[str, Any]) -> EngineSpec:
    return EngineSpec(
        id=str(raw["id"]),
        title=str(raw.get("title") or raw["id"]),
        description=str(raw.get("description") or ""),
        status=str(raw.get("status") or "planned"),
        path=str(raw["path"]) if raw.get("path") else None,
        resources=tuple(str(value) for value in raw.get("resources") or ()),
    )


def _read_catalog() -> EngineCatalog:
    raw = json.loads(files("fg_env.engines").joinpath("manifest.json").read_text(encoding="utf-8"))
    return EngineCatalog(raw)


_CATALOG: Optional[EngineCatalog] = None


def catalog() -> EngineCatalog:
    """Return the engine catalog shipped with the installed SDK version."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _read_catalog()
    return _CATALOG


def list_engines(*, available: Optional[bool] = None) -> list[EngineSpec]:
    """List behavioral engines, optionally filtered by implementation availability."""
    return catalog().list(available=available)


def get(engine_id: str) -> EngineSpec:
    """Resolve one behavioral engine by its stable id."""
    return catalog().get(engine_id)


def _named(source: Dict[str, Any], name: Optional[str]) -> Dict[str, Any]:
    result = copy.deepcopy(source)
    if name:
        result["name"] = name
    return result


def clone(engine_id: str, destination: Union[str, Path], *, name: Optional[str] = None,
          overwrite: bool = False) -> Path:
    """Clone a reusable engine contract into a project-owned JSON file."""
    engine = get(engine_id)
    path = Path(destination).expanduser()
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to replace existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_named(engine.source(), name), indent=2) + "\n")
    root = files("fg_env.engines").joinpath("starters")
    for resource in engine.resources:
        relative = Path(resource)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe bundled resource path: {resource}")
        target = path.parent / relative
        if target.exists() and not overwrite:
            raise FileExistsError(f"refusing to replace existing file: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(root.joinpath(*relative.parts).read_bytes())
    return path


def load(engine_id: str, *, inputs: Optional[Mapping[str, Any]] = None, seed: int = 0) -> Any:
    """Load a reusable engine as :class:`fg_env.Env`."""
    engine = get(engine_id)
    if engine.path is None:
        engine.source()  # raise the engine-specific availability error
    from ..sdk.api import load as load_contract
    resource = files("fg_env.engines").joinpath(engine.path or "")
    return load_contract(Path(str(resource)), inputs=inputs, seed=seed)
