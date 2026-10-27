from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

from app.tts import generate
from codespace.security import UnsafeOperation


def render(text: str, engine: str, output_format: str) -> dict[str, Any]:
    if len(text.encode()) > 3000:
        raise UnsafeOperation("El texto supera el límite de 3000 caracteres")
    try:
        audio, media_type = asyncio.run(generate(text, engine, output_format))
    except Exception as exc:
        raise UnsafeOperation(str(getattr(exc, "detail", exc))[:500]) from exc
    if not audio or len(audio) > 8_000_000:
        raise UnsafeOperation("El audio generado está vacío o supera el límite de 8 MB")
    return {"audio": base64.b64encode(audio).decode("ascii"), "media_type": media_type, "format": output_format}
