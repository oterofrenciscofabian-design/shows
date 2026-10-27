from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.auth import create_session, valid_session, verify_password
from codespace import tools
from codespace.security import UnsafeOperation, validate_command


class CommandPolicyTests(unittest.TestCase):
    def test_allowed_commands_are_tokenized(self):
        self.assertEqual(validate_command("npm install fastapi"), ["npm", "install", "fastapi"])
        self.assertEqual(validate_command("git status"), ["git", "status"])
        self.assertEqual(validate_command("pm2 restart api"), ["pm2", "restart", "api"])

    def test_shell_metacharacters_and_unknown_commands_are_rejected(self):
        for command in ("npm install; whoami", "git status | cat", "python -c import os", "rm -rf /"):
            with self.subTest(command=command), self.assertRaises(UnsafeOperation):
                validate_command(command)


class WorkspacePathTests(unittest.TestCase):
    def test_parent_traversal_and_private_paths_are_rejected(self):
        for path in ("../outside", ".env", ".env.example", ".git/config", "src/.venv/file"):
            with self.subTest(path=path), self.assertRaises(UnsafeOperation):
                tools.resolve_path(path)

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside"
            outside.mkdir()
            try:
                (root / "escape").symlink_to(outside, target_is_directory=True)
                with patch.object(tools, "ROOT", root), self.assertRaises(UnsafeOperation):
                    tools.resolve_path("escape/file.txt")
            finally:
                outside.rmdir()

    def test_binary_output_is_confined_to_assets(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tools, "ROOT", Path(directory)):
            content = base64.b64encode(b"image-bytes").decode()
            result = tools.execute_tool("write_binary", {"path": "assets/logo.png", "content": content})
            self.assertEqual(result["bytes"], 11)
            self.assertEqual((Path(directory) / "assets/logo.png").read_bytes(), b"image-bytes")
            with self.assertRaises(UnsafeOperation):
                tools.execute_tool("write_binary", {"path": "logo.png", "content": content})

    def test_workspace_listing_skips_private_directories(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tools, "ROOT", Path(directory)):
            root = Path(directory)
            (root / ".aws").mkdir()
            (root / ".aws" / "credentials").write_text("private")
            (root / "public.py").write_text("print('ok')")
            self.assertEqual(tools._tracked_files(root), ["public.py"])

    def test_child_process_environment_drops_secrets(self):
        with patch.dict(os.environ, {"PATH": "/bin", "MCP_SECRET": "secret", "HF_TOKEN": "token", "PIP_INDEX_URL": "https://private"}, clear=True):
            env = tools._safe_environment()
            self.assertEqual(env["PATH"], "/bin")
            self.assertNotIn("MCP_SECRET", env)
            self.assertNotIn("HF_TOKEN", env)
            self.assertNotIn("PIP_INDEX_URL", env)


class SessionTests(unittest.TestCase):
    def test_sessions_are_signed_and_expire_in_validation(self):
        with patch.dict(os.environ, {"MCP_SECRET": "a" * 40}):
            token = create_session()
            self.assertTrue(valid_session(token))
            self.assertFalse(valid_session(token + "invalid"))
            self.assertFalse(valid_session(None))

    def test_password_has_minimum_entropy_length_requirement(self):
        with patch.dict(os.environ, {"APP_PASSWORD": "short"}):
            self.assertFalse(verify_password("short"))
        with patch.dict(os.environ, {"APP_PASSWORD": "correct-password-value"}):
            self.assertTrue(verify_password("correct-password-value"))
            self.assertFalse(verify_password("incorrect-password-value"))


if __name__ == "__main__":
    unittest.main()
