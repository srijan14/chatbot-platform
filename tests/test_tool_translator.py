"""Unit tests for the MCP→OpenAI schema bridge.

The bridge is the most likely source of subtle bugs since both sides use JSON
Schema but with different envelope keys. MCP gives us flat
`{name, description, inputSchema}`; OpenAI wants
`{type:"function", function:{name, description, parameters}}`.
"""
from src.chatbot.engines.tool_engine.mcp_client import MCPToolDef
from src.chatbot.engines.tool_engine.tool_translator import mcp_to_openai


def _t(name: str) -> MCPToolDef:
    return MCPToolDef(
        name=name,
        description=f"do {name}",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )


def test_translates_basic_shape():
    out = mcp_to_openai([_t("a"), _t("b")])
    assert len(out) == 2
    assert out[0] == {
        "type": "function",
        "function": {
            "name": "a",
            "description": "do a",
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
        },
    }


def test_allowlist_filters():
    out = mcp_to_openai([_t("a"), _t("b"), _t("c")], allowlist=["a", "c"])
    assert [t["function"]["name"] for t in out] == ["a", "c"]


def test_empty_allowlist_means_keep_all():
    out = mcp_to_openai([_t("a")], allowlist=None)
    assert [t["function"]["name"] for t in out] == ["a"]


def test_missing_input_schema_falls_back_to_empty_object():
    tool = MCPToolDef(name="z", description="zz", input_schema=None)  # type: ignore[arg-type]
    out = mcp_to_openai([tool])
    assert out[0]["function"]["parameters"] == {"type": "object", "properties": {}}
