"""RagSkill — verify prep + dispatch + default injection without booting MCP."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.chatbot.engines.tool_engine.mcp_client import MCPToolDef
from src.chatbot.skills.rag_skill import RagSkill


def _fake_mcp_tools() -> list[MCPToolDef]:
    return [
        MCPToolDef(
            name="search_knowledge_base",
            description="Search KB.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "collection": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
            },
        ),
        MCPToolDef(
            name="list_collections",
            description="List collections.",
            input_schema={"type": "object", "properties": {}},
        ),
        MCPToolDef(
            name="irrelevant_tool",
            description="Should be filtered by allowlist.",
            input_schema={"type": "object", "properties": {}},
        ),
    ]


@pytest.mark.asyncio
async def test_prepare_tools_filters_to_allowlist():
    mcp = AsyncMock()
    mcp.list_tools.return_value = _fake_mcp_tools()

    skill = RagSkill(mcp, default_collection="kb", top_k=5)
    tools = await skill.prepare_tools()

    names = {t["function"]["name"] for t in tools}
    assert names == {"search_knowledge_base", "list_collections"}
    assert skill.owns_tool("search_knowledge_base")
    assert not skill.owns_tool("irrelevant_tool")


@pytest.mark.asyncio
async def test_execute_tool_injects_defaults_for_search():
    mcp = AsyncMock()
    mcp.list_tools.return_value = _fake_mcp_tools()
    mcp.call_tool.return_value = ('{"results": []}', False)

    skill = RagSkill(mcp, default_collection="telecom_policies", top_k=7)
    await skill.prepare_tools()

    await skill.execute_tool("search_knowledge_base", {"query": "cancel"})
    called_args = mcp.call_tool.call_args
    assert called_args[0][0] == "search_knowledge_base"
    args = called_args[0][1]
    assert args["query"] == "cancel"
    assert args["collection"] == "telecom_policies"
    assert args["top_k"] == 7


@pytest.mark.asyncio
async def test_execute_tool_does_not_overwrite_caller_collection():
    mcp = AsyncMock()
    mcp.list_tools.return_value = _fake_mcp_tools()
    mcp.call_tool.return_value = ("ok", False)
    skill = RagSkill(mcp, default_collection="default", top_k=5)
    await skill.prepare_tools()

    await skill.execute_tool(
        "search_knowledge_base", {"query": "x", "collection": "explicit"}
    )
    args = mcp.call_tool.call_args[0][1]
    assert args["collection"] == "explicit"


def test_system_prompt_addition_uses_override_when_provided():
    s_default = RagSkill(AsyncMock(), default_collection="k", search_instructions=None)
    assert "search_knowledge_base" in s_default.system_prompt_addition()

    s_custom = RagSkill(AsyncMock(), default_collection="k", search_instructions="custom hint")
    assert s_custom.system_prompt_addition() == "custom hint"
