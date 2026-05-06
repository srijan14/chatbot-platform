"""MCP client wrapper around the official `mcp` Python SDK (Streamable HTTP).

For POC simplicity, each list_tools/call_tool opens a fresh session. The tool list
is cached after the first fetch. Localhost latency is negligible; for production we'd
maintain a long-lived session in app state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@dataclass
class MCPToolDef:
    name: str
    description: str
    input_schema: dict


class MCPClient:
    def __init__(self, url: str):
        self.url = url
        self._tools_cache: list[MCPToolDef] | None = None

    async def list_tools(self) -> list[MCPToolDef]:
        if self._tools_cache is not None:
            return self._tools_cache
        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                self._tools_cache = [
                    MCPToolDef(
                        name=t.name,
                        description=t.description or "",
                        input_schema=t.inputSchema or {"type": "object", "properties": {}},
                    )
                    for t in response.tools
                ]
        return self._tools_cache

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Call a tool. Returns (text_content, is_error)."""
        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                texts: list[str] = []
                for block in result.content or []:
                    text = getattr(block, "text", None)
                    if text is not None:
                        texts.append(text)
                    else:
                        # fallback for non-text content
                        texts.append(str(getattr(block, "data", block)))
                return "\n".join(texts) if texts else "", bool(result.isError)
