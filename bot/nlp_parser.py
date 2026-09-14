"""
nlp_parser.py — Procesamiento de lenguaje natural para registro rápido de peso, comidas y macros.
Permite registrar datos mediante frases coloquiales por texto o audio.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from groq import AsyncGroq

from bot.config import settings

logger = logging.getLogger(__name__)


def parse_weight_intent(text: str) -> Optional[float]:
    """
    Detecta si el mensaje es un registro de peso corporal.
    Ejemplos:
      - 'Hoy he pesado 79.5 kg' -> 79.5
      - 'Peso 80.2' -> 80.2
      - 'Mi peso hoy es 79kg' -> 79.0
      - '80.5 kg' -> 80.5
      - 'Pesé 81,3 kilos' -> 81.3
    """
    cleaned = text.strip().lower()
    
    # Si contiene palabras de comida o entreno, no es peso
    if any(w in cleaned for w in ["comido", "desayunado", "cenado", "kcal", "calorias", "proteina", "serie", "reps"]):
        return None

    # Patrones específicos de peso
    patterns = [
        r"(?:hoy\s+)?(?:he\s+pesado|pes[eé]|peso|mi\s+peso(?:\s+hoy)?(?:\s+es)?)\s*[:=]?\s*(\d{2,3}(?:[\.,]\d{1,2})?)\s*(?:kg|kilos|k|kgs)?\b",
        r"^(\d{2,3}(?:[\.,]\d{1,2})?)\s*(?:kg|kilos|k|kgs)$",
        r"\b(\d{2,3}(?:[\.,]\d{1,2})?)\s*(?:kg|kilos)\b",
        r"^pes[eé]\s*(\d{2,3}(?:[\.,]\d{1,2})?)$",
        r"^peso\s*(\d{2,3}(?:[\.,]\d{1,2})?)$",
    ]

    for p in patterns:
        m = re.search(p, cleaned)
        if m:
            val_str = m.group(1).replace(",", ".")
            try:
                val = float(val_str)
                if 40.0 <= val <= 250.0:
                    return round(val, 2)
            except ValueError:
                continue

    return None


def parse_explicit_macros_intent(text: str) -> Optional[dict[str, float]]:
    """
    Detecta si el usuario indica explícitamente calorías o gramos de macros.
    Ejemplos:
      - 'Apunta 300 kcal y 35g de prote'
      - 'Añade 40g de proteína y 250 calorías'
      - 'He tomado un batido: 30g proteina, 200 kcal'
    """
    cleaned = text.strip().lower()

    # Patrones de calorías
    cal_match = re.search(r"(\d+[\.,]?\d*)\s*(?:kcal|calor[ií]as|cal)", cleaned)
    # Patrones de proteína
    prot_match = re.search(r"(\d+[\.,]?\d*)\s*(?:g|gr|gramos)?\s*(?:de\s+)?(?:prote[ií]na|prote|prot)", cleaned)
    # Patrones de carbohidratos
    carbs_match = re.search(r"(\d+[\.,]?\d*)\s*(?:g|gr|gramos)?\s*(?:de\s+)?(?:carbos?|carbohidratos?|hc)", cleaned)
    # Patrones de grasas
    fat_match = re.search(r"(\d+[\.,]?\d*)\s*(?:g|gr|gramos)?\s*(?:de\s+)?(?:grasas?|fat)", cleaned)

    if not cal_match and not prot_match:
        return None

    calories = float(cal_match.group(1).replace(",", ".")) if cal_match else 0.0
    protein = float(prot_match.group(1).replace(",", ".")) if prot_match else 0.0
    carbs = float(carbs_match.group(1).replace(",", ".")) if carbs_match else 0.0
    fat = float(fat_match.group(1).replace(",", ".")) if fat_match else 0.0

    # Si se indicaron macros pero no calorías, estimar calorías con 4-4-9
    if calories == 0.0 and (protein > 0 or carbs > 0 or fat > 0):
        calories = round(protein * 4 + carbs * 4 + fat * 9, 1)

    return {
        "calories": calories,
        "protein_g": protein,
        "carbs_g": carbs,
        "fat_g": fat,
    }


def is_food_description_intent(text: str) -> bool:
    """
    Detecta si el texto parece describir alimentos ingeridos para estimación de macros.
    """
    cleaned = text.strip().lower()
    food_triggers = [
        "he comido", "he cenado", "he desayunado", "he merendado", "me he tomado",
        "comí", "desayuné", "cené", "merendé", "tomé", "almorcé",
        "de comida", "de cena", "de desayuno", "apunta comida", "registrar comida",
        "plato de", "ración de", "tazón de", "batido de"
    ]
    return any(trigger in cleaned for trigger in food_triggers)


async def estimate_food_macros_ai(food_description: str) -> Optional[dict[str, Any]]:
    """
    Usa la IA de Groq para estimar los macros y calorías de una descripción de comida en lenguaje natural.
    """
    if not settings.groq_api_key or settings.groq_api_key.startswith("dummy"):
        return None

    prompt = f"""Analiza los alimentos descritos por el usuario y estima con rigor nutricional sus calorías y macronutrientes.
Texto del usuario: "{food_description}"

Devuelve ÚNICAMENTE un objeto JSON válido con este formato exacto (sin explicaciones adicionales ni markdown):
{{
  "food_name": "Nombre descriptivo y apetitoso del plato",
  "calories": 450,
  "protein_g": 38.0,
  "carbs_g": 45.0,
  "fat_g": 12.0,
  "confidence": "alta|media|baja",
  "notes": "Breve desglose de los ingredientes principales estimados"
}}
"""
    try:
        client = AsyncGroq(api_key=settings.groq_api_key)
        response = await client.chat.completions.create(
            model="openai/gpt-oss-120b",
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        # Limpiar posibles bloques ```json ... ```
        cleaned_json = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
        cleaned_json = re.sub(r"\s*```$", "", cleaned_json).strip()
        data = json.loads(cleaned_json)
        return {
            "food_name": data.get("food_name", "Comida registrada"),
            "calories": float(data.get("calories", 0)),
            "protein_g": float(data.get("protein_g", 0)),
            "carbs_g": float(data.get("carbs_g", 0)),
            "fat_g": float(data.get("fat_g", 0)),
            "notes": data.get("notes", ""),
        }
    except Exception as exc:
        logger.error("Error estimando macros con IA: %s", exc)
        return None
