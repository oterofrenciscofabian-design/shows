from __future__ import annotations

import hashlib
import hmac
import os
import time
import json
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request, WebSocket

COOKIE = "codespace_session"
SESSION_SECONDS = 12 * 60 * 60


def create_session() -> str:
    secret = os.getenv("MCP_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "El administrador debe configurar MCP_SECRET (mínimo 32 caracteres)")
    expiry = str(int(time.time()) + SESSION_SECONDS)
    signature = hmac.new(secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def valid_session(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    expiry, signature = value.split(".", 1)
    if not expiry.isdecimal() or int(expiry) < time.time() or int(expiry) > time.time() + SESSION_SECONDS + 60:
        return False
    secret = os.getenv("MCP_SECRET", "")
    if len(secret) < 32:
        return False
    expected = hmac.new(secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


async def require_session(request: Request) -> None:
    if not valid_session(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Inicia sesión para continuar")


def verify_password(candidate: str) -> bool:
    configured = os.getenv("APP_PASSWORD", "")
    return len(configured) >= 16 and hmac.compare_digest(candidate.encode(), configured.encode())


def verify_telegram_init_data(init_data: str) -> str:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_ids = {value.strip() for value in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if value.strip()}
    if not bot_token or not admin_ids or len(init_data) > 8192:
        raise ValueError("El acceso por Telegram no está configurado")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    data = dict(pairs)
    if len(data) != len(pairs) or "hash" not in data or "auth_date" not in data or "user" not in data:
        raise ValueError("Datos de inicio de Telegram no válidos")
    supplied_hash = data.pop("hash")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_hash, expected_hash):
        raise ValueError("Firma de Telegram no válida")
    try:
        auth_date = int(data["auth_date"])
        user = json.loads(data["user"])
        user_id = str(user["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Identidad de Telegram no válida") from exc
    if abs(time.time() - auth_date) > 300:
        raise ValueError("La sesión de Telegram expiró; vuelve a abrir la mini app")
    if user_id not in admin_ids:
        raise ValueError("La cuenta de Telegram no está autorizada")
    return user_id


def websocket_session(socket: WebSocket) -> bool:
    return valid_session(socket.cookies.get(COOKIE))