"""The template API: the earlier template-based kernel, kept for existing templates.

New environments are contracts (:mod:`fg_env`). Existing templates keep working from here:

    from fg_env.legacy import simulate, Kernel

    world = simulate(template)     # dict, WorldTemplate, or path to JSON
    print(world.summary())

    kernel = Kernel(seed=42)
    world = kernel.load(template_dict, decision_fn=my_agent)
    world.run()

Extension surface for template authors:

    from fg_env.legacy import (
        registry,
        effect, precondition, resolution, phase, termination_decorator, module,
        EffectContext,
    )

The decorators register custom verbs, archetypes and phases; the engine looks everything up by
name through the registry. The command line is ``fg-env legacy <command>``.
"""
import logging as _logging
import os as _os

from ..registry import (
    KernelRegistry,
    registry,
    effect,
    precondition,
    resolution,
    phase,
    termination as termination_decorator,  # avoid shadowing the termination submodule
    module,
    target_selector,
)
# The termination submodule under its natural name: evaluate / check_all / resolve_winner /
# register_winner_resolver.
from .. import termination
from ..effect_context import EffectContext

# Primitive discovery (kernel_primitives/*.py drop-in directories) is opt-in: call ``discover()``
# explicitly, or set ``KERNEL_PRIMITIVES_DIR`` — an explicitly configured directory is a clear request
# for discovery, so it is honoured when the template API is imported.
from ..primitives_loader import discover, list_loaded_primitives

if _os.environ.get("KERNEL_PRIMITIVES_DIR"):
    try:
        discover()
    except Exception:
        _logging.getLogger(__name__).exception("kernel_primitives discovery failed")

# Pipeline — template loader, compile, lint, smoke, packages, contract
from ..pipeline import (
    WorldTemplate,
    build_world_state,
    load_world,
    load_world_parts,
    CompileIssue,
    CompileResult,
    compile_template,
    lint_template,
    SmokeReport,
    smoke_test,
    ReplayStep,
    ReplayTrace,
    replay,
    EnvPackage,
    PACKAGE_EXTENSION,
    load_env_package,
    save_env_package,
    scaffold_env,
    pack_env,
    unpack_env,
    validate_package,
    CONTRACT_VERSION,
    export_kernel_contract,
)
from ..predicates import evaluate as evaluate_predicate
from ..predicates import resolve as resolve_expression_value
from ..kernel_module import (
    KernelModule,
    dispatch_despawn,
    dispatch_spawn,
    dispatch_round_start,
    collect_snapshots,
)
from ..observability import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    metrics,
    timed,
    enable_engine_metrics,
    engine_metrics_enabled,
    get_logger,
)
from ..temporal import TemporalModel, TimeMode, Phase
from ..continuous_time import ContinuousTemporalModel, EventQueue, ScheduledEvent
from ..action import ActionInstance
from ..event import SimEvent
from ..state import WorldState
from ..runtime.engine import SimulationEngine
from ..kernel import DecisionFn, Kernel, OnEventFn, TemplateError, World, simulate
from ..policies import random_policy
from ..physics import (
    PhysicsModel,
    PhysicsVariable,
    EntitySource,
    EntityWriteback,
    PhysicsExprError,
)

__all__ = [
    # Pipeline
    "WorldTemplate",
    "build_world_state",
    "load_world",
    "load_world_parts",
    "compile_template",
    "CompileIssue",
    "CompileResult",
    "lint_template",
    "smoke_test",
    "SmokeReport",
    "replay",
    "ReplayStep",
    "ReplayTrace",
    "EnvPackage",
    "PACKAGE_EXTENSION",
    "load_env_package",
    "save_env_package",
    "scaffold_env",
    "pack_env",
    "unpack_env",
    "validate_package",
    "export_kernel_contract",
    "CONTRACT_VERSION",
    "termination",
    "termination_decorator",
    "evaluate_predicate",
    "resolve_expression_value",
    "KernelModule",
    "dispatch_despawn",
    "dispatch_spawn",
    "dispatch_round_start",
    "collect_snapshots",
    # Observability
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "metrics",
    "timed",
    "enable_engine_metrics",
    "engine_metrics_enabled",
    "get_logger",
    # Primitives
    "discover",
    "list_loaded_primitives",
    "KernelRegistry",
    "registry",
    "effect",
    "precondition",
    "resolution",
    "phase",
    "module",
    "target_selector",
    "EffectContext",
    # Facade and agent contract
    "simulate",
    "random_policy",
    "Kernel",
    "TemplateError",
    "World",
    "DecisionFn",
    "OnEventFn",
    "ActionInstance",
    "WorldState",
    "SimulationEngine",
    "SimEvent",
    # Temporal modes
    "TemporalModel",
    "TimeMode",
    "Phase",
    "ContinuousTemporalModel",
    "EventQueue",
    "ScheduledEvent",
    # Continuous coupled dynamics ("physics")
    "PhysicsModel",
    "PhysicsVariable",
    "EntitySource",
    "EntityWriteback",
    "PhysicsExprError",
]
