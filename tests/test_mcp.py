from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpServerTests(unittest.TestCase):
    def test_stdio_server_advertises_workspace_and_model_tools(self):
        async def list_tools():
            server = StdioServerParameters(
                command=sys.executable,
                args=["-m", "codespace.bridge", "mcp"],
                env={**os.environ, "PYTHONPATH": str(Path.cwd())},
            )
            async with stdio_client(server) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return {tool.name for tool in (await session.list_tools()).tools}

        names = asyncio.run(list_tools())
        self.assertTrue({"read_file", "run_terminal", "git_status", "ollama_chat", "tts_generate", "generate_image", "generate_video"} <= names)


if __name__ == "__main__":
    unittest.main()
