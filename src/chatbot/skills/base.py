"""Base Skill class + the data contracts every skill returns.

A `Skill` produces (a) the OpenAI-shape tool schemas it wants exposed to the
model, and (b) a `ToolResult` when one of those tools is dispatched. The
result carries the text that goes back into the LLM history (so the model can
keep reasoning) and, optionally, a `TurnSignal` that escapes the tool loop
and surfaces on the chat response — used by clarification, handoff,
end-of-conversation, confirmation, etc. The orchestrator treats every tool
uniformly; the skill alone decides whether a tool emits a signal and/or
halts the loop.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnSignal:
    """A structured event the skill wants surfaced to the caller / UI.

    The platform is intentionally type-agnostic: any string is accepted.
    Conventions to keep callers consistent:
        - "clarification": payload = {question, expected, suggested_replies}
        - "confirmation_required": payload = {summary, action, options}
        - "handoff": payload = {reason, queue}
        - "end_conversation": payload = {reason}
    Add a new type by inventing one — no core change required.
    """
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """What a Skill returns when one of its tools is invoked.

    Fields:
      text                 The string we put back into the LLM history as the
                           role:"tool" content. The model "sees" this on the
                           next iteration. Use a placeholder like
                           "(awaiting user response)" when no LLM-facing
                           result is meaningful.
      is_error             True marks the tool as failed; the model may
                           apologise / retry.
      user_visible_text    If set, overrides the assistant's content for the
                           chat response. Used by terminal skills (e.g.
                           clarification) where the assistant message had
                           content=None and the user-visible text lives in
                           the tool's arguments.
      signal               If set, bubbles up to TurnResult.signals and onward
                           to the chat response.
      terminal             If True, the orchestrator stops the iteration loop
                           after this tool round. Use for skills that pause
                           the conversation waiting for the user (clarify /
                           confirm / handoff).
      sources              Structured source references the tool grounded its
                           result in (e.g. RAG documents: {document_id, title,
                           url}). Collected across the turn and surfaced on the
                           chat response so callers can show clickable citations.
    """
    text: str
    is_error: bool = False
    user_visible_text: str | None = None
    signal: TurnSignal | None = None
    terminal: bool = False
    sources: list[dict[str, Any]] | None = None


class Skill(ABC):
    name: str

    @abstractmethod
    async def prepare_tools(self) -> list[dict]:
        """Return the OpenAI-shaped tool definitions this skill contributes."""

    @abstractmethod
    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        """Execute a tool call. Returns a structured ToolResult."""

    @abstractmethod
    def owns_tool(self, name: str) -> bool:
        """Whether this skill can handle the named tool."""

    def system_prompt_addition(self) -> str | None:
        """Optional: text the skill contributes to the system prompt at runtime.

        The orchestrator concatenates non-None additions from every enabled
        skill into the system prompt before each LLM call. This is the
        platform's way to let a skill teach the model how to invoke it,
        without each bot's YAML having to repeat the same boilerplate.
        """
        return None
