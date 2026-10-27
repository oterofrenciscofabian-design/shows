from __future__ import annotations

import io
import os
import json
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from huggingface_hub import InferenceClient

IMAGE_MODELS = {
    "flux": "black-forest-labs/FLUX.1-schnell",
    "sdxl": "stabilityai/stable-diffusion-xl-base-1.0",
    "juggernaut": "RunDiffusion/Juggernaut-XL-v9",
}
VIDEO_MODELS = {
    "wan": "Wan-AI/Wan2.2-TI2V-5B",
    "cogvideox": "THUDM/CogVideoX-5b",
    "hunyuan": "tencent/HunyuanVideo",
}


def _client() -> InferenceClient:
    token = os.getenv("HF_TOKEN", "")
    if not token:
        raise HTTPException(503, "Configura HF_TOKEN para usar la inferencia de medios")
    return InferenceClient(token=token, timeout=240)


def _comfyui_output(prompt: str, workflow_name: str, output_types: set[str]) -> bytes | None:
    base_url = os.getenv("COMFYUI_URL", "").rstrip("/")
    workflow_path = os.getenv(workflow_name, "")
    if not base_url or not workflow_path:
        return None
    path = Path(workflow_path)
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise HTTPException(503, f"El workflow {workflow_name} configurado no existe o es demasiado grande")
    try:
        import httpx

        workflow = json.loads(path.read_text(encoding="utf-8"))
        node_id = os.getenv("COMFYUI_PROMPT_NODE_ID", "")
        prompt_input = os.getenv("COMFYUI_PROMPT_INPUT", "text")
        if not node_id or node_id not in workflow or prompt_input not in workflow[node_id].get("inputs", {}):
            raise HTTPException(503, "Configura COMFYUI_PROMPT_NODE_ID e COMFYUI_PROMPT_INPUT para el workflow")
        workflow[node_id]["inputs"][prompt_input] = prompt
        timeout = httpx.Timeout(15, read=240)
        with httpx.Client(timeout=timeout) as client:
            queued = client.post(f"{base_url}/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex})
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            deadline = time.monotonic() + int(os.getenv("COMFYUI_TIMEOUT", "300"))
            while time.monotonic() < deadline:
                time.sleep(2)
                history = client.get(f"{base_url}/history/{prompt_id}")
                history.raise_for_status()
                outputs = history.json().get(prompt_id, {}).get("outputs", {})
                for output in outputs.values():
                    for kind in output_types:
                        records = output.get(kind, [])
                        if records:
                            record = records[0]
                            result = client.get(f"{base_url}/view", params={"filename": record["filename"], "subfolder": record.get("subfolder", ""), "type": record.get("type", "output")})
                            result.raise_for_status()
                            if len(result.content) > 100_000_000:
                                raise HTTPException(413, "El medio generado supera 100 MB")
                            return result.content
            raise HTTPException(504, "ComfyUI agotó el tiempo de generación")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"ComfyUI no pudo completar el workflow: {str(exc)[:300]}") from exc


def image_bytes(prompt: str, model: str) -> bytes:
    if model not in IMAGE_MODELS:
        raise HTTPException(400, "Modelo de imagen no reconocido")
    try:
        generated = _comfyui_output(prompt, "COMFYUI_IMAGE_WORKFLOW", {"images"})
        if generated is not None:
            return generated
        image = _client().text_to_image(prompt, model=IMAGE_MODELS[model])
        result = io.BytesIO()
        image.save(result, format="PNG")
        return result.getvalue()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"La inferencia de imagen no está disponible: {str(exc)[:300]}") from exc


def edit_image_bytes(image: bytes, prompt: str, model: str) -> bytes:
    if model not in IMAGE_MODELS:
        raise HTTPException(400, "Modelo de imagen no reconocido")
    try:
        import PIL.Image

        source = PIL.Image.open(io.BytesIO(image)).convert("RGB")
        result_image = _client().image_to_image(source, prompt=prompt, model=IMAGE_MODELS[model])
        result = io.BytesIO()
        result_image.save(result, format="PNG")
        return result.getvalue()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"No se pudo editar la imagen: {str(exc)[:300]}") from exc


def upscale_image_bytes(image: bytes) -> bytes:
    try:
        import PIL.Image

        source = PIL.Image.open(io.BytesIO(image)).convert("RGB")
        result_image = _client().image_to_image(source, prompt="Upscale this image with high fidelity", model=os.getenv("IMAGE_UPSCALE_MODEL", "caidas/swin2SR-classical-sr-x2-64"))
        result = io.BytesIO()
        result_image.save(result, format="PNG")
        return result.getvalue()
    except Exception as exc:
        raise HTTPException(502, f"No se pudo ampliar la imagen: {str(exc)[:300]}") from exc


def caption_image_bytes(image: bytes) -> str:
    try:
        import PIL.Image

        source = PIL.Image.open(io.BytesIO(image)).convert("RGB")
        return str(_client().image_to_text(source, model=os.getenv("IMAGE_CAPTION_MODEL", "Salesforce/blip-image-captioning-base")))
    except Exception as exc:
        raise HTTPException(502, f"No se pudo describir la imagen: {str(exc)[:300]}") from exc


def video_bytes(prompt: str, model: str) -> bytes:
    if model not in VIDEO_MODELS:
        raise HTTPException(400, "Modelo de vídeo no reconocido")
    try:
        generated = _comfyui_output(prompt, "COMFYUI_VIDEO_WORKFLOW", {"videos", "gifs"})
        if generated is not None:
            return generated
        return _client().text_to_video(prompt, model=VIDEO_MODELS[model])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"El proveedor no ofrece este modelo de vídeo o requiere un endpoint GPU: {str(exc)[:300]}") from exc