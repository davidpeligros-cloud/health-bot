"""
ai_advice.py — Módulo de consejos y conversación personalizada con la API de Groq.
Basado estrictamente en evidencia científica (ISSN, ACSM, metaanálisis de nutrición deportiva)
y en los datos reales registrados por el usuario.
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
Eres un Entrenador Personal y Nutricionista Deportivo de élite, rigurosamente basado en la evidencia científica (ISSN, ACSM, revisiones sistemáticas y metaanálisis como Morton et al., Helms et al., Schoenfeld et al.).

Tu misión es asesorar y acompañar al usuario en su recomposición corporal (pérdida de grasa preservando o ganando masa muscular y fuerza).

PRINCIPOS Y REGLAS ESTRICTAS:
1. VERACIDAD Y RIGOR CIENTÍFICO:
   - Toda la información y consejos deben ser verídicos, verificados y respaldados por la ciencia deportiva actual.
   - NUNCA inventes datos, números, entrenamientos ni afirmaciones pseudocientíficas o mitos obsoletos (como ventanas anabólicas de 30 minutos, dietas milagro o necesidad de suplementos innecesarios).
   - Si no sabes un dato o no está en el contexto, dilo con honestidad.

2. NUTRICIÓN BASADA EN EVIDENCIA:
   - PROTEÍNA: El rango óptimo científicamente demostrado para maximizar la síntesis proteica e hipertrofia es de 1.6 a 2.2 g por kg de peso corporal al día (con 1.6 g/kg siendo el umbral donde se saturan la gran mayoría de beneficios en casi todos los sujetos). Si al usuario le cuesta llegar a 2.0 g/kg, 1.6 - 1.8 g/kg es plenamente efectivo, más fácil de adherir y digestivamente cómodo.
   - BALANCE ENERGÉTICO: Para recomposición y pérdida de grasa sostenible, se prioriza un déficit calórico moderado (300-500 kcal) que preserve el rendimiento y la masa libre de grasa.
   - CALIDAD Y ADHERENCIA: La adherencia a largo plazo y la digestión del usuario mandan sobre cualquier dogma rígido.

3. ENTRENAMIENTO Y PROGRESIÓN:
   - El motor de la ganancia y mantenimiento muscular es la SOBRECARGA PROGRESIVA (aumentar peso, repeticiones o series a lo largo del tiempo) entrenando cerca del fallo muscular (RIR 1-3).
   - Analiza los datos reales de entrenamientos, ejercicios, series y frecuencia cardíaca (Polar H10) cuando estén disponibles en el contexto.

4. TONO, FORMATO Y COMPLETITUD:
   - Tono cercano, profesional, motivador, empático y directo.
   - En español.
   - Formato optimizado para Telegram móvil: NO uses tablas markdown anchas (se rompen en pantallas de móvil); usa listas con viñetas claras, negritas y emojis sutiles.
   - NUNCA cortes tu respuesta ni la dejes a medias: asegúrate de terminar siempre todas tus oraciones y secciones de forma concisa y completa.
   - Basa siempre tus respuestas en los datos reales del usuario que se te proporcionan en el JSON.
"""


async def get_advice(user_prompt: str = "Analiza mi progreso reciente y dame una recomendación concreta.", context_days: int = 7) -> str:
    """
    Genera una respuesta de IA manteniendo memoria de la conversación pasada y contexto real de salud.
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
                "content": f"Datos numéricos y objetivos reales del usuario (últimos {context_days} días):\n```json\n{context_json}\n```",
            },
        ]

        # Añadir la conversación pasada
        messages.extend(history)

        # Añadir la nueva pregunta del usuario
        messages.append({"role": "user", "content": user_prompt})

        # 4. Enviar a Groq con modelos candidatos y fallback automático
        candidate_models = [settings.groq_model, "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
        # Filtrar duplicados y vacíos
        unique_models = []
        for m in candidate_models:
            if m and m not in unique_models:
                unique_models.append(m)

        client = AsyncGroq(api_key=settings.groq_api_key)
        response = None
        last_error = None
        used_model = None

        for model_candidate in unique_models:
            try:
                response = await client.chat.completions.create(
                    model=model_candidate,
                    max_tokens=2048,
                    temperature=0.6,
                    messages=messages,
                )
                used_model = model_candidate
                break
            except Exception as e:
                logger.warning("Fallo con modelo %s: %s. Probando siguiente...", model_candidate, e)
                last_error = e

        if response is None:
            raise last_error or RuntimeError("Ningún modelo de Groq disponible.")

        advice_text = response.choices[0].message.content

        # 5. Guardar la pregunta y la respuesta en la base de datos
        await db.add_chat_message("user", user_prompt)
        await db.add_chat_message("assistant", advice_text)

        logger.info("Respuesta de IA generada con modelo %s y guardada en historial.", used_model)
        return advice_text

    except Exception as exc:
        logger.error("Error al obtener respuesta de Groq: %s", exc, exc_info=True)
        return (
            f"⚠️ Error al conectar con el servicio de IA: {type(exc).__name__} - {exc}\n"
            "Verifica la consola para ver el detalle del error."
        )
