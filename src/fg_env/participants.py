"""Participants: whoever takes the turns. Anything callable with a :class:`~fg_env.Wake` works.

``"random"``, ``"idle"`` and ``"policy:<name>"`` name built-in participants; :func:`anthropic` and :func:`openai`
drive a turn with your own LLM client; :func:`replay` plays a recorded run back.
"""
from .sdk import participants as _participants
from .sdk.participants import *  # noqa: F403 - the participant constructors, whole

__all__ = list(_participants.__all__)
