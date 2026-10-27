from __future__ import annotations

import difflib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

SYSTEM = """Eres Codespace Agent. Recibes una instantánea del workspace y una tarea.
Propón cambios mínimos y coherentes. Devuelve únicamente JSON válido con la forma
{"summary":"resumen breve","files":{"ruta":"contenido COMPLETO nuevo"},"delete":["ruta"]}.
No uses rutas absolutas, ../, archivos .env, secretos, binarios ni más de 30 cambios.
No ejecutes comandos ni afirmes haber cambiado archivos. El usuario revisará y aprobará el plan."""


async def propose(
    task: str,
    snapshot: dict[str, str],
    model: str,
    chat: Callable[[str, list[dict[str, str]], str | None], Awaitable[dict[str, str]]],
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps({"task": task[:3000], "files": snapshot}, ensure_ascii=False)},
    ]
    response = await chat(model, messages, "json")
    data = json.loads(response["content"])
    files, deletes = data.get("files", {}), data.get("delete", [])
    if not isinstance(files, dict) or not isinstance(deletes, list) or len(files) + len(deletes) > 30:
        raise ValueError("El modelo devolvió un plan que no cumple las restricciones")
    for path in [*files.keys(), *deletes]:
        if not isinstance(path, str) or not path or "\\" in path or path.startswith("/") or ".." in path.split("/") or any(part in {".git", ".ssh", ".venv", "venv", "node_modules"} or part == ".env" or part.startswith(".env.") for part in path.split("/")):
            raise ValueError("El modelo propuso una ruta no permitida")
    if any(not isinstance(content, str) or len(content.encode()) > 150_000 for content in files.values()):
        raise ValueError("El modelo propuso contenido no válido o demasiado grande")
    if not files and not deletes:
        raise ValueError("El modelo no propuso cambios de archivos")
    expected = {path: snapshot.get(path) for path in [*files.keys(), *deletes]}
    return {"id": uuid.uuid4().hex, "summary": str(data.get("summary", "Cambios propuestos"))[:300], "files": files, "delete": deletes, "expected": expected}


def make_diff(plan: dict[str, Any], snapshot: dict[str, str]) -> str:
    chunks = []
    for path, content in plan["files"].items():
        chunks.extend(difflib.unified_diff(snapshot.get(path, "").splitlines(keepends=True), content.splitlines(keepends=True), f"a/{path}", f"b/{path}"))
    for path in plan["delete"]:
        chunks.extend(difflib.unified_diff(snapshot.get(path, "").splitlines(keepends=True), [], f"a/{path}", "/dev/null"))
    return "".join(chunks)[:80_000]