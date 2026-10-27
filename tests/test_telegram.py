from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from app.auth import verify_telegram_init_data


def signed_init_data(bot_token: str, user_id: int, auth_date: int) -> str:
    fields = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id}, separators=(",", ":"))}
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TelegramAuthTests(unittest.TestCase):
    def test_signed_recent_admin_is_accepted(self):
        token = "12345:telegram-bot-secret"
        init_data = signed_init_data(token, 123456, int(time.time()))
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ADMIN_IDS": "123456,987654"}):
            self.assertEqual(verify_telegram_init_data(init_data), "123456")

    def test_non_admin_expired_and_tampered_data_are_rejected(self):
        token = "12345:telegram-bot-secret"
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ADMIN_IDS": "123456"}):
            with self.assertRaises(ValueError):
                verify_telegram_init_data(signed_init_data(token, 999, int(time.time())))
            with self.assertRaises(ValueError):
                verify_telegram_init_data(signed_init_data(token, 123456, int(time.time()) - 600))
            tampered = signed_init_data(token, 123456, int(time.time())).replace("123456", "987654")
            with self.assertRaises(ValueError):
                verify_telegram_init_data(tampered)


if __name__ == "__main__":
    unittest.main()
