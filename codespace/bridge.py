from __future__ import annotations

import argparse
import asyncio
import json
import os
from urllib.parse import urlparse

import websockets

from codespace.tools import execute_tool


async def connect(url: str) -> None:
    secret = os.getenv("MCP_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("Configura MCP_SECRET con al menos 32 caracteres en el Codespace")
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or parsed.path.rstrip("/") != "/ws/bridge":
        raise SystemExit("URL no válida: usa la ruta /ws/bridge del servidor")
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {secret}"}, max_size=12_000_000, ping_interval=20) as socket:
        print("Codespace conectado. Mantén este proceso activo.", flush=True)
        async for message in socket:
            request = json.loads(message)
            if request.get("type") != "rpc":
                continue
            try:
                result = await asyncio.to_thread(execute_tool, request["tool"], request.get("args", {}))
                response = {"type": "rpc_result", "id": request["id"], "ok": True, "result": result}
            except Exception as exc:
                response = {"type": "rpc_result", "id": request.get("id"), "ok": False, "error": str(exc)}
            await socket.send(json.dumps(response, ensure_ascii=False))


def run_mcp() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("Codespace")

    @server.tool()
    def read_file(path: str) -> dict:
        """Lee un archivo del workspace Codespace."""
        return execute_tool("read_file", {"path": path})

    @server.tool()
    def write_file(path: str, content: str) -> dict:
        """Escribe un archivo en el workspace Codespace."""
        return execute_tool("write_file", {"path": path, "content": content})

    @server.tool()
    def delete_file(path: str) -> dict:
        """Elimina un archivo del workspace Codespace."""
        return execute_tool("delete_file", {"path": path})

    @server.tool()
    def list_files(path: str = ".") -> list[str]:
        """Lista los archivos del workspace Codespace."""
        return execute_tool("list_files", {"path": path})

    @server.tool()
    def create_folder(path: str) -> dict:
        """Crea una carpeta en el workspace Codespace."""
        return execute_tool("create_folder", {"path": path})

    @server.tool()
    def run_terminal(command: str) -> dict:
        """Ejecuta un comando permitido por la política de seguridad."""
        return execute_tool("run_terminal", {"command": command})

    @server.tool()
    def git_status() -> dict:
        """Consulta el estado de Git."""
        return execute_tool("git_status", {})

    @server.tool()
    def git_commit(message: str) -> dict:
        """Crea un commit de los archivos rastreados modificados."""
        return execute_tool("git_commit", {"message": message})

    @server.tool()
    def git_push() -> dict:
        """Envía los commits al remoto configurado."""
        return execute_tool("git_push", {})

    @server.tool()
    def pm2_list() -> dict:
        """Lista los procesos gestionados por PM2."""
        return execute_tool("pm2_list", {})

    @server.tool()
    def pm2_restart(name: str) -> dict:
        """Reinicia un proceso PM2 por nombre."""
        return execute_tool("pm2_restart", {"name": name})

    @server.tool()
    def pm2_logs(name: str) -> dict:
        """Obtiene las últimas líneas de logs de PM2."""
        return execute_tool("pm2_logs", {"name": name})

    @server.tool()
    def docker_ps() -> dict:
        """Lista los contenedores Docker en ejecución."""
        return execute_tool("docker_ps", {})

    @server.tool()
    def docker_restart(container: str) -> dict:
        """Reinicia un contenedor Docker por nombre o ID."""
        return execute_tool("docker_restart", {"container": container})

    @server.tool()
    def system_info() -> dict:
        """Muestra información del sistema Codespace."""
        return execute_tool("system_info", {})

    @server.tool()
    def ollama_models() -> dict:
        """Lista los modelos instalados en el Ollama local del Codespace."""
        return execute_tool("ollama_models", {})

    @server.tool()
    def ollama_chat(model: str, messages: list[dict], format: str | None = None) -> dict:
        """Envía mensajes a un modelo Ollama local a través del puente Codespace."""
        return execute_tool("ollama_chat", {"model": model, "messages": messages, "format": format})

    @server.tool()
    def tts_generate(text: str, engine: str = "kokoro", format: str = "wav", path: str = "assets/speech.wav") -> dict:
        """Genera voz con Kokoro, Piper o XTTS-v2 y la guarda en assets/."""
        return execute_tool("tts_generate", {"text": text, "engine": engine, "format": format, "path": path})

    @server.tool()
    def write_binary(path: str, content: str) -> dict:
        """Guarda contenido base64 dentro de assets/ en el workspace."""
        return execute_tool("write_binary", {"path": path, "content": content})

    @server.tool()
    def generate_image(prompt: str, model: str = "flux", path: str = "assets/generated-image.png") -> dict:
        """Genera una imagen con FLUX, SDXL o Juggernaut y la guarda en assets/."""
        return execute_tool("generate_image", {"prompt": prompt, "model": model, "path": path})

    @server.tool()
    def edit_image(path: str, prompt: str, model: str = "flux", output_path: str = "assets/edited-image.png") -> dict:
        """Edita una imagen del workspace mediante inferencia de imágenes."""
        return execute_tool("edit_image", {"path": path, "prompt": prompt, "model": model, "output_path": output_path})

    @server.tool()
    def generate_video(prompt: str, model: str = "wan", path: str = "assets/generated-video.mp4") -> dict:
        """Genera un vídeo mediante un proveedor de inferencia compatible."""
        return execute_tool("generate_video", {"prompt": prompt, "model": model, "path": path})

    @server.tool()
    def upscale_image(path: str, output_path: str = "assets/upscaled-image.png") -> dict:
        """Intenta ampliar una imagen con el modelo de upscaling configurado."""
        return execute_tool("upscale_image", {"path": path, "output_path": output_path})

    @server.tool()
    def caption_image(path: str) -> dict:
        """Obtiene una descripción textual de una imagen."""
        return execute_tool("caption_image", {"path": path})

    server.run(transport="stdio")


def main() -> None:
    parser = argparse.ArgumentParser(description="Puente seguro Codespace / servidor MCP")
    parser.add_argument("mode", choices=["connect", "mcp"])
    parser.add_argument("--url", default=os.getenv("MCP_SERVER_URL", ""))
    args = parser.parse_args()
    if args.mode == "mcp":
        run_mcp()
    elif not args.url:
        parser.error("Indica --url o configura MCP_SERVER_URL")
    else:
        asyncio.run(connect(args.url))


if __name__ == "__main__":
    main()