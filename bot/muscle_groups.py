"""
muscle_groups.py — Clasificación anatómica de ejercicios, cálculo de 1RM estimado (Epley),
seguimiento de volumen semanal y análisis de recuperación.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

MUSCLE_GROUP_ORDER = [
    "Pecho",
    "Espalda",
    "Cuádriceps",
    "Isquios y Glúteos",
    "Hombros",
    "Brazos",
    "Core y Abdomen",
]

MUSCLE_GROUP_EMOJIS = {
    "Pecho": "🛡️",
    "Espalda": "🦅",
    "Cuádriceps": "🦵",
    "Isquios y Glúteos": "🍑",
    "Hombros": "🥥",
    "Brazos": "💪",
    "Core y Abdomen": "🧱",
    "Otros / General": "🏋️‍♂️",
}

# Referencias científicas (Schoenfeld et al. 2017/2019, Brad Schoenfeld & James Krieger)
VOLUME_LANDMARKS = {
    "min_maintenance": 6,   # MV: Mantenimiento mínimo
    "min_optimal": 10,      # MEV/MAV: Mínimo efectivo para hipertrofia
    "max_optimal": 20,      # MAV: Techo adaptativo óptimo
    "max_recoverable": 25,  # MRV: Límite de recuperación
}

MUSCLE_PATTERNS = [
    (
        "Pecho",
        [
            r"banca", r"bench", r"pecho", r"chest", r"apertura", r"fly", r"cruce",
            r"flexion", r"push\s*up", r"inclinado", r"declinado", r"fondos.*pecho",
            r"dips.*chest"
        ]
    ),
    (
        "Espalda",
        [
            r"dominada", r"pull\s*up", r"chin\s*up", r"remo", r"row", r"jalon", r"lat\s*pulldown",
            r"pullover", r"espalda", r"back", r"trapecio", r"shrug", r"encogimiento",
            r"peso\s*muerto(?!.*rumano)", r"deadlift(?!.*romanian)"
        ]
    ),
    (
        "Cuádriceps",
        [
            r"sentadilla", r"squat", r"prensa", r"leg\s*press", r"extension.*pierna",
            r"quad", r"cuadricep", r"zancada", r"lunge", r"bulgara", r"hack", r"sissy"
        ]
    ),
    (
        "Isquios y Glúteos",
        [
            r"rumano", r"romanian", r"rdl", r"femoral", r"hamstring", r"hip\s*thrust",
            r"glute", r"gluteo", r"puente.*gluteo", r"buenos\s*dias", r"good\s*morning",
            r"abductor", r"peso\s*muerto.*piernas\s*rigidas"
        ]
    ),
    (
        "Hombros",
        [
            r"militar", r"military", r"overhead", r"press\s*hombro", r"shoulder",
            r"elevacion.*lateral", r"lateral\s*raise", r"pajaro", r"rear\s*delt",
            r"face\s*pull", r"deltoid", r"frontal", r"arnold"
        ]
    ),
    (
        "Brazos",
        [
            r"bicep", r"curl", r"tricep", r"frances", r"french\s*press", r"martillo",
            r"hammer", r"fondos", r"dips", r"pushdown", r"calavera", r"skull\s*crusher"
        ]
    ),
    (
        "Core y Abdomen",
        [
            r"plank", r"plancha", r"crunch", r"abdomen", r"abdominal", r"core",
            r"elevacion.*piernas", r"leg\s*raise", r"rueda", r"ab\s*wheel", r"pallof"
        ]
    ),
]


def get_muscle_group(exercise_name: str) -> str:
    """Clasifica un ejercicio en su grupo muscular principal."""
    name_clean = exercise_name.lower()
    for group, patterns in MUSCLE_PATTERNS:
        for p in patterns:
            if re.search(p, name_clean):
                return group
    return "Otros / General"


def get_muscle_groups_for_exercise(exercise_name: str) -> list[str]:
    """Devuelve todos los grupos musculares involucrados."""
    return [get_muscle_group(exercise_name)]


def estimate_1rm(weight_kg: float, reps: int) -> float:
    """
    Calcula el 1RM estimado usando la fórmula de Epley.
    1RM = Peso * (1 + Reps / 30)
    """
    if reps <= 0 or weight_kg <= 0:
        return 0.0
    if reps == 1:
        return round(weight_kg, 1)
    e1rm = weight_kg * (1.0 + (reps / 30.0))
    return round(e1rm, 1)


def get_volume_status(sets: int) -> tuple[str, str]:
    """
    Retorna el estado de volumen y emoji según literatura científica:
    - 0 series: Inactivo
    - 1-9 series: Mantenimiento / Volumen Bajo
    - 10-20 series: Rango Óptimo de Hipertrofia
    - 21+ series: Alto Volumen / Cerca del límite recuperable
    """
    if sets == 0:
        return "⚪", "Sin entrenar esta semana (0 series)"
    elif sets < VOLUME_LANDMARKS["min_optimal"]:
        return "🟡", f"Mantenimiento ({sets} series — óptimo: 10-20)"
    elif sets <= VOLUME_LANDMARKS["max_optimal"]:
        return "🟢", f"Óptimo ({sets} series — hipertrofia máxima)"
    else:
        return "🔴", f"Alto volumen ({sets} series — vigila fatiga)"


def recovery_status(hours_since: int) -> tuple[str, str]:
    """Estado de recuperación según horas transcurridas."""
    if hours_since < 24:
        return "🔴", "Recién entrenado / Fatiga alta"
    elif hours_since < 48:
        return "🟡", "Recuperación en progreso"
    elif hours_since < 96:
        return "🟢", "Totalmente recuperado / Listo"
    else:
        return "⚪", "Descansado / Listo para estímulo"


def aggregate_muscle_fatigue(all_sets: list[dict[str, Any]], current_date: str) -> dict[str, Any]:
    """Agrega series y volumen por grupo muscular a partir de una lista de series."""
    result: dict[str, Any] = {
        muscle: {"sets": 0, "volumen_kg": 0.0, "reps": 0, "último_entreno": None}
        for muscle in MUSCLE_GROUP_ORDER
    }
    result["Otros / General"] = {"sets": 0, "volumen_kg": 0.0, "reps": 0, "último_entreno": None}

    for s in all_sets:
        ex_name = s.get("exercise_name", "")
        group = get_muscle_group(ex_name)
        if group not in result:
            result[group] = {"sets": 0, "volumen_kg": 0.0, "reps": 0, "último_entreno": None}

        w = s.get("weight_kg") or 0.0
        r = s.get("reps") or 0
        set_date = s.get("date")

        result[group]["sets"] += 1
        result[group]["reps"] += r
        result[group]["volumen_kg"] += (w * r)

        if set_date:
            if not result[group]["último_entreno"] or set_date > result[group]["último_entreno"]:
                result[group]["último_entreno"] = set_date

    return result
