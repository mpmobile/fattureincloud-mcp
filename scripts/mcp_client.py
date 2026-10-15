#!/usr/bin/env python3
"""Minimal MCP test client: spawns server.py over stdio, exactly like Claude does.

Each run starts a fresh server process, so code changes are picked up immediately.
Credentials are read from .env (or from the current environment).

Usage:
    .venv/bin/python scripts/mcp_client.py                      # list tools
    .venv/bin/python scripts/mcp_client.py --schema list_invoices
    .venv/bin/python scripts/mcp_client.py list_invoices '{"year": 2025, "month": 3}'
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def run(tool, args, schema):
    load_dotenv(ROOT / ".env")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server.py")],
        env=dict(os.environ),
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools

            if schema:
                match = next((t for t in tools if t.name == schema), None)
                if match is None:
                    sys.exit(f"Tool '{schema}' not found")
                print(match.description)
                print(json.dumps(match.inputSchema, indent=2, ensure_ascii=False))
                return

            if tool is None:
                for t in tools:
                    print(f"{t.name:30} {t.description[:90]}")
                print(f"\n{len(tools)} tools")
                return

            result = await session.call_tool(tool, args)
            for block in result.content:
                print(getattr(block, "text", block))
            if result.isError:
                sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tool", nargs="?", help="tool name to call")
    parser.add_argument("args", nargs="?", default="{}", help="tool arguments as JSON")
    parser.add_argument("--schema", metavar="TOOL", help="print description and inputSchema of a tool")
    ns = parser.parse_args()
    asyncio.run(run(ns.tool, json.loads(ns.args), ns.schema))


if __name__ == "__main__":
    main()
