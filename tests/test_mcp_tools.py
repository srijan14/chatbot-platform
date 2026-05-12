"""End-to-end MCP server tests: spin up the FastAPI telecom service + MCP server,
then talk to the MCP server via the official client.

Skipped when the integration toggle isn't set, since these tests start subprocesses
and bind ports."""
import asyncio
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 to run MCP integration tests",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code < 500:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"timeout waiting for {url}")


@contextmanager
def _stack():
    api_port = _free_port()
    mcp_port = _free_port()
    env = os.environ.copy()
    env["TELECOM_API_URL"] = f"http://127.0.0.1:{api_port}"
    env["MCP_PORT"] = str(mcp_port)

    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "telecom_api.app:app",
         "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
        env=env,
    )
    mcp = subprocess.Popen(
        [sys.executable, "-m", "mcp_telecom.server"],
        env=env,
    )
    try:
        _wait_http(f"http://127.0.0.1:{api_port}/health")
        # MCP server has no GET endpoint; just give it a moment
        time.sleep(1.5)
        yield mcp_port
    finally:
        for p in (mcp, api):
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()


async def _list_and_call(url: str):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert "get_customer_profile" in names
            assert len(names) == 14
            result = await session.call_tool(
                "get_customer_profile", {"customer_id": "CUST002"}
            )
            assert not result.isError
            text = result.content[0].text
            assert "Priya" in text


def test_mcp_server_lists_and_calls():
    with _stack() as mcp_port:
        asyncio.run(_list_and_call(f"http://127.0.0.1:{mcp_port}/mcp"))
