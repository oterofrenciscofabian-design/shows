from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import base64
import httpx
from pathlib import Path
from typing import Any

from codespace.security import UnsafeOperation, validate_command

ROOT = Path(os.getenv("CODESPACE_ROOT", ".")).resolve()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
IGNORED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "build"}
PRIVATE_DIRS = {".aws", ".azure", ".docker", ".gnupg", ".kube", ".ssh"}
PRIVATE_FILES = {".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}
PRIVATE_SUFFIXES = {".pem", ".p12", ".pfx", ".key"}
MAX_FILE_BYTES = 1_000_000


def resolve_path(relative: str) -> Path:
    parts = Path(relative).parts
    if any(_is_private_path_part(part) or part in {".git", ".venv", "venv", "node_modules"} for part in parts):
        raise UnsafeOperation("No se permite acceder a directorios internos ni archivos de entorno")
    candidate = (ROOT / relative).resolve()
    if not candidate.is_relative_to(ROOT):
        raise UnsafeOperation("La ruta debe permanecer dentro del workspace")
    current = ROOT
    for part in parts:
        if part == "..":
            raise UnsafeOperation("No se permiten rutas fuera del workspace")
        current = current / part
        if current.is_symlink():
            raise UnsafeOperation("No se permiten rutas que atraviesen enlaces simbólicos")
    return candidate


def _is_private_path_part(part: str) -> bool:
    name = part.lower()
    return name in PRIVATE_DIRS | PRIVATE_FILES or name == ".env" or name.startswith(".env.") or name.endswith(tuple(PRIVATE_SUFFIXES)) or bool(re.search(r"(?:^|[._-])(secret|token|credential|password)(?:[._-]|$)", name))


def _safe_environment() -> dict[str, str]:
    sensitive = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PRIVATE_KEY", "AUTH")
    blocked = {"PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "NPM_CONFIG_USERCONFIG", "GIT_ASKPASS"}
    return {key: value for key, value in os.environ.items() if key.upper() not in blocked and not any(marker in key.upper() for marker in sensitive)}


def _git(*args: str) -> dict[str, Any]:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, env=_safe_environment())
    return {"returncode": result.returncode, "stdout": result.stdout[-20000:], "stderr": result.stderr[-4000:]}


def _run(command: str) -> dict[str, Any]:
    parts = validate_command(command)
    result = subprocess.run(parts, cwd=ROOT, capture_output=True, text=True, timeout=120, env=_safe_environment())
    return {"returncode": result.returncode, "stdout": result.stdout[-20000:], "stderr": result.stderr[-4000:]}


def _tracked_files(directory: Path, limit: int = 3000) -> list[str]:
    paths = []
    for current, dirs, names in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs if name not in IGNORED and name not in PRIVATE_DIRS and not _is_private_path_part(name) and not (Path(current) / name).is_symlink()]
        for name in names:
            path = Path(current) / name
            if path.is_symlink() or _is_private_path_part(name):
                continue
            paths.append(path.relative_to(ROOT).as_posix())
            if len(paths) >= limit:
                return sorted(paths)
    return sorted(paths)


def execute_tool(name: str, args: dict[str, Any]) -> Any:
    if name in {"tts_render", "tts_generate"}:
        from codespace.tts import render

        engine = str(args.get("engine", "kokoro"))
        output_format = str(args.get("format", "wav"))
        if engine not in {"kokoro", "piper", "xtts"} or output_format not in {"wav", "mp3"}:
            raise UnsafeOperation("Motor o formato TTS no reconocido")
        result = render(str(args.get("text", "")), engine, output_format)
        if name == "tts_render":
            return result
        path = resolve_path(str(args.get("path", f"assets/speech.{output_format}")))
        if not path.relative_to(ROOT).parts or path.relative_to(ROOT).parts[0] != "assets" or path.suffix.lower() != f".{output_format}":
            raise UnsafeOperation("La salida TTS debe usar su extensión y estar dentro de assets/")
        content = base64.b64decode(result["audio"], validate=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"path": str(path.relative_to(ROOT)), "bytes": len(content), "media_type": result["media_type"]}
    if name == "ollama_models":
        try:
            response = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=8)
            response.raise_for_status()
            return {"connected": True, "models": [item["name"] for item in response.json().get("models", [])]}
        except (httpx.HTTPError, ValueError) as exc:
            return {"connected": False, "models": [], "error": str(exc)[:300]}
    if name == "ollama_chat":
        model = str(args.get("model", ""))
        messages = args.get("messages", [])
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", model) or not isinstance(messages, list) or not 1 <= len(messages) <= 60:
            raise UnsafeOperation("Modelo o mensajes de chat no válidos")
        if any(not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str) or len(message["content"]) > 12000 for message in messages):
            raise UnsafeOperation("Formato o tamaño de mensaje no válido")
        output_format = args.get("format")
        if output_format not in {None, "json"}:
            raise UnsafeOperation("Formato de salida no permitido")
        try:
            response = httpx.post(f"{OLLAMA_HOST}/api/chat", json={"model": model, "messages": messages, "stream": False, "format": output_format}, timeout=180)
            response.raise_for_status()
            return {"model": model, "content": response.json().get("message", {}).get("content", "")}
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"Ollama no pudo completar la solicitud: {str(exc)[:300]}") from exc
    if name == "read_file":
        path = resolve_path(str(args.get("path", "")))
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise UnsafeOperation("El archivo no existe o supera el límite de lectura de 1 MB")
        return {"path": str(path.relative_to(ROOT)), "content": path.read_text(encoding="utf-8")}
    if name == "write_file":
        path = resolve_path(str(args.get("path", "")))
        content = str(args.get("content", ""))
        if len(content.encode()) > MAX_FILE_BYTES:
            raise UnsafeOperation("El archivo supera el límite de escritura de 1 MB")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"written": str(path.relative_to(ROOT))}
    if name == "write_binary":
        path = resolve_path(str(args.get("path", "")))
        if not path.relative_to(ROOT).parts or path.relative_to(ROOT).parts[0] != "assets":
            raise UnsafeOperation("Los archivos binarios solo se pueden guardar dentro de assets/")
        try:
            content = base64.b64decode(str(args.get("content", "")), validate=True)
        except ValueError as exc:
            raise UnsafeOperation("Contenido base64 no válido") from exc
        if len(content) > 8_000_000:
            raise UnsafeOperation("El archivo binario supera el límite de 8 MB")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"written": str(path.relative_to(ROOT)), "bytes": len(content)}
    if name == "delete_file":
        path = resolve_path(str(args.get("path", "")))
        if not path.is_file():
            raise UnsafeOperation("Solo se pueden eliminar archivos existentes")
        path.unlink()
        return {"deleted": str(path.relative_to(ROOT))}
    if name == "list_files":
        path = resolve_path(str(args.get("path", ".")))
        if not path.is_dir():
            raise UnsafeOperation("La ruta no es una carpeta")
        return _tracked_files(path)
    if name == "create_folder":
        path = resolve_path(str(args.get("path", "")))
        path.mkdir(parents=True, exist_ok=True)
        return {"created": str(path.relative_to(ROOT))}
    if name == "run_terminal":
        return _run(str(args.get("command", "")))
    if name == "git_status":
        return _git("status", "--short", "--branch")
    if name == "git_commit":
        message = str(args.get("message", "")).strip()
        if not message or len(message) > 200 or "\n" in message:
            raise UnsafeOperation("El mensaje del commit debe tener entre 1 y 200 caracteres en una línea")
        return _git("commit", "-am", message)
    if name == "git_push":
        return _git("push")
    if name == "pm2_list":
        return _run("pm2 list")
    if name in {"pm2_restart", "pm2_logs"}:
        action = "restart" if name == "pm2_restart" else "logs"
        return _run(f"pm2 {action} {args.get('name', '')}")
    if name == "docker_ps":
        return _run("docker ps")
    if name == "docker_restart":
        container = str(args.get("container", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", container):
            raise UnsafeOperation("Identificador de contenedor no válido")
        return {"returncode": 0, "stdout": subprocess.run(["docker", "restart", container], cwd=ROOT, capture_output=True, text=True, timeout=60, env=_safe_environment()).stdout, "stderr": ""}
    if name == "system_info":
        try:
            import psutil

            memory = psutil.virtual_memory()
            cpu_percent, ram_percent, ram_total, ram_available = psutil.cpu_percent(interval=0.1), memory.percent, memory.total, memory.available
        except ImportError:
            cpu_percent, ram_percent, ram_total, ram_available = None, None, None, None
        return {"workspace": str(ROOT), "platform": os.uname().sysname, "python": os.sys.version.split()[0], "cpu_percent": cpu_percent, "ram_percent": ram_percent, "ram_total": ram_total, "ram_available": ram_available, "disk": shutil.disk_usage(ROOT)._asdict()}
    if name in {"generate_image", "edit_image", "generate_video", "upscale_image", "caption_image"}:
        return _media_tool(name, args)
    if name == "project_snapshot":
        files = {}
        size = 0
        for relative in _tracked_files(ROOT, 1000):
            path = resolve_path(relative)
            try:
                if path.stat().st_size > 32_000:
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if size + len(content.encode()) > 500_000:
                break
            files[relative] = content
            size += len(content.encode())
        return files
    if name == "apply_changes":
        return _apply_changes(args)
    raise UnsafeOperation(f"Herramienta desconocida: {name}")


def _media_tool(name: str, args: dict[str, Any]) -> Any:
    from app.media import caption_image_bytes, edit_image_bytes, image_bytes, upscale_image_bytes, video_bytes

    token = os.getenv("HF_TOKEN", "")
    if not token:
        raise UnsafeOperation("Configura HF_TOKEN en el Codespace para usar herramientas de medios")
    if name == "generate_image":
        relative = str(args.get("path", "assets/generated-image.png"))
        path = resolve_path(relative)
        if not path.relative_to(ROOT).parts or path.relative_to(ROOT).parts[0] != "assets":
            raise UnsafeOperation("La salida de imagen debe estar dentro de assets/")
        data = image_bytes(str(args.get("prompt", ""))[:2000], str(args.get("model", "flux")))
    elif name == "edit_image":
        source = resolve_path(str(args.get("path", "")))
        output = resolve_path(str(args.get("output_path", "assets/edited-image.png")))
        if not source.is_file() or source.stat().st_size > 8_000_000 or output.relative_to(ROOT).parts[0] != "assets":
            raise UnsafeOperation("Archivo fuente o ruta de salida no válidos")
        data = edit_image_bytes(source.read_bytes(), str(args.get("prompt", ""))[:2000], str(args.get("model", "flux")))
        path = output
    elif name == "generate_video":
        path = resolve_path(str(args.get("path", "assets/generated-video.mp4")))
        if path.relative_to(ROOT).parts[0] != "assets":
            raise UnsafeOperation("La salida de vídeo debe estar dentro de assets/")
        data = video_bytes(str(args.get("prompt", ""))[:2000], str(args.get("model", "wan")))
    elif name == "upscale_image":
        source = resolve_path(str(args.get("path", "")))
        path = resolve_path(str(args.get("output_path", "assets/upscaled-image.png")))
        if not source.is_file() or source.stat().st_size > 8_000_000 or path.relative_to(ROOT).parts[0] != "assets":
            raise UnsafeOperation("Archivo fuente o ruta de salida no válidos")
        data = upscale_image_bytes(source.read_bytes())
    else:
        source = resolve_path(str(args.get("path", "")))
        if not source.is_file() or source.stat().st_size > 8_000_000:
            raise UnsafeOperation("Archivo fuente no válido")
        result = caption_image_bytes(source.read_bytes())
        return {"path": str(source.relative_to(ROOT)), "caption": result}
    if not data or len(data) > 8_000_000:
        raise UnsafeOperation("El archivo generado está vacío o supera el límite de 8 MB")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path.relative_to(ROOT)), "bytes": len(data)}


def _apply_changes(args: dict[str, Any]) -> dict[str, Any]:
    files = args.get("files", {})
    deletes = args.get("delete", [])
    expected = args.get("expected", {})
    if not isinstance(files, dict) or not isinstance(deletes, list) or not isinstance(expected, dict) or len(files) + len(deletes) > 30:
        raise UnsafeOperation("El plan debe contener como máximo 30 cambios")
    staged: dict[Path, str] = {}
    for relative, content in files.items():
        path = resolve_path(str(relative))
        if not isinstance(content, str) or len(content.encode()) > 150_000:
            raise UnsafeOperation("Contenido de archivo no válido o demasiado grande")
        staged[path] = content
    removed = [resolve_path(str(relative)) for relative in deletes]
    if any(not path.is_file() for path in removed):
        raise UnsafeOperation("El plan intenta eliminar un archivo inexistente")

    for path in [*staged, *removed]:
        relative = path.relative_to(ROOT).as_posix()
        original = expected.get(relative)
        if original is None:
            if path.exists():
                raise UnsafeOperation(f"Conflicto: {relative} ya existe y no se revisó en el plan")
        elif not path.is_file() or path.read_bytes() != original.encode("utf-8"):
            raise UnsafeOperation(f"Conflicto: {relative} cambió desde que se generó el diff")

    backups: dict[Path, bytes | None] = {}
    changed = list(staged) + removed
    try:
        for path in changed:
            backups[path] = path.read_bytes() if path.is_file() else None
        for path, content in staged.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
                temp.write(content)
                temporary = Path(temp.name)
            temporary.replace(path)
        for path in removed:
            path.unlink()
    except Exception:
        for path, content in backups.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    return {"written": [str(path.relative_to(ROOT)) for path in staged], "deleted": [str(path.relative_to(ROOT)) for path in removed]}