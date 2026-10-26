"""Print the MCP tool listing of a datagouv-mcp source tree as stable JSON.

Usage: python dump_tools.py <path-to-source-tree>

Used by sync-upstream.sh to check that the fork's default (data.gouv.fr)
tool listing is byte-identical to upstream's.
"""

import asyncio
import json
import os
import sys

tree = os.path.abspath(sys.argv[1])
os.chdir(tree)
sys.path.insert(0, tree)

from mcp.server.fastmcp import FastMCP

from tools import register_tools

app = FastMCP()
register_tools(app)
tools = asyncio.run(app.list_tools())
print(
    json.dumps(
        [tool.model_dump(mode="json") for tool in tools],
        indent=1,
        ensure_ascii=False,
        sort_keys=True,
    )
)
