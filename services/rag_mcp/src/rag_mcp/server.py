"""RAG MCP server — exposes search_knowledge_base and list_collections as MCP
tools over Streamable HTTP on :8766.

Verify:
    npx @modelcontextprotocol/inspector http://localhost:8766/mcp
"""
import os

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP  # noqa: E402

from rag_mcp.tools import register  # noqa: E402

HOST = os.getenv("RAG_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("RAG_MCP_PORT", "8766"))

mcp = FastMCP("rag", host=HOST, port=PORT)
register(mcp)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
