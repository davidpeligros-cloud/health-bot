"""
ai_advice.py — Módulo de consejos y conversación personalizada con la API de Groq.
"""
from __future__ import annotations

import json
import logging

from groq import AsyncGroq

from bot import db
from bot.config import settings
from bot.logic import build_ai_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Eres un asistente personal de nutrición y entrenamiento. Tu función es conversar con el usuario, \
recordar el contexto de la charla anterior y analizar sus datos numéricos de salud.

Reglas estrictas:
1. NUNCA inventes datos, valores o tendencias que no estén explícitamente en el contexto.
2. Mantén la coherencia con lo que se ha hablado anteriormente en la conversación.
3. El objetivo del usuario es recomposición corporal: perder grasa manteniendo o ganando \
músculo, con un déficit calórico moderado y alta ingesta de proteína.
4. Responde de forma directa, motivadora y sin florituras en español.
5. Usa emojis con moderación para hacer el mensaje más legible en Telegram.
6. NO incluyas títulos ni cabeceras Markdown pesadas.
"""


async def get_advice(user_prompt: str = "Analiza mi progreso reciente y dame una recomendación concreta.", context_days: int = 7) -> str:
    """
    Genera una respuesta de IA manteniendo memoria de la conversación pasada.
    """
    if not settings.groq_api_key or settings.groq_api_key.startswith("dummy"):
        logger.error("GROQ_API_KEY no válida en .env")
        return "⚠️ No se ha configurado una GROQ_API_KEY válida en el archivo .env."

    try:
        # 1. Obtener los datos numéricos de los últimos N días
        context = await build_ai_context(days=context_days)
        context_json = json.dumps(context, ensure_ascii=False, indent=2)

        # 2. Recuperar el historial de conversación guardado (últimos 8 mensajes)
        history = await db.get_recent_chat_history(limit=8)

        # 3. Ensamblar los mensajes para Groq
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": f"Datos numéricos de salud actualizados del usuario (últimos {context_days} días):\n```json\n{context_json}\n```",
            },
        ]

        # Añadir la conversación pasada
        messages.extend(history)

        # Añadir la nueva pregunta del usuario
        messages.append({"role": "user", "content": user_prompt})

        # 4. Enviar a Groq
        client = AsyncGroq(api_key=settings.groq_api_key)

        response = await client.chat.completions.create(
            model="openai/gpt-oss-120b",
            max_tokens=500,
            temperature=0.7,
            messages=messages,
        )

        advice_text = response.choices[0].message.content

        # 5. Guardar la pregunta y la respuesta en la base de datos
        await db.add_chat_message("user", user_prompt)
        await db.add_chat_message("assistant", advice_text)

        logger.info("Respuesta de IA generada y guardada en el historial correctamente.")
        return advice_text

    except Exception as exc:
        logger.error("Error al obtener respuesta de Groq: %s", exc, exc_info=True)
        return (
            f"⚠️ Error al conectar con Groq: {type(exc).__name__} - {exc}\n"
            "Verifica la consola para ver el detalle del error."
        )