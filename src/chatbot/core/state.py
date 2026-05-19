"""Graph state for the LangGraph orchestrator.

Mirrors LangGraph's prebuilt `AgentState` (messages + remaining_steps —
the latter is required by `create_react_agent` even though it's marked
NotRequired in the type hint) and extends it with per-session metadata
the prompt callable reads.

The checkpointer persists this whole dict per session, so values written
once at turn start are available on graph resume after an interrupt.
"""
from __future__ import annotations

from typing import Annotated, NotRequired, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    remaining_steps: NotRequired[RemainingSteps]
    bot_id: NotRequired[str]
    customer_id: NotRequired[str | None]
