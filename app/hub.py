from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import WebSocket


class BridgeHub:
    def __init__(self) -> None:
        self.bridge: WebSocket | None = None
        self.browsers: set[WebSocket] = set()
        self.pending: dict[str, asyncio.Future] = {}
        self.lock = asyncio.Lock()
        self.last_seen: float | None = None

    async def status(self) -> dict[str, Any]:
        return {"connected": self.bridge is not None, "last_seen": self.last_seen}

    async def broadcast_status(self) -> None:
        message = json.dumps({"type": "bridge_status", **await self.status()})
        for browser in tuple(self.browsers):
            try:
                await browser.send_text(message)
            except Exception:
                self.browsers.discard(browser)

    async def attach(self, socket: WebSocket) -> None:
        async with self.lock:
            previous, self.bridge = self.bridge, socket
        if previous is not None and previous is not socket:
            await previous.close(code=1012)
        await self.broadcast_status()
        try:
            while True:
                message = await socket.receive_json()
                if message.get("type") != "rpc_result":
                    continue
                self.last_seen = asyncio.get_running_loop().time()
                future = self.pending.pop(str(message.get("id")), None)
                if future is not None and not future.done():
                    if message.get("ok"):
                        future.set_result(message.get("result"))
                    else:
                        future.set_exception(RuntimeError(str(message.get("error", "Error del Codespace"))[:1000]))
        except Exception:
            pass
        finally:
            async with self.lock:
                if self.bridge is socket:
                    self.bridge = None
                    for future in tuple(self.pending.values()):
                        if not future.done():
                            future.set_exception(ConnectionError("El Codespace se desconectó"))
                    self.pending.clear()
            await self.broadcast_status()

    async def invoke(self, tool: str, args: dict[str, Any] | None = None, timeout: int = 180) -> Any:
        async with self.lock:
            socket = self.bridge
            if socket is None:
                raise ConnectionError("Conecta primero el Codespace desde la pestaña MCP")
            request_id = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = future
        try:
            await socket.send_json({"type": "rpc", "id": request_id, "tool": tool, "args": args or {}})
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.pending.pop(request_id, None)