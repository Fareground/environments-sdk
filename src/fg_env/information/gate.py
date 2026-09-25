"""The one gate every template is rendered through: bound to the reader it is rendered for.

Who reads a text decides what it may show, and the one definition of what is hidden from whom (``expr/hidden.py``)
is enforced on whoever the render binds as ``$viewer`` (``expr/values.py``). So a template is rendered only here, and
every caller says whom it renders for:

* an entity — text one agent reads (an outcome, a refusal, its brief): its own private values may show, no other's;
* :data:`~fg_env.expr.EVERYONE` — text sent to more than one agent (an announcement, news, an end's `say`, an
  invariant's `why`): no private value may show;
* ``None`` — the rules' own words, which no agent reads as text, in the true state as game logic reads it (a value
  hidden from the acting agent counts as read by it, see ``runtime/ledger.py``): generated ids and names, a host's
  prompt.

:class:`~fg_env.information.core.Information` renders for a run through :meth:`~.core.Information.render`; the parts
built before it or beneath it — the world's builder, effects, the action book, mechanisms — call :func:`render` with
their world.

Whom a field is rendered or worked out for comes from one table (``contract/readers.py``), the one the privacy check
reads too: :func:`viewer_for` gives the ``$viewer`` a field's reader class binds, so the run and the check cannot
disagree about a field.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..contract.readers import Reader, reader_of
from ..errors import RunError
from ..expr import EVERYONE, ExprError
from ..expr.template import compile_template

if TYPE_CHECKING:
    from ..expr.objects import Entity
    from ..expr.values import _Everyone
    from ..world.store import World

__all__ = ["render", "viewer_for"]


def render(world: World, template: str, vars: Mapping[str, Any], *, viewer: Entity | _Everyone | None,
           subject: str | None = None, path: str | None = None) -> str:
    """``template`` rendered over ``world`` with the roots ``vars``, for ``viewer`` (see the module docstring).
    ``subject`` is the root a bare ``{field}`` reads; with ``path``, an expression error is a :class:`RunError` there.
    """
    scope = world.evaluation.scope(**vars) if viewer is None else world.evaluation.scope(**{**vars, "viewer": viewer})
    if path is None:
        return compile_template(template, subject).render(scope)
    try:
        return compile_template(template, subject).render(scope)
    except ExprError as exc:
        raise RunError(str(exc), path) from None


def viewer_for(field: str, reader: Entity | _Everyone | None = None) -> Entity | _Everyone | None:
    """The ``$viewer`` ``field`` is worked out for (``Model.field`` or ``op.field``; see contract/readers.py): game
    logic's None for the rules' fields, :data:`~fg_env.expr.EVERYONE` for what several are sent or learn from, and
    ``reader`` for the fields whose reader the caller knows — the one agent shown it, or (a message, a stage's passes)
    whoever it reaches as the caller resolves it."""
    kind = reader_of(field)
    if kind is Reader.RULES or kind is Reader.WORDS:
        return None
    if kind is Reader.SEVERAL:
        return EVERYONE
    return reader
