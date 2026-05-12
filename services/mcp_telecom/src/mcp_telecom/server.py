"""Telecom MCP server — exposes 14 telecom tools via Streamable HTTP on :8765.

Run:
    mcp-telecom

Verify:
    npx @modelcontextprotocol/inspector http://localhost:8765/mcp
"""
import os

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP  # noqa: E402

from mcp_telecom.tools import register  # noqa: E402

HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8765"))

mcp = FastMCP("telecom", host=HOST, port=PORT)
register(mcp)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
