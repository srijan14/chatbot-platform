"""Clarification short-circuit: ask_clarification surfaces structured fields
and (via the skill→tool adapter) trips a LangGraph interrupt."""
from __future__ import annotations

import pytest

from src.chatbot.adapters.skill_to_tool import skill_to_langchain_tools
from src.chatbot.core.bot_config_store import BotConfig, ClarificationConfig
from src.chatbot.skills.base import ToolResult
from src.chatbot.skills.clarification_skill import (
    DEFAULT_EXPECTED_VALUES,
    TOOL_NAME,
    ClarificationSkill,
)


# --- Skill-level (no orchestrator) ------------------------------------------

@pytest.mark.asyncio
async def test_clarification_skill_returns_terminal_tool_result():
    """The skill itself emits a terminal ToolResult with a clarification signal.
    The skill→tool adapter is the layer that translates this into an interrupt()."""
    skill = ClarificationSkill(expected_types=["plan_id", "yes_no"])
    result: ToolResult = await skill.execute_tool(
        TOOL_NAME,
        {
            "question": "Which plan?",
            "expected": "plan_id",
            "suggested_replies": ["LITE_299", "PRO_599"],
        },
    )
    assert result.terminal is True
    assert result.signal is not None and result.signal.type == "clarification"
    assert result.signal.payload["question"] == "Which plan?"
    assert result.signal.payload["expected"] == "plan_id"
    assert result.signal.payload["suggested_replies"] == ["LITE_299", "PRO_599"]
    # Placeholder text for the LLM history (no useful content; the question is
    # delivered to the user via the signal/interrupt).
    assert "awaiting" in result.text.lower()


@pytest.mark.asyncio
async def test_clarification_skill_exposes_tool_schema():
    skill = ClarificationSkill()
    tools = await skill.prepare_tools()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == TOOL_NAME
    params = tools[0]["function"]["parameters"]
    assert "question" in params["required"]
    assert "enum" not in params["properties"]["expected"]
    assert skill.owns_tool(TOOL_NAME)
    assert not skill.owns_tool("get_customer_profile")


@pytest.mark.asyncio
async def test_clarification_schema_is_driven_by_config():
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

    bi = ClarificationSkill(expected_types=["metric", "dimension", "date_range"])
    bi_tools = await bi.prepare_tools()
    bi_expected = bi_tools[0]["function"]["parameters"]["properties"]["expected"]
    assert bi_expected["enum"] == ["metric", "dimension", "date_range"]


# --- Adapter-level (interrupt translation) ----------------------------------

@pytest.mark.asyncio
async def test_adapter_converts_clarification_skill_to_structured_tool():
    """The skill→tool adapter must yield exactly one LangChain StructuredTool
    matching the skill's OpenAI schema."""
    skill = ClarificationSkill(expected_types=["plan_id"])
    tools = await skill_to_langchain_tools(skill)
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == TOOL_NAME
    # The args_schema is built dynamically; check fields are present.
    fields = tool.args_schema.model_fields
    assert "question" in fields
    assert "expected" in fields
    assert "suggested_replies" in fields


# --- YAML config tests (unchanged) ------------------------------------------

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
