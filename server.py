#!/usr/bin/env python3
"""Fatture in Cloud MCP Server - entry point.

Kept so existing configurations keep working (Claude Desktop `python server.py`,
the MCPB manifest). The server lives in the `fattureincloud_mcp` package.
"""

from fattureincloud_mcp.app import main

if __name__ == "__main__":
    main()
