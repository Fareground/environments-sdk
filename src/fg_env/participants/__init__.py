"""Participants: whoever takes the turns. Anything callable with a :class:`~fg_env.Wake` works.

``"random"``, ``"idle"`` and ``"policy:<name>"`` name built-in participants; :func:`anthropic` and :func:`openai`
drive a turn with your own LLM client; :func:`replay` plays a recorded run back.
"""
from .builtin import Idle, Participant, PolicyAgent, RandomAgent, replay, resolve_participant
from .llm import anthropic, openai

__all__ = ["Participant", "RandomAgent", "Idle", "PolicyAgent", "anthropic", "openai", "replay", "resolve_participant"]
