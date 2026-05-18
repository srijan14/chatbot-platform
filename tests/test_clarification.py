"""Clarification short-circuit: ask_clarification tool surfaces structured fields."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.chatbot.core.bot_config_store import BotConfig, ClarificationConfig
from src.chatbot.core.conversation_manager import Session
from src.chatbot.core.llm_orchestrator import LLMOrchestrator
from src.chatbot.skills.clarification_skill import (
    DEFAULT_EXPECTED_VALUES,
    TOOL_NAME,
    ClarificationSkill,
)


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
        enabled_skills=["tool_call", "clarification"],
        mcp_servers=[],
        tool_allowlist=[],
        clarification_expected_values=None,
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
    # No domain-specific enum should be present by default — the skill stays
    # generic until a bot config injects expected_types.
    assert "enum" not in params["properties"]["expected"]
    assert skill.owns_tool(TOOL_NAME)
    assert not skill.owns_tool("get_customer_profile")


@pytest.mark.asyncio
async def test_clarification_schema_is_driven_by_config():
    # A telecom-flavoured bot pulls its own vocabulary in via config.
    telecom = ClarificationSkill(
        expected_types=["plan_id", "bill_id", "yes_no"],
        description="Telecom override description.",
        max_suggested_replies=3,
    )
    tools = await telecom.prepare_tools()
    fn = tools[0]["function"]
    assert fn["description"] == "Telecom override description."
    expected = fn["parameters"]["properties"]["expected"]
    assert expected["enum"] == ["plan_id", "bill_id", "yes_no"]
    assert fn["parameters"]["properties"]["suggested_replies"]["maxItems"] == 3

    # A different bot picks a completely different vocabulary — proving the
    # skill no longer carries telecom-specific knowledge.
    bi = ClarificationSkill(expected_types=["metric", "dimension", "date_range"])
    bi_tools = await bi.prepare_tools()
    bi_expected = bi_tools[0]["function"]["parameters"]["properties"]["expected"]
    assert bi_expected["enum"] == ["metric", "dimension", "date_range"]


def test_clarification_config_parsed_from_yaml(tmp_path):
    """BotConfig.from_yaml should hydrate `clarification` from the YAML block."""
    yaml_path = tmp_path / "demo_bot.yaml"
    yaml_path.write_text(
        """
bot_id: demo_bot
name: Demo
description: ""
llm:
  provider: azure_openai
  deployment: gpt-4o-test
  max_tokens: 128
  temperature: 0.1
  max_tool_iterations: 2
persona:
  system_prompt: hi
skills:
  enabled: []
clarification:
  expected_types: [metric, dimension]
  max_suggested_replies: 2
  description: Ask only when the slice is ambiguous.
""".strip()
    )
    cfg = BotConfig.from_yaml(yaml_path)
    assert cfg.clarification.expected_types == ["metric", "dimension"]
    assert cfg.clarification.max_suggested_replies == 2
    assert cfg.clarification.description.startswith("Ask only")


def test_clarification_config_defaults_when_block_omitted(tmp_path):
    yaml_path = tmp_path / "bare_bot.yaml"
    yaml_path.write_text(
        """
bot_id: bare_bot
name: Bare
description: ""
llm:
  provider: azure_openai
  deployment: gpt-4o-test
  max_tokens: 128
  temperature: 0.1
  max_tool_iterations: 2
persona:
  system_prompt: hi
skills:
  enabled: []
""".strip()
    )
    cfg = BotConfig.from_yaml(yaml_path)
    assert cfg.clarification == ClarificationConfig()
    assert cfg.clarification.expected_types == []
