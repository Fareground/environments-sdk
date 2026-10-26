"""Discovery, cloning, and loading for reusable behavioral engines.

The kernel names no environment of its own: a platform keeps its engines in a folder of its own (a ``manifest.json``
listing them, and their starter contracts) and registers it (:func:`register`). Every engine read here — listed,
cloned, loaded — comes from the folder its manifest is in."""
from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..contract.layout import dumps


class EngineNotFound(KeyError):
    """An engine id is absent from every registered catalog."""


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
    #: The folder ``path`` and ``resources`` are relative to: the catalog's own (where its manifest is).
    root: Path | None = None

    def file(self, relative: str) -> Path:
        """A file of this engine's catalog, by its path relative to the catalog's folder."""
        if self.root is None:
            raise EngineUnavailable(f"engine {self.id!r} belongs to no registered catalog folder")
        return self.root.joinpath(relative)

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
        return json.loads(self.file(self.path).read_text(encoding="utf-8"))

    def materialized_source(self) -> dict[str, Any]:
        """Return a self-contained contract with bundled files inlined."""
        if self.path is None:
            return self.source()
        from ..api import expand, parse
        from ..contract.inputs import resolve_inputs

        resource = self.file(self.path)
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
    """Immutable view of a set of behavioral engines: one catalog folder's, or every registered one's."""

    def __init__(self, raw: Mapping[str, Any], root: Path | None = None):
        self.schema_version = int(raw.get("schema_version", 1))
        self.source = str(raw.get("source") or "")
        self._engines = tuple(_engine(item, root) for item in raw.get("engines") or ())
        self._by_id = {engine.id: engine for engine in self._engines}

    @classmethod
    def from_directory(cls, directory: str | Path) -> EngineCatalog:
        """The catalog a folder holds: its ``manifest.json`` and the starters it names."""
        root = Path(directory).expanduser().resolve()
        manifest = root / "manifest.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"no engine catalog in {root}: it needs a manifest.json listing its engines")
        return cls(json.loads(manifest.read_text(encoding="utf-8")), root)

    @classmethod
    def merged(cls, catalogs: list[EngineCatalog]) -> EngineCatalog:
        """One catalog of several; an engine id named twice is an error, not a silent choice between them."""
        combined = cls({})
        engines: list[EngineSpec] = []
        for each in catalogs:
            for engine in each:
                if engine.id in combined._by_id:
                    raise ValueError(f"engine {engine.id!r} is in two registered catalogs: "
                                     f"{combined._by_id[engine.id].root} and {engine.root}")
                combined._by_id[engine.id] = engine
                engines.append(engine)
        combined._engines = tuple(engines)
        return combined

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


def _engine(raw: Mapping[str, Any], root: Path | None) -> EngineSpec:
    return EngineSpec(
        id=str(raw["id"]),
        title=str(raw.get("title") or raw["id"]),
        summary=str(raw.get("summary") or ""),
        description=str(raw.get("description") or ""),
        status=str(raw.get("status") or "planned"),
        path=str(raw["path"]) if raw.get("path") else None,
        resources=tuple(str(value) for value in raw.get("resources") or ()),
        root=root,
    )


#: The SDK's own catalog folder (it ships none of a platform's environments), then each registered one in order.
_BUNDLED = Path(str(files("fg_env.engines")))
_FOLDERS: list[Path] = []
_CATALOG: EngineCatalog | None = None


def register(directory: str | Path) -> EngineCatalog:
    """Add a platform's catalog folder (a ``manifest.json`` and its starters) to what is listed, cloned and loaded;
    registering the same folder again does nothing. Returns the combined catalog."""
    global _CATALOG
    root = Path(directory).expanduser().resolve()
    if root not in _FOLDERS:
        EngineCatalog.merged([catalog(), EngineCatalog.from_directory(root)])  # refuse a clash before adding it
        _FOLDERS.append(root)
        _CATALOG = None
    return catalog()


def unregister(directory: str | Path) -> None:
    """Remove a folder :func:`register` added (tests, a platform swapping its catalog)."""
    global _CATALOG
    root = Path(directory).expanduser().resolve()
    if root in _FOLDERS:
        _FOLDERS.remove(root)
        _CATALOG = None


def catalog() -> EngineCatalog:
    """Every engine of the SDK's own catalog and the registered ones."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = EngineCatalog.merged([EngineCatalog.from_directory(folder) for folder in (_BUNDLED, *_FOLDERS)])
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
    path.write_text(dumps(_named(engine.source(), name)), encoding="utf-8")
    starters = engine.file(str(Path(engine.path).parent)) if engine.path else engine.file("")
    for relative in resources:
        target = path.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(starters.joinpath(*relative.parts).read_bytes())
    return path


def load(engine_id: str, *, inputs: Mapping[str, Any] | None = None, seed: int = 0) -> Any:
    """Load a reusable engine as :class:`fg_env.Env`."""
    engine = get(engine_id)
    if engine.path is None:
        engine.source()  # raise the engine-specific availability error
    from ..api import load as load_contract
    return load_contract(engine.file(engine.path or ""), inputs=inputs, seed=seed)
