"""Discovery, cloning and loading for bundled engine starters."""
from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple, Union


class EngineNotFound(KeyError):
    """An engine or preset id is absent from the installed SDK catalogue."""


@dataclass(frozen=True)
class EnginePreset:
    """One configurable starting point owned by an engine."""

    id: str
    title: str
    description: str
    product: str
    format: str
    path: str
    module_sources: Tuple[str, ...] = ()
    resources: Tuple[str, ...] = ()
    deprecated: bool = False
    hidden: bool = False

    @property
    def native(self) -> bool:
        return self.format == "contract"

    def source(self) -> Dict[str, Any]:
        resource = files("fg_env.engines").joinpath(self.path)
        return json.loads(resource.read_text(encoding="utf-8"))

    def materialized_source(self) -> Dict[str, Any]:
        """Return a self-contained native contract with bundled files inlined."""
        if not self.native:
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


@dataclass(frozen=True)
class EngineSpec:
    """Reusable mechanics plus the presets currently backed by them."""

    id: str
    title: str
    products: Tuple[str, ...]
    status: str
    default_preset: str
    presets: Tuple[EnginePreset, ...]

    def preset(self, preset_id: Optional[str] = None) -> EnginePreset:
        wanted = preset_id or self.default_preset
        for preset in self.presets:
            if preset.id == wanted:
                return preset
        choices = ", ".join(p.id for p in self.presets)
        raise EngineNotFound(f"engine {self.id!r} has no preset {wanted!r}; choose: {choices}")

    def to_dict(self, *, include_presets: bool = True) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "products": list(self.products),
            "status": self.status,
            "default_preset": self.default_preset,
        }
        if include_presets:
            value["presets"] = [
                {
                    "id": p.id,
                    "title": p.title,
                    "description": p.description,
                    "product": p.product,
                    "format": p.format,
                    "deprecated": p.deprecated,
                    "hidden": p.hidden,
                }
                for p in self.presets
            ]
        return value


class EngineCatalog:
    """Immutable view of the engine manifest shipped in this SDK version."""

    def __init__(self, raw: Mapping[str, Any]):
        self.schema_version = int(raw.get("schema_version", 1))
        self.source = str(raw.get("source") or "")
        self.excluded = tuple(str(value) for value in raw.get("excluded") or ())
        self.retired = tuple(str(value) for value in raw.get("retired") or ())
        self.replaced_by_native = tuple(str(value) for value in raw.get("replaced_by_native") or ())
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

    def list(self, *, product: Optional[str] = None, native: Optional[bool] = None) -> list[EngineSpec]:
        engines = list(self._engines)
        if product is not None:
            if product not in {"simulation", "arena"}:
                raise ValueError("product must be 'simulation' or 'arena'")
            engines = [engine for engine in engines if product in engine.products]
        if native is not None:
            engines = [engine for engine in engines
                       if any(preset.native is native for preset in engine.presets)]
        return engines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "excluded": list(self.excluded),
            "retired": list(self.retired),
            "replaced_by_native": list(self.replaced_by_native),
            "engines": [engine.to_dict() for engine in self._engines],
        }


def _preset(raw: Mapping[str, Any]) -> EnginePreset:
    return EnginePreset(
        id=str(raw["id"]),
        title=str(raw.get("title") or raw["id"]),
        description=str(raw.get("description") or ""),
        product=str(raw["product"]),
        format=str(raw["format"]),
        path=str(raw["path"]),
        module_sources=tuple(str(value) for value in raw.get("module_sources") or ()),
        resources=tuple(str(value) for value in raw.get("resources") or ()),
        deprecated=bool(raw.get("deprecated", False)),
        hidden=bool(raw.get("hidden", False)),
    )


def _engine(raw: Mapping[str, Any]) -> EngineSpec:
    return EngineSpec(
        id=str(raw["id"]),
        title=str(raw.get("title") or raw["id"]),
        products=tuple(str(value) for value in raw.get("products") or ()),
        status=str(raw.get("status") or "legacy_compatible"),
        default_preset=str(raw["default_preset"]),
        presets=tuple(_preset(item) for item in raw.get("presets") or ()),
    )


def _read_catalog() -> EngineCatalog:
    raw = json.loads(files("fg_env.engines").joinpath("manifest.json").read_text(encoding="utf-8"))
    return EngineCatalog(raw)


_CATALOG: Optional[EngineCatalog] = None


def catalog() -> EngineCatalog:
    """Return the catalogue shipped with the installed SDK version."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _read_catalog()
    return _CATALOG


def list_engines(*, product: Optional[str] = None, native: Optional[bool] = None) -> list[EngineSpec]:
    """List engines, optionally filtered to simulation/Arena or native starters."""
    return catalog().list(product=product, native=native)


def get(engine_id: str) -> EngineSpec:
    """Resolve one engine by its stable id."""
    return catalog().get(engine_id)


def _named(source: Dict[str, Any], name: Optional[str]) -> Dict[str, Any]:
    result = copy.deepcopy(source)
    if name:
        result["name"] = name
    return result


def clone(engine_id: str, destination: Union[str, Path], *, preset: Optional[str] = None,
          name: Optional[str] = None, overwrite: bool = False) -> Path:
    """Write an engine starter to a project-owned JSON file.

    Native starters produce an SDK contract; compatibility presets produce a
    legacy template.  The returned file is self-contained data.  Domain-module
    implementations remain versioned in the SDK and are loaded by :func:`load`.
    """
    selected = get(engine_id).preset(preset)
    path = Path(destination).expanduser()
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to replace existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_named(selected.source(), name), indent=2) + "\n")
    if selected.native:
        root = files("fg_env.engines").joinpath("starters")
        for resource in selected.resources:
            relative = Path(resource)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe bundled resource path: {resource}")
            target = path.parent / relative
            if target.exists() and not overwrite:
                raise FileExistsError(f"refusing to replace existing file: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(root.joinpath(*relative.parts).read_bytes())
    return path


def _load_module(source: str) -> None:
    module_name = f"fg_env.engines.modules.{source}"
    if module_name in sys.modules:
        return
    resource = files("fg_env.engines").joinpath("modules", f"{source}.py")
    path = Path(str(resource))
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load bundled engine module {source!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    from ..domain_module import DomainModule, DomainModuleRegistry
    registry = DomainModuleRegistry.get_instance()
    for _name, cls in inspect.getmembers(module, inspect.isclass):
        if cls is DomainModule or not issubclass(cls, DomainModule) or cls.__module__ != module.__name__:
            continue
        signature = inspect.signature(cls.__init__)
        parameter = signature.parameters.get("name")
        if parameter is None or parameter.default is inspect.Parameter.empty:
            raise TypeError(f"bundled module {cls.__name__} has no default registration name")
        registry.register(str(parameter.default), cls)


def load(engine_id: str, *, preset: Optional[str] = None, inputs: Optional[Mapping[str, Any]] = None,
         seed: int = 0, decision_fn: Any = None, max_rounds: Optional[int] = None) -> Any:
    """Load a runnable starter from the installed SDK.

    Native starters return :class:`fg_env.Env` and accept ``inputs``.  Existing
    compatibility engines return :class:`fg_env.legacy.World`; their current
    template parameters remain part of the template until each engine is ported
    to the native contract format.  ``decision_fn`` is only for compatibility
    engines and follows the legacy kernel callback contract.
    """
    selected = get(engine_id).preset(preset)
    if selected.native:
        if decision_fn is not None or max_rounds is not None:
            raise ValueError("decision_fn/max_rounds are legacy options; use Env.run participants/rounds")
        from ..sdk.api import load as load_contract
        resource = files("fg_env.engines").joinpath(selected.path)
        return load_contract(Path(str(resource)), inputs=inputs, seed=seed)
    source = selected.source()
    if inputs:
        raise ValueError("legacy compatibility presets do not accept SDK inputs; clone and edit their template")
    for module_source in selected.module_sources:
        _load_module(module_source)
    from ..legacy import Kernel
    return Kernel(seed=seed).load(source, decision_fn=decision_fn, max_rounds=max_rounds)
