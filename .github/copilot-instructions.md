# Codespace Neon development notes

The project exposes a Python MCP server using the official MCP Python SDK (`mcp.server.fastmcp.FastMCP`). See the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and [MCP server concepts](https://modelcontextprotocol.io/docs/develop/build-server).

Run the local server with `.venv/bin/python -m codespace.bridge mcp`. VS Code discovers that stdio server from `.vscode/mcp.json`. The separate Hugging Face-to-Codespace WebSocket bridge is started with `python -m codespace.bridge connect --url wss://<space>.hf.space/ws/bridge`.

Keep Codespace operations inside `codespace/tools.py`. Validate workspace paths, never invoke a shell for terminal commands, and require explicit user confirmation before applying agent-generated changes. Never commit secrets, model credentials, or `.env` files.
