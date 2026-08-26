"""
muscle_groups.py — Mapeo de ejercicios → grupos musculares y biblioteca de openGym.
Usado para análisis de fatiga, recuperación y planificación de entrenamientos.
"""
from __future__ import annotations

from typing import Literal

# Definición de grupos musculares
MuscleGroup = Literal[
    "pecho", "espalda", "hombros", "brazos", "piernas",
    "gemelos", "core", "glúteos", "cuádriceps", "isquiotibiales"
]

# Mapeo de ejercicios comunes → grupos musculares implicados
EXERCISE_MUSCLE_MAP: dict[str, list[MuscleGroup]] = {
    # Pecho
    "press de banca": ["pecho", "hombros", "brazos"],
    "press inclinado": ["pecho", "hombros"],
    "press declinado": ["pecho"],
    "aperturas": ["pecho"],
    "aperturas en máquina": ["pecho"],
    "fondos": ["pecho", "brazos", "hombros"],
    "pectoral máquina": ["pecho"],
    "press máquina": ["pecho", "hombros"],

    # Espalda
    "peso muerto": ["espalda", "glúteos", "isquiotibiales", "core"],
    "remo": ["espalda", "brazos"],
    "remo máquina": ["espalda", "brazos"],
    "dominadas": ["espalda", "brazos"],
    "jalón polea": ["espalda", "brazos"],
    "remo máquina horizontal": ["espalda", "brazos"],
    "remo inclinado": ["espalda", "brazos"],
    "face pull": ["hombros", "espalda"],

    # Hombros
    "press de hombros": ["hombros", "brazos"],
    "elevación lateral": ["hombros"],
    "elevaciones": ["hombros"],
    "elevación frontal": ["hombros"],
    "encogimiento": ["hombros"],
    "press arnold": ["hombros", "pecho"],

    # Brazos (Bíceps/Tríceps)
    "curl": ["brazos"],
    "curl barra ez": ["brazos"],
    "curl mancuerna": ["brazos"],
    "extensión tríceps": ["brazos"],
    "fondos tríceps": ["brazos"],
    "extensión polea": ["brazos"],
    "dips": ["brazos", "pecho", "hombros"],

    # Piernas - Cuádriceps
    "sentadilla": ["cuádriceps", "glúteos", "isquiotibiales"],
    "sentadilla máquina": ["cuádriceps", "glúteos"],
    "leg press": ["cuádriceps", "glúteos"],
    "prensa de piernas": ["cuádriceps", "glúteos"],
    "extensión de cuádriceps": ["cuádriceps"],
    "extensión pierna": ["cuádriceps"],

    # Piernas - Posteriores
    "peso muerto rumano": ["isquiotibiales", "espalda", "glúteos"],
    "curl femoral": ["isquiotibiales"],
    "curl sentado": ["isquiotibiales"],
    "hip thrust": ["glúteos", "isquiotibiales"],
    "puente": ["glúteos", "isquiotibiales"],

    # Piernas - Glúteos
    "sentadilla búlgara": ["glúteos", "cuádriceps"],
    "paso": ["glúteos", "cuádriceps"],
    "abducción máquina": ["glúteos"],
    "aducción máquina": ["glúteos", "piernas"],

    # Gemelos
    "elevación gemelos": ["gemelos"],
    "gemelos máquina": ["gemelos"],
    "gemelos prensa": ["gemelos"],

    # Core
    "abdominales": ["core"],
    "crunch": ["core"],
    "planchas": ["core", "hombros"],
    "cable rotación": ["core"],
    "pallof press": ["core"],

    # Cardio / Híbridos
    "burpees": ["pecho", "brazos", "core", "piernas"],
    "montañeros": ["core", "hombros", "brazos"],
    "saltos sentadilla": ["piernas", "glúteos"],
}

# Nombre canónico por grupo muscular
MUSCLE_GROUP_EMOJIS = {
    "pecho": "🫀",
    "espalda": "🔙",
    "hombros": "💪",
    "brazos": "💪",
    "piernas": "🦵",
    "gemelos": "🦵",
    "core": "⚙️",
    "glúteos": "🍑",
    "cuádriceps": "🦵",
    "isquiotibiales": "🦵",
}

# Orden de presentación visual
MUSCLE_GROUP_ORDER = [
    "pecho", "espalda", "hombros", "brazos",
    "glúteos", "cuádriceps", "isquiotibiales", "gemelos", "core"
]


def get_muscle_groups_for_exercise(exercise_name: str) -> list[MuscleGroup]:
    """
    Devuelve los grupos musculares para un ejercicio dado.
    Si no encuentra coincidencia exacta, intenta búsqueda parcial.
    """
    exercise_lower = exercise_name.strip().lower()
    
    # Búsqueda exacta
    if exercise_lower in EXERCISE_MUSCLE_MAP:
        return EXERCISE_MUSCLE_MAP[exercise_lower]
    
    # Búsqueda parcial (primeras palabras o contiene)
    for key, muscles in EXERCISE_MUSCLE_MAP.items():
        if key in exercise_lower or exercise_lower in key:
            return muscles
    
    # Default: core (cuando no se identifica)
    return ["core"]


def aggregate_muscle_fatigue(
    exercise_sets: list[dict[str, any]],
    workout_date: str
) -> dict[MuscleGroup, dict[str, any]]:
    """
    Agrega datos de series por grupo muscular.
    Devuelve {grupo: {reps, volumen, sets, último_entreno}}.
    """
    fatigue_map: dict[MuscleGroup, dict[str, any]] = {
        muscle: {"reps": 0, "volumen_kg": 0.0, "sets": 0, "último_entreno": None}
        for muscle in MUSCLE_GROUP_ORDER
    }
    
    for exercise_set in exercise_sets:
        exercise_name = exercise_set.get("exercise_name", "")
        weight_kg = exercise_set.get("weight_kg") or 0.0
        reps = exercise_set.get("reps") or 0
        date_str = exercise_set.get("date")
        
        muscles = get_muscle_groups_for_exercise(exercise_name)
        volume = weight_kg * reps
        
        for muscle in muscles:
            if muscle in fatigue_map:
                fatigue_map[muscle]["reps"] += reps
                fatigue_map[muscle]["volumen_kg"] += volume
                fatigue_map[muscle]["sets"] += 1
                if date_str and (not fatigue_map[muscle]["último_entreno"] or date_str > fatigue_map[muscle]["último_entreno"]):
                    fatigue_map[muscle]["último_entreno"] = date_str
    
    return fatigue_map


def estimate_recovery_hours(
    last_workout_date: str | None,
    today_date: str
) -> float:
    """
    Estima horas de recuperación desde el último entreno.
    """
    if not last_workout_date:
        return 999.0
    
    from datetime import datetime
    last = datetime.fromisoformat(last_workout_date)
    today = datetime.fromisoformat(today_date)
    delta = today - last
    return delta.total_seconds() / 3600


def recovery_status(hours_since_workout: float) -> tuple[str, str]:
    """
    Devuelve emoji y estado de recuperación basado en horas.
    """
    if hours_since_workout < 24:
        return "🔴", "Muy fatigado"
    elif hours_since_workout < 48:
        return "🟠", "Fatigado"
    elif hours_since_workout < 72:
        return "🟡", "En recuperación"
    elif hours_since_workout < 120:
        return "🟢", "Recuperado"
    else:
        return "🟢", "Totalmente recuperado"
