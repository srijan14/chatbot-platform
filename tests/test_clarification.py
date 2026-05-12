"""Clarification short-circuit: ask_clarification tool surfaces structured fields."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.chatbot.core.bot_config_store import BotConfig
from src.chatbot.core.conversation_manager import Session
from src.chatbot.core.llm_orchestrator import LLMOrchestrator
from src.chatbot.skills.clarification_skill import TOOL_NAME, ClarificationSkill


def _bot_config() -> BotConfig:
    return BotConfig(
        bot_id="telecom_support",
        name="test",
        description="",
        llm_provider="azure_openai",
        llm_deployment="gpt-4o-test",
        llm_reasoning=False,
        max_tokens=128,
        temperature=0.2,
        max_tool_iterations=3,
        system_prompt="you are a test bot",
        enabled_skills=["tool_call"],
        mcp_servers=[],
        tool_allowlist=[],
        max_input_chars=2000,
        pii_redaction_in_logs=True,
    )


def _make_response(*, tool_call=None, content=None, finish_reason="stop"):
    """Build a duck-typed OpenAI ChatCompletion response."""
    tool_calls = None
    if tool_call is not None:
        tool_calls = [SimpleNamespace(
            id=tool_call["id"],
            function=SimpleNamespace(
                name=tool_call["name"],
                arguments=tool_call["arguments"],
            ),
        )]
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeOpenAI:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            return self._responses.pop(0)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


@pytest.mark.asyncio
async def test_ask_clarification_short_circuits_loop():
    client = _FakeOpenAI([
        _make_response(
            tool_call={
                "id": "call_clar_1",
                "name": TOOL_NAME,
                "arguments": json.dumps({
                    "question": "Which plan would you like to switch to?",
                    "expected": "plan_id",
                    "suggested_replies": ["LITE_299", "PRO_599", "MAX_999"],
                }),
            },
            finish_reason="tool_calls",
        ),
    ])
    orch = LLMOrchestrator(client)
    sess = Session(session_id="s1", customer_id="CUST001", history=[])
    skills = [ClarificationSkill()]

    result = await orch.run_turn(sess, "change my plan", _bot_config(), skills)

    assert result.awaiting_clarification is True
    assert result.text == "Which plan would you like to switch to?"
    assert result.clarification is not None
    assert result.clarification.expected == "plan_id"
    assert result.clarification.suggested_replies == ["LITE_299", "PRO_599", "MAX_999"]
    # The loop must have stopped after one LLM call.
    assert len(client.calls) == 1
    # Every tool_call_id must have a matching tool message in history.
    last_tool = sess.history[-1]
    assert last_tool["role"] == "tool"
    assert last_tool["tool_call_id"] == "call_clar_1"
    assert "awaiting" in last_tool["content"].lower()


@pytest.mark.asyncio
async def test_followup_turn_clears_clarification_flag():
    client = _FakeOpenAI([
        _make_response(content="Switched you to PRO_599.", finish_reason="stop"),
    ])
    orch = LLMOrchestrator(client)
    sess = Session(
        session_id="s2",
        customer_id="CUST001",
        history=[
            {"role": "user", "content": "change my plan"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_x", "type": "function",
                     "function": {"name": TOOL_NAME, "arguments": "{\"question\":\"which plan?\"}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_x", "content": "(awaiting user response)"},
        ],
    )
    result = await orch.run_turn(sess, "PRO_599", _bot_config(), [ClarificationSkill()])

    assert result.awaiting_clarification is False
    assert "PRO_599" in result.text


@pytest.mark.asyncio
async def test_clarification_skill_exposes_tool_schema():
    skill = ClarificationSkill()
    tools = await skill.prepare_tools()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == TOOL_NAME
    params = tools[0]["function"]["parameters"]
    assert "question" in params["required"]
    assert skill.owns_tool(TOOL_NAME)
    assert not skill.owns_tool("get_customer_profile")
