from __future__ import annotations

import asyncio
import base64
import hmac
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.agent import make_diff, propose
from app.auth import COOKIE, create_session, require_session, verify_password, verify_telegram_init_data, websocket_session
from app.hub import BridgeHub
from app.media import edit_image_bytes, image_bytes, video_bytes

BASE = Path(__file__).parent
hub = BridgeHub()
plans: dict[str, dict] = {}
auth = [Depends(require_session)]
image_semaphore = asyncio.Semaphore(1)
video_semaphore = asyncio.Semaphore(1)
login_attempts: dict[str, list[float]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    plans.clear()


app = FastAPI(title="Codespace Neon", version="1.0.0", lifespan=lifespan)
if os.getenv("TRUSTED_HOSTS"):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[item.strip() for item in os.environ["TRUSTED_HOSTS"].split(",")])


@app.exception_handler(ConnectionError)
async def bridge_connection_error(_: Request, exc: ConnectionError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(RuntimeError)
async def bridge_tool_error(_: Request, exc: RuntimeError):
    return JSONResponse(status_code=502, content={"detail": str(exc)[:1000]})


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class TelegramLoginBody(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)


class ChatBody(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    messages: list[dict] | None = Field(default=None, min_length=1, max_length=60)
    message: str | None = Field(default=None, max_length=12000)


class AgentBody(BaseModel):
    task: str = Field(min_length=1, max_length=3000)
    model: str | None = Field(default=None, max_length=120)
    action: Literal["plan", "apply", "cancel"] = "plan"
    plan_id: str | None = Field(default=None, max_length=32)


class TTSBody(BaseModel):
    text: str
    engine: Literal["kokoro", "piper", "xtts"]
    format: Literal["wav", "mp3"] = "wav"


class FileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=1_000_000)


class MCPBody(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)


class ImageBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    model: str = "flux"


class VideoBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    model: str = "wan"


class SaveMediaBody(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=12_000_000)


@app.get("/health")
async def health():
    return {"ok": True, "service": "codespace-neon"}


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    now = time.time()
    address = request.client.host if request.client else "unknown"
    attempts = [stamp for stamp in login_attempts.get(address, []) if now - stamp < 900]
    if len(attempts) >= 8:
        raise HTTPException(429, "Demasiados intentos. Espera 15 minutos.")
    attempts.append(now)
    login_attempts[address] = attempts
    if not verify_password(body.password):
        raise HTTPException(401, "Credenciales no válidas")
    login_attempts.pop(address, None)
    response = JSONResponse({"authenticated": True})
    response.set_cookie(COOKIE, create_session(), httponly=True, secure=os.getenv("COOKIE_SECURE", "true").lower() == "true", samesite="strict", max_age=43200, path="/")
    return response


@app.post("/api/login/telegram")
async def telegram_login(body: TelegramLoginBody):
    try:
        verify_telegram_init_data(body.init_data)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    response = JSONResponse({"authenticated": True})
    response.set_cookie(COOKIE, create_session(), httponly=True, secure=os.getenv("COOKIE_SECURE", "true").lower() == "true", samesite="strict", max_age=43200, path="/")
    return response


@app.post("/api/logout", dependencies=auth)
async def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/session")
async def session(request: Request):
    from app.auth import valid_session
    return {"authenticated": valid_session(request.cookies.get(COOKIE))}


@app.get("/api/models", dependencies=auth)
async def models():
    suggested = ["qwen2.5:7b", "deepseek-coder-v2", "dolphin-llama3", "dolphin-mistral", "mistral:7b"]
    if not hub.bridge:
        return {"connected": False, "models": [], "suggested": suggested}
    data = await hub.invoke("ollama_models", timeout=12)
    return {"connected": data.get("connected", False), "models": [{"id": name, "label": name} for name in data.get("models", [])], "suggested": suggested}


@app.post("/api/chat", dependencies=auth)
@app.post("/chat", dependencies=auth)
async def chat(body: ChatBody):
    allowed = {"system", "user", "assistant"}
    messages = body.messages or ([{"role": "user", "content": body.message}] if body.message else [])
    if not messages:
        raise HTTPException(400, "Indica message o al menos un elemento en messages")
    if any(not isinstance(message, dict) or not isinstance(message.get("content"), str) or message.get("role") not in allowed or len(message["content"]) > 12000 for message in messages):
        raise HTTPException(400, "Mensajes no válidos")
    aliases = {"qwen": os.getenv("QWEN_MODEL", "qwen2.5:7b"), "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-coder-v2"), "dolphin": os.getenv("DOLPHIN_MODEL", "dolphin-llama3"), "mistral": os.getenv("MISTRAL_MODEL", "mistral:7b")}
    model = aliases.get(body.model.lower(), body.model)
    if not model.replace("-", "").replace("_", "").replace(".", "").replace(":", "").isalnum():
        raise HTTPException(400, "Nombre de modelo no válido")
    result = await hub.invoke("ollama_chat", {"model": model, "messages": messages}, timeout=180)
    return {"content": result["content"], "model": model}


@app.post("/api/agent", dependencies=auth)
@app.post("/agent", dependencies=auth)
async def agent(body: AgentBody):
    if body.action in {"apply", "cancel"}:
        plan = plans.get(body.plan_id or "")
        if not plan:
            raise HTTPException(404, "El plan expiró o ya se utilizó")
        if body.action == "cancel":
            plans.pop(body.plan_id, None)
            return {"status": "cancelled", "plan_id": body.plan_id}
        result = await hub.invoke("apply_changes", {"files": plan["files"], "delete": plan["delete"], "expected": plan["expected"]})
        plans.pop(body.plan_id, None)
        return {"status": "applied", **result}
    try:
        snapshot = await hub.invoke("project_snapshot", timeout=60)
        async def bridge_chat(model: str, messages: list[dict[str, str]], output_format: str | None) -> dict[str, str]:
            return await hub.invoke("ollama_chat", {"model": model, "messages": messages, "format": output_format}, timeout=180)

        selected_model = body.model or os.getenv("AGENT_MODEL", "deepseek-coder-v2")
        selected_model = {"qwen": os.getenv("QWEN_MODEL", "qwen2.5:7b"), "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-coder-v2"), "dolphin": os.getenv("DOLPHIN_MODEL", "dolphin-llama3"), "mistral": os.getenv("MISTRAL_MODEL", "mistral:7b")}.get(selected_model.lower(), selected_model)
        plan = await propose(body.task, snapshot, selected_model, bridge_chat)
    except (ConnectionError, RuntimeError, ValueError) as exc:
        raise HTTPException(502, str(exc)[:500]) from exc
    if len(plans) >= 20:
        plans.pop(next(iter(plans)))
    plans[plan["id"]] = plan
    plan["diff"] = make_diff(plan, snapshot)
    return {"status": "awaiting_confirmation", **plan}


@app.get("/api/files", dependencies=auth)
@app.get("/files", dependencies=auth)
async def files(path: str = Query(default=".", max_length=500)):
    return await hub.invoke("list_files", {"path": path})


@app.get("/api/files/read", dependencies=auth)
async def read_file(path: str = Query(min_length=1, max_length=500)):
    return await hub.invoke("read_file", {"path": path})


@app.post("/api/files/write", dependencies=auth)
async def write_file(body: FileWrite):
    return await hub.invoke("write_file", body.model_dump())


@app.delete("/api/files", dependencies=auth)
async def delete_file(path: str = Query(min_length=1, max_length=500)):
    return await hub.invoke("delete_file", {"path": path})


@app.post("/api/files/folder", dependencies=auth)
async def create_folder(path: str = Query(min_length=1, max_length=500)):
    return await hub.invoke("create_folder", {"path": path})


@app.post("/api/terminal", dependencies=auth)
async def terminal(body: dict):
    if not isinstance(body.get("command"), str):
        raise HTTPException(400, "Indica un comando")
    return await hub.invoke("run_terminal", {"command": body["command"]}, timeout=180)


@app.get("/api/git", dependencies=auth)
async def git_status():
    return await hub.invoke("git_status")


@app.post("/api/git", dependencies=auth)
async def git_action(body: dict):
    if body.get("action") == "commit":
        return await hub.invoke("git_commit", {"message": body.get("message", "")})
    if body.get("action") == "push":
        return await hub.invoke("git_push")
    raise HTTPException(400, "Acción Git no reconocida")


@app.get("/api/system", dependencies=auth)
async def system():
    if not hub.bridge:
        return {"bridge": await hub.status(), "ollama": False, "host": None}
    try:
        host, ollama = await asyncio.gather(hub.invoke("system_info"), hub.invoke("ollama_models", timeout=12))
    except (ConnectionError, RuntimeError, asyncio.TimeoutError):
        host, ollama = None, {"connected": False}
    return {"bridge": await hub.status(), "ollama": ollama.get("connected", False), "host": host}


@app.post("/api/tts", dependencies=auth)
@app.post("/tts", dependencies=auth)
async def tts(body: TTSBody):
    result = await hub.invoke("tts_render", {"text": body.text, "engine": body.engine, "format": body.format}, timeout=240)
    try:
        content = base64.b64decode(result["audio"], validate=True)
    except (ValueError, KeyError) as exc:
        raise HTTPException(502, "El Codespace devolvió audio no válido") from exc
    return Response(content, media_type=result["media_type"], headers={"Content-Disposition": f"attachment; filename=speech.{body.format}", "Cache-Control": "no-store"})


@app.post("/api/image", dependencies=auth)
@app.post("/image", dependencies=auth)
async def image(body: ImageBody):
    content = await asyncio.to_thread(image_bytes, body.prompt, body.model)
    return Response(content, media_type="image/png", headers={"Content-Disposition": "inline; filename=generated.png", "Cache-Control": "no-store"})


@app.post("/api/image/edit", dependencies=auth)
async def image_edit(prompt: str = Query(min_length=1, max_length=2000), model: str = "flux", image: UploadFile = File(...)):
    if image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(415, "Sube una imagen PNG, JPEG o WebP")
    content = await image.read(8_000_001)
    if len(content) > 8_000_000:
        raise HTTPException(413, "La imagen supera el límite de 8 MB")
    result = await asyncio.to_thread(edit_image_bytes, content, prompt, model)
    return Response(result, media_type="image/png", headers={"Content-Disposition": "inline; filename=edited.png", "Cache-Control": "no-store"})


@app.post("/api/video", dependencies=auth)
@app.post("/video", dependencies=auth)
async def video(body: VideoBody):
    content = await asyncio.to_thread(video_bytes, body.prompt, body.model)
    return Response(content, media_type="video/mp4", headers={"Content-Disposition": "inline; filename=generated.mp4", "Cache-Control": "no-store"})


@app.post("/api/media/save", dependencies=auth)
async def save_media(body: SaveMediaBody):
    try:
        content = base64.b64decode(body.content, validate=True)
    except ValueError as exc:
        raise HTTPException(400, "Contenido base64 no válido") from exc
    if len(content) > 8_000_000 or not body.path.startswith("assets/"):
        raise HTTPException(400, "Solo se pueden guardar medios de hasta 8 MB dentro de assets/")
    return await hub.invoke("write_binary", {"path": body.path, "content": base64.b64encode(content).decode("ascii")})


@app.get("/api/mcp", dependencies=auth)
async def mcp_status():
    tools = ["read_file", "write_file", "delete_file", "list_files", "create_folder", "run_terminal", "git_status", "git_commit", "git_push", "pm2_list", "pm2_restart", "pm2_logs", "docker_ps", "docker_restart", "system_info", "ollama_models", "ollama_chat", "tts_generate", "write_binary", "generate_image", "edit_image", "generate_video", "upscale_image", "caption_image"]
    return {"bridge": await hub.status(), "tools": tools}


@app.post("/api/mcp", dependencies=auth)
async def mcp_call(body: MCPBody):
    allowed = {"read_file", "write_file", "delete_file", "list_files", "create_folder", "run_terminal", "git_status", "git_commit", "git_push", "pm2_list", "pm2_restart", "pm2_logs", "docker_ps", "docker_restart", "system_info", "ollama_models", "ollama_chat", "tts_generate", "write_binary", "generate_image", "edit_image", "generate_video", "upscale_image", "caption_image"}
    if body.tool not in allowed:
        raise HTTPException(400, "Herramienta MCP no reconocida")
    return await hub.invoke(body.tool, body.args, timeout=300)


@app.websocket("/ws/bridge")
async def bridge_socket(socket: WebSocket):
    expected, supplied = os.getenv("MCP_SECRET", ""), socket.headers.get("authorization", "")
    if len(expected) < 32 or not hmac.compare_digest(supplied, f"Bearer {expected}"):
        await socket.close(code=1008)
        return
    await socket.accept()
    await hub.attach(socket)


@app.websocket("/ws")
async def browser_socket(socket: WebSocket):
    if not websocket_session(socket):
        await socket.close(code=1008)
        return
    await socket.accept()
    hub.browsers.add(socket)
    await socket.send_json({"type": "bridge_status", **await hub.status()})
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        hub.browsers.discard(socket)


@app.get("/")
async def index():
    return FileResponse(BASE / "app" / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "app" / "static"), name="static")