from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException


async def generate(text: str, engine: str, output_format: str = "wav") -> tuple[bytes, str]:
    if not text.strip() or len(text) > 3000:
        raise HTTPException(400, "El texto debe tener entre 1 y 3000 caracteres")
    if output_format not in {"wav", "mp3"}:
        raise HTTPException(400, "Formato no válido")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "speech.wav"
        if engine == "piper":
            binary, model = shutil.which("piper"), os.getenv("PIPER_MODEL", "")
            if not binary or not model:
                raise HTTPException(503, "Configura PIPER_MODEL y el ejecutable piper")
            process = await asyncio.create_subprocess_exec(binary, "--model", model, "--output_file", str(target), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await asyncio.wait_for(process.communicate(text.encode()), timeout=90)
            if process.returncode:
                raise HTTPException(502, f"Piper no pudo generar audio: {stderr.decode(errors='replace')[-500:]}")
        elif engine == "kokoro":
            try:
                from kokoro import KPipeline
                import numpy as np
                import soundfile as sf
            except ImportError as exc:
                raise HTTPException(503, "Instala kokoro, soundfile y numpy para habilitar Kokoro") from exc

            def render() -> None:
                pipeline = KPipeline(lang_code=os.getenv("KOKORO_LANG", "a"))
                audio = [chunk for _, _, chunk in pipeline(text, voice=os.getenv("KOKORO_VOICE", "af_heart"))]
                if not audio:
                    raise RuntimeError("Kokoro no devolvió audio")
                sf.write(target, np.concatenate(audio), 24000)

            try:
                await asyncio.wait_for(asyncio.to_thread(render), timeout=120)
            except TimeoutError as exc:
                raise HTTPException(504, "Kokoro agotó el tiempo de generación") from exc
        elif engine == "xtts":
            binary = shutil.which("tts")
            if not binary:
                raise HTTPException(503, "Instala Coqui TTS (tts) para habilitar XTTS-v2")
            process = await asyncio.create_subprocess_exec(binary, "--text", text, "--model_name", os.getenv("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2"), "--out_path", str(target), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            if process.returncode:
                raise HTTPException(502, f"XTTS-v2 no pudo generar audio: {stderr.decode(errors='replace')[-500:]}")
        else:
            raise HTTPException(400, "Motor TTS no reconocido")
        if not target.is_file():
            raise HTTPException(502, "El motor no produjo un archivo de audio")
        result = await _convert_mp3(target) if output_format == "mp3" else target
        return result.read_bytes(), "audio/mpeg" if output_format == "mp3" else "audio/wav"


async def _convert_mp3(wav: Path) -> Path:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise HTTPException(503, "Instala ffmpeg para convertir el audio a MP3")
    target = wav.with_suffix(".mp3")
    process = await asyncio.create_subprocess_exec(binary, "-nostdin", "-loglevel", "error", "-i", str(wav), "-y", str(target), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    if process.returncode:
        raise HTTPException(502, f"Conversión MP3 fallida: {stderr.decode(errors='replace')[-500:]}")
    wav.unlink(missing_ok=True)
    return target