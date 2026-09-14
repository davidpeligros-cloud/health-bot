"""
audio_transcriber.py — Servicio de transcripción de notas de voz usando la API de Groq Whisper.
Convierte audios de Telegram (.ogg/.oga/.mp3) en texto en español de forma ultra rápida.
"""
from __future__ import annotations

import logging
from typing import Optional

from groq import AsyncGroq

from bot.config import settings

logger = logging.getLogger(__name__)


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> Optional[str]:
    """
    Envía los bytes de un archivo de audio a Groq Whisper y devuelve el texto transcrito.
    """
    if not settings.groq_api_key or settings.groq_api_key.startswith("dummy"):
        logger.error("GROQ_API_KEY no configurada para transcripción de audio.")
        return None

    if not audio_bytes:
        return None

    client = AsyncGroq(api_key=settings.groq_api_key)
    models_to_try = ["whisper-large-v3-turbo", "whisper-large-v3"]

    for model_name in models_to_try:
        try:
            audio_file = (filename, audio_bytes, "audio/ogg")
            transcription = await client.audio.transcriptions.create(
                file=audio_file,
                model=model_name,
                language="es",
                response_format="json",
                temperature=0.0,
            )
            text = (transcription.text or "").strip()
            if text:
                logger.info("Audio transcrito exitosamente con %s (%d caracteres).", model_name, len(text))
                return text
        except Exception as exc:
            logger.warning("Fallo en transcripción con modelo %s: %s", model_name, exc)

    logger.error("No se pudo transcribir el audio con ningún modelo de Whisper.")
    return None
