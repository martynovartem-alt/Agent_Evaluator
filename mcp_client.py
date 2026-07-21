"""
Live MCP integration for MCPClear (used when config.MCP_MODE == "live").

Connects to a real MCP server per config (stdio or streamable-http), calls the transactions
tool, and returns operations in the shape MCPClear expects (see json_answer_history_operations.md).
Requires `pip install mcp`. With MCP_MODE=fixture (default) this module is never imported.

Adapt `_tool_args` to your server's tool schema if its parameter names differ.
"""
import asyncio
import concurrent.futures
import json
import shlex

import config


def _tool_args(user_id: str, from_date: str, to_date: str, operation_amount: int | None) -> dict:
    args = {"userId": user_id, "fromDate": from_date, "toDate": to_date}
    if operation_amount is not None:
        args["operationAmount"] = operation_amount
    return args


def _parse_operations(result) -> list[dict]:
    """MCP tool result → list of operation dicts (expects JSON text with an `operations` array)."""
    text = "".join(getattr(b, "text", "") for b in result.content)
    data = json.loads(text)
    return data["operations"] if isinstance(data, dict) else data


async def _fetch_async(user_id, from_date, to_date, operation_amount) -> list[dict]:
    from mcp import ClientSession

    args = _tool_args(user_id, from_date, to_date, operation_amount)
    if config.MCP_TRANSPORT == "http":
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {config.MCP_AUTH_TOKEN}"} if config.MCP_AUTH_TOKEN else None
        async with streamablehttp_client(config.MCP_SERVER_URL, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(config.MCP_TOOL_NAME, args)
    else:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        cmd = shlex.split(config.MCP_COMMAND)
        params = StdioServerParameters(command=cmd[0], args=cmd[1:])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(config.MCP_TOOL_NAME, args)
    return _parse_operations(result)


def _run_sync(coro):
    """Run an async coroutine from sync code, even when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def fetch_operations(user_id: str, from_date: str, to_date: str, operation_amount: int | None = None) -> list[dict]:
    """Fetch operations from the live MCP server. Validates config, then calls the tool."""
    if config.MCP_TRANSPORT == "http":
        if not config.MCP_SERVER_URL:
            raise RuntimeError("MCP_MODE=live with http transport requires MCP_SERVER_URL")
    elif not config.MCP_COMMAND:
        raise RuntimeError("MCP_MODE=live with stdio transport requires MCP_COMMAND")
    try:
        return _run_sync(_fetch_async(user_id, from_date, to_date, operation_amount))
    except ImportError as e:
        raise RuntimeError("MCP_MODE=live requires the MCP SDK — `pip install mcp`") from e
