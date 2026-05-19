"""Graph state for the LangChain v1 agent.

Subclasses `langchain.agents.AgentState` (which provides `messages`, `jump_to`,
and `structured_response`) with the per-session metadata our middlewares need:
  • bot_id / customer_id — read by the dynamic-prompt middleware
  • token_*_used — populated by the token-usage middleware after each model call

The checkpointer persists this whole dict per session, so values written once
at turn start are available on graph resume after an interrupt.
"""
from __future__ import annotations

from typing import NotRequired

from langchain.agents import AgentState


class ChatbotAgentState(AgentState):
    bot_id: NotRequired[str]
    customer_id: NotRequired[str | None]
    # Populated by token_usage_middleware after each model call. The chat
    # handler reads these off final state instead of scanning messages.
    prompt_tokens_used: NotRequired[int]
    completion_tokens_used: NotRequired[int]
    cached_tokens_used: NotRequired[int]
