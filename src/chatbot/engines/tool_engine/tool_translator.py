"""Translate MCP tool definitions into the OpenAI/Azure OpenAI tool shape.

This is the most likely source of subtle bugs — both sides use JSON Schema but the
envelope differs. MCP uses a flat `{name, description, inputSchema}`; OpenAI nests
the tool metadata inside `{type:"function", function:{name, description, parameters}}`.

Note: with Azure OpenAI / gpt-4o, prompt caching kicks in automatically once the
prompt exceeds ~1024 tokens. We don't need to mark anything explicitly the way
Anthropic requires `cache_control` blocks.
"""
from typing import Iterable

from src.chatbot.engines.tool_engine.mcp_client import MCPToolDef


def mcp_to_openai(
    mcp_tools: Iterable[MCPToolDef],
    allowlist: list[str] | None = None,
) -> list[dict]:
    """Convert MCP tool definitions to the OpenAI `tools=` parameter shape.

    OpenAI spec for a single tool:
        {
            "type": "function",
            "function": {
                "name": str,
                "description": str,
                "parameters": JSONSchema,   // <-- not "input_schema"
            },
        }
    """
    allow = set(allowlist) if allowlist else None
    out: list[dict] = []
    for t in mcp_tools:
        if allow is not None and t.name not in allow:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema or {"type": "object", "properties": {}},
            },
        })
    return out
