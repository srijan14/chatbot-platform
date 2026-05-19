"""Graph state for the LangGraph orchestrator.

We extend the default agent state (messages list with the `add_messages`
reducer) with per-session metadata (`bot_id`, `customer_id`) the prompt
callable needs to assemble the system prompt dynamically.

The checkpointer persists this whole dict per session, so values written
once at turn start are available on graph resume after an interrupt.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    bot_id: str
    customer_id: str | None
