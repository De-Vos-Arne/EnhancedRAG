#!/usr/bin/env python3
"""Start the MCP server (stdio). Point Claude Desktop at this file.

    pip install "mcp[cli]"
    python scripts/mcp_server.py
"""
import _bootstrap  # noqa: F401
from enhanced_rag.mcp_server import main

main()
