from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from main import app


class ApiAuthTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "MCP_SECRET": "test-secret-" + "x" * 32,
            "APP_PASSWORD": "test-password-at-least-16",
            "COOKIE_SECURE": "false",
        })
        self.environment.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.environment.stop()

    def test_health_is_public_and_workspace_api_requires_login(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/models").status_code, 401)
        self.assertEqual(self.client.get("/api/mcp").status_code, 401)

    def test_login_cookie_unlocks_mcp_catalog_and_frontend(self):
        invalid = self.client.post("/api/login", json={"password": "wrong"})
        self.assertEqual(invalid.status_code, 401)
        login = self.client.post("/api/login", json={"password": "test-password-at-least-16"})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(self.client.get("/api/session").json()["authenticated"])
        catalog = self.client.get("/api/mcp")
        self.assertEqual(catalog.status_code, 200)
        self.assertIn("generate_image", catalog.json()["tools"])
        self.assertIn("tts_generate", catalog.json()["tools"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_tool_allowlist_rejects_unknown_methods(self):
        self.client.post("/api/login", json={"password": "test-password-at-least-16"})
        response = self.client.post("/api/mcp", json={"tool": "run_arbitrary_shell", "args": {}})
        self.assertEqual(response.status_code, 400)

    def test_chat_accepts_single_message_and_dolphin_alias(self):
        self.client.post("/api/login", json={"password": "test-password-at-least-16"})
        with patch.object(main.hub, "invoke", new_callable=AsyncMock) as invoke:
            invoke.return_value = {"content": "Hola", "model": "dolphin-llama3"}
            response = self.client.post("/chat", json={"model": "dolphin", "message": "Hola"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"content": "Hola", "model": "dolphin-llama3"})
        invoke.assert_awaited_once_with("ollama_chat", {"model": "dolphin-llama3", "messages": [{"role": "user", "content": "Hola"}]}, timeout=180)


if __name__ == "__main__":
    unittest.main()
