"""
logic.py — Lógica de negocio: TDEE, déficit, proteína, racha, progresión y comparaciones.
Todas las funciones son async y acceden a la BD a través de db.py.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from bot import db
from bot.config import settings

logger = logging.getLogger(__name__)

PROTEIN_RATIO = 2.0  # g por kg de peso corporal
WEIGHT_CHANGE_THRESHOLD = 1.0  # kg de diferencia para recalcular targets


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades de fecha (Zona horaria Madrid)
# ─────────────────────────────────────────────────────────────────────────────


def _today() -> str:
    return datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()


def _n_days_ago(n: int) -> str:
    current_date = datetime.now(ZoneInfo("Europe/Madrid")).date()
    return (current_date - timedelta(days=n)).isoformat()


def _month_bounds(month: str | None = None) -> tuple[str, str, str]:
    """Devuelve inicio, fin y etiqueta YYYY-MM de un mes válido."""
    if month is None:
        current = datetime.now(ZoneInfo("Europe/Madrid"))
        year, month_number = current.year, current.month
        month_key = f"{year:04d}-{month_number:02d}"
    else:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise ValueError("El mes debe tener formato YYYY-MM") from exc
        year, month_number = parsed.year, parsed.month
        month_key = month

    start_date = date(year, month_number, 1)
    if month_number == 12:
        end_date = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = date(year, month_number + 1, 1) - timedelta(days=1)
    return start_date.isoformat(), end_date.isoformat(), month_key


# ─────────────────────────────────────────────────────────────────────────────
# Peso y TDEE
# ─────────────────────────────────────────────────────────────────────────────


async def get_weight_trend(days: int = 7) -> float | None:
    """Media móvil del peso de los últimos N días."""
    end = _today()
    start = _n_days_ago(days)
    rows = await db.get_weight_range(start, end)
    if not rows:
        return None
    weights = [r["weight_kg"] for r in rows if r["weight_kg"] is not None]
    return round(sum(weights) / len(weights), 2) if weights else None


async def get_latest_weight() -> float | None:
    """Último registro de peso disponible."""
    end = _today()
    start = _n_days_ago(30)
    rows = await db.get_weight_range(start, end)
    if not rows:
        return None
    weight = rows[-1]["weight_kg"]
    return round(weight, 2) if weight is not None else None


async def recalculate_targets_if_needed() -> bool:
    """
    Si el peso actual difiere >1 kg del usado para el objetivo de proteína,
    recalcula y actualiza. Devuelve True si hubo recálculo.
    """
    targets = await db.get_targets()
    if not targets:
        return False

    current_weight = await get_latest_weight()
    if current_weight is None:
        return False

    current_protein_target = targets.get("protein_target_g") or 0
    implied_weight = current_protein_target / PROTEIN_RATIO if current_protein_target else 0

    if abs(current_weight - implied_weight) >= WEIGHT_CHANGE_THRESHOLD:
        new_protein = round(current_weight * PROTEIN_RATIO, 1)
        await db.update_targets(protein_target_g=new_protein)
        logger.info(
            "Proteína objetivo recalculada: %.1f g (peso: %.1f kg)",
            new_protein,
            current_weight,
        )
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Resumen diario
# ─────────────────────────────────────────────────────────────────────────────


async def get_today_summary() -> dict[str, Any]:
    """
    Retorna un diccionario con:
    - datos consumidos hoy
    - objetivos actuales
    - diferencias (cuánto falta o sobra)
    - si hay datos de hoy
    """
    today = _today()
    nutrition = await db.get_daily_nutrition(today)
    targets = await db.get_targets()
    weight = await get_latest_weight()

    cal_target = (targets or {}).get("calorie_target") or settings.default_calorie_target
    prot_target = (targets or {}).get("protein_target_g") or (weight or 80) * PROTEIN_RATIO

    if nutrition:
        cal_consumed = round(nutrition.get("calories") or 0, 1)
        prot_consumed = round(nutrition.get("protein_g") or 0, 1)
        carbs_consumed = round(nutrition.get("carbs_g") or 0, 1)
        fat_consumed = round(nutrition.get("fat_g") or 0, 1)
        has_data = True
    else:
        cal_consumed = prot_consumed = carbs_consumed = fat_consumed = 0
        has_data = False

    return {
        "date": today,
        "has_data": has_data,
        "calories_consumed": cal_consumed,
        "calories_target": cal_target,
        "calories_remaining": round(cal_target - cal_consumed, 1),
        "protein_consumed": prot_consumed,
        "protein_target": prot_target,
        "protein_remaining": round(prot_target - prot_consumed, 1),
        "carbs_consumed": carbs_consumed,
        "fat_consumed": fat_consumed,
        "is_complete": (nutrition or {}).get("is_complete", 0),
        "latest_weight": weight,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Racha de proteína (/racha)
# ─────────────────────────────────────────────────────────────────────────────


async def get_protein_streak() -> dict[str, Any]:
    """
    Calcula la racha actual y el récord de días consecutivos cumpliendo
    el objetivo de proteína diario.
    """
    targets = await db.get_targets()
    prot_target = (targets or {}).get("protein_target_g") or 160.0
    all_nutrition = await db.get_all_daily_nutrition(limit=None)

    today_str = _today()
    # Mapear fecha -> protein_g
    prot_by_date = {
        row["date"]: (row.get("protein_g") or 0.0)
        for row in all_nutrition
        if row.get("date")
    }

    today_prot = prot_by_date.get(today_str, 0.0)
    today_met = today_prot >= prot_target

    # Calcular racha actual hacia atrás
    current_streak = 0
    start_date = datetime.now(ZoneInfo("Europe/Madrid")).date()

    # Si hoy ya se cumplió, empezamos a contar desde hoy; si no, desde ayer
    if today_met:
        check_date = start_date
    else:
        check_date = start_date - timedelta(days=1)

    while True:
        d_str = check_date.isoformat()
        p = prot_by_date.get(d_str, 0.0)
        if p >= prot_target:
            current_streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    # Récord histórico de racha
    sorted_dates = sorted(prot_by_date.keys())
    max_streak = 0
    temp_streak = 0
    prev_d: date | None = None

    for d_str in sorted_dates:
        p = prot_by_date[d_str]
        cur_d = date.fromisoformat(d_str)

        if p >= prot_target:
            if prev_d and (cur_d - prev_d).days == 1:
                temp_streak += 1
            else:
                temp_streak = 1
            max_streak = max(max_streak, temp_streak)
        else:
            temp_streak = 0

        prev_d = cur_d

    max_streak = max(max_streak, current_streak)

    # Mensaje motivacional
    if current_streak == 0:
        motivation = "🌱 ¡Hoy es el momento perfecto para iniciar tu racha! La proteína construye y protege tu músculo."
    elif current_streak == 1:
        motivation = "🔥 ¡Primer día completado! El hábito se construye día a día."
    elif current_streak < 4:
        motivation = "⚡ ¡Gran comienzo! Mantén la consistencia y la masa muscular estará a salvo."
    elif current_streak < 7:
        motivation = "🚀 ¡Ritmo imparable! Tu constancia con la proteína está dando resultados."
    elif current_streak < 14:
        motivation = "🏆 ¡Más de una semana perfecta! Disciplina de acero protegiendo tu masa muscular."
    else:
        motivation = "👑 ¡Nivel Leyenda! Tu disciplina con la nutrición es impecable."

    return {
        "current_streak": current_streak,
        "max_streak": max_streak,
        "protein_target": prot_target,
        "today_protein": round(today_prot, 1),
        "today_met": today_met,
        "today_remaining": max(0.0, round(prot_target - today_prot, 1)),
        "motivation": motivation,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Resumen semanal
# ─────────────────────────────────────────────────────────────────────────────


async def get_weekly_summary(days: int = 7) -> dict[str, Any]:
    """
    Retorna un diccionario con:
    - media de peso de la semana
    - adherencia a proteína (% de días que se cumplió el objetivo)
    - adherencia calórica
    - número de sesiones de entrenamiento
    - lista de nombres de entrenos
    """
    end = _today()
    start = _n_days_ago(days - 1)
    targets = await db.get_targets()

    prot_target = (targets or {}).get("protein_target_g") or 160.0
    cal_target = (targets or {}).get("calorie_target") or settings.default_calorie_target

    nutrition_rows = await db.get_nutrition_range(start, end)
    workout_rows = await db.get_workouts_range(start, end)
    weight_rows = await db.get_weight_range(start, end)

    days_with_prot = [
        r for r in nutrition_rows
        if r.get("protein_g") is not None and r["protein_g"] >= prot_target
    ]
    prot_adherence = (
        round(len(days_with_prot) / len(nutrition_rows) * 100)
        if nutrition_rows
        else None
    )

    days_in_cal_range = [
        r for r in nutrition_rows
        if r.get("calories") is not None
        and cal_target * 0.90 <= r["calories"] <= cal_target * 1.10
    ]
    cal_adherence = (
        round(len(days_in_cal_range) / len(nutrition_rows) * 100)
        if nutrition_rows
        else None
    )

    weights = [r["weight_kg"] for r in weight_rows if r.get("weight_kg") is not None]
    avg_weight = round(sum(weights) / len(weights), 2) if weights else None

    weight_trend = None
    if len(weights) >= 2:
        weight_trend = round(weights[-1] - weights[0], 2)

    workout_names = [r["name"] for r in workout_rows if r.get("name")]
    total_kcal_burned = sum(
        r["active_energy_kcal"] for r in workout_rows if r.get("active_energy_kcal")
    )

    return {
        "period_start": start,
        "period_end": end,
        "days_with_nutrition_data": len(nutrition_rows),
        "protein_adherence_pct": prot_adherence,
        "calorie_adherence_pct": cal_adherence,
        "avg_weight_kg": avg_weight,
        "weight_trend_kg": weight_trend,
        "num_workouts": len(workout_rows),
        "workout_names": workout_names,
        "total_active_kcal": round(total_kcal_burned, 1) if total_kcal_burned else None,
        "protein_target": prot_target,
        "calorie_target": cal_target,
    }


async def get_season_summary(month: str | None = None) -> dict[str, Any]:
    """Calcula la temporada mensual con XP, récords y ejercicio estrella."""
    start, end, month_key = _month_bounds(month)
    workouts = await db.get_workouts_range(start, end)
    nutrition = await db.get_nutrition_range(start, end)
    current_sets = await db.get_exercise_sets_range(start, end)
    all_sets = await db.get_all_exercise_sets()
    targets = await db.get_targets()
    protein_target = (targets or {}).get("protein_target_g") or 160.0

    protein_days = sum(
        1 for row in nutrition
        if row.get("protein_g") is not None and row["protein_g"] >= protein_target
    )
    total_volume = round(sum(
        (row.get("weight_kg") or 0.0) * (row.get("reps") or 0)
        for row in current_sets
    ), 1)

    current_max: dict[str, float] = {}
    previous_max: dict[str, float] = {}
    volume_by_exercise: dict[str, float] = {}
    for row in all_sets:
        name = row.get("exercise_name")
        weight = row.get("weight_kg")
        if not name or weight is None:
            continue
        if row["date"] < start:
            previous_max[name.casefold()] = max(previous_max.get(name.casefold(), 0.0), weight)
        elif row["date"] <= end:
            key = name.casefold()
            current_max[key] = max(current_max.get(key, 0.0), weight)
            volume_by_exercise[name] = volume_by_exercise.get(name, 0.0) + weight * (row.get("reps") or 0)

    personal_records = [
        name for name, weight in current_max.items()
        if name in previous_max and weight > previous_max[name]
    ]
    star = max(volume_by_exercise, key=volume_by_exercise.get) if volume_by_exercise else None
    xp = (
        len(workouts) * 100
        + protein_days * 25
        + len(personal_records) * 100
        + int(total_volume / 1000)
    )

    return {
        "month": month_key,
        "num_workouts": len(workouts),
        "total_volume_kg": total_volume,
        "protein_days": protein_days,
        "personal_records": len(personal_records),
        "personal_record_names": personal_records,
        "star_exercise": star,
        "xp": xp,
        "level": xp // 250 + 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Comparación de entrenos y progresión de ejercicios
# ─────────────────────────────────────────────────────────────────────────────


async def compare_workout(workout_id: str, name: str | None) -> dict[str, Any]:
    """Compara el entreno actual con el anterior del mismo tipo incluyendo FC Polar H10."""
    current = await db.get_last_workout(name)
    previous = await db.get_previous_same_workout(workout_id, name or "") if name else None

    result: dict[str, Any] = {"current": current, "previous": previous, "diff": None}

    if current and previous:
        dur_diff = None
        kcal_diff = None
        hr_avg_diff = None
        hr_max_diff = None

        if current.get("duration_min") and previous.get("duration_min"):
            dur_diff = round(current["duration_min"] - previous["duration_min"], 1)
        if current.get("active_energy_kcal") and previous.get("active_energy_kcal"):
            kcal_diff = round(current["active_energy_kcal"] - previous["active_energy_kcal"], 1)
        if current.get("avg_hr_bpm") and previous.get("avg_hr_bpm"):
            hr_avg_diff = round(current["avg_hr_bpm"] - previous["avg_hr_bpm"], 1)
        if current.get("max_hr_bpm") and previous.get("max_hr_bpm"):
            hr_max_diff = round(current["max_hr_bpm"] - previous["max_hr_bpm"], 1)

        result["diff"] = {
            "duration_min": dur_diff,
            "active_energy_kcal": kcal_diff,
            "avg_hr_bpm": hr_avg_diff,
            "max_hr_bpm": hr_max_diff,
        }

    return result


async def compare_exercise_progression(
    workout_id: str,
    exercises: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Calcula la progresión de cada ejercicio vs su sesión anterior registrada en BD.
    """
    progression: list[dict[str, Any]] = []

    for ex in exercises:
        ex_name = ex["name"]
        cur_volume = ex.get("total_volume_kg", 0.0)
        cur_best_w = ex.get("best_set", {}).get("weight_kg", 0.0)

        prev_sets = await db.get_previous_exercise_sets(ex_name, current_workout_id=workout_id)

        if not prev_sets:
            progression.append({
                "exercise_name": ex_name,
                "has_previous": False,
            })
            continue

        prev_volume = sum((s.get("weight_kg") or 0.0) * (s.get("reps") or 0) for s in prev_sets)
        prev_best_w = max((s.get("weight_kg") or 0.0) for s in prev_sets) if prev_sets else 0.0

        weight_diff = round(cur_best_w - prev_best_w, 1)
        volume_diff = round(cur_volume - prev_volume, 1)

        progression.append({
            "exercise_name": ex_name,
            "has_previous": True,
            "prev_date": prev_sets[0].get("date"),
            "weight_diff": weight_diff,
            "volume_diff": volume_diff,
            "cur_best_w": cur_best_w,
            "prev_best_w": prev_best_w,
        })

    return progression


# ─────────────────────────────────────────────────────────────────────────────
# Contexto para IA
# ─────────────────────────────────────────────────────────────────────────────


async def build_ai_context(days: int = 7) -> dict[str, Any]:
    """Construye el contexto numérico para el módulo de IA."""
    end = _today()
    start = _n_days_ago(days - 1)

    targets = await db.get_targets()
    nutrition_rows = await db.get_nutrition_range(start, end)
    workout_rows = await db.get_workouts_range(start, end)
    weight_rows = await db.get_weight_range(start, end)
    weekly = await get_weekly_summary(days)
    streak = await get_protein_streak()

    return {
        "today": _today(),
        "targets": targets,
        "protein_streak": streak,
        "weekly_summary": weekly,
        "nutrition_last_7_days": nutrition_rows,
        "workouts_last_7_days": [
            {k: v for k, v in w.items() if k != "raw_json"}
            for w in workout_rows
        ],
        "weight_last_7_days": weight_rows,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Análisis de Fatiga y Recuperación Muscular (openGym integration)
# ─────────────────────────────────────────────────────────────────────────────


async def get_muscle_fatigue_map(days: int = 7) -> dict[str, Any]:
    """
    Analiza la fatiga muscular basada en los entrenamientos de los últimos N días.
    Devuelve un mapa de grupos musculares con su estado de recuperación.
    """
    from bot.muscle_groups import (
        aggregate_muscle_fatigue, recovery_status, MUSCLE_GROUP_ORDER, MUSCLE_GROUP_EMOJIS
    )
    
    end = _today()
    start = _n_days_ago(days - 1)
    
    # Obtener todas las series del periodo
    all_sets = await db.get_exercise_sets_range(start, end)
    if not all_sets:
        return {
            "has_data": False,
            "message": "No hay entrenamientos registrados en los últimos 7 días."
        }
    
    # Agregar fatiga por grupo muscular
    fatigue_map = aggregate_muscle_fatigue(all_sets, end)
    
    # Enriquecer con estado de recuperación
    result = {
        "has_data": True,
        "period": f"{start} → {end}",
        "muscles": {},
        "summary": []
    }
    
    for muscle in MUSCLE_GROUP_ORDER:
        data = fatigue_map[muscle]
        if data["sets"] == 0:
            continue
        
        last_workout = data["último_entreno"]
        hours_since = (
            0 if not last_workout else 
            round((datetime.fromisoformat(end) - datetime.fromisoformat(last_workout)).total_seconds() / 3600)
        )
        emoji, status = recovery_status(hours_since)
        
        result["muscles"][muscle] = {
            "emoji": MUSCLE_GROUP_EMOJIS.get(muscle, "❓"),
            "sets": data["sets"],
            "volumen_kg": round(data["volumen_kg"], 1),
            "reps": data["reps"],
            "último_entreno": last_workout,
            "horas_desde": hours_since,
            "status_emoji": emoji,
            "status": status,
        }
        result["summary"].append(f"{MUSCLE_GROUP_EMOJIS.get(muscle)} {muscle}: {status} ({hours_since}h)")
    
    return result


async def get_exercise_library_insights(user_exercises: list[str]) -> dict[str, Any]:
    """
    Analiza los ejercicios del usuario y devuelve insights sobre qué grupos musculares trabaja.
    Útil para recomendar ejercicios complementarios o detectar desbalances.
    """
    from bot.muscle_groups import get_muscle_groups_for_exercise, MUSCLE_GROUP_EMOJIS
    
    muscle_count: dict[str, int] = {}
    exercise_detail = {}
    
    for exercise_name in user_exercises:
        muscles = get_muscle_groups_for_exercise(exercise_name)
        exercise_detail[exercise_name] = muscles
        for muscle in muscles:
            muscle_count[muscle] = muscle_count.get(muscle, 0) + 1
    
    # Detectar desbalances
    most_worked = max(muscle_count, key=muscle_count.get) if muscle_count else None
    least_worked = min(muscle_count, key=muscle_count.get) if muscle_count else None
    
    return {
        "ejercicios_analizados": len(user_exercises),
        "grupos_musculares_únicos": len(muscle_count),
        "frecuencia_por_grupo": {
            f"{MUSCLE_GROUP_EMOJIS.get(m, '?')} {m}": count
            for m, count in sorted(muscle_count.items(), key=lambda x: -x[1])
        },
        "más_trabajado": f"{MUSCLE_GROUP_EMOJIS.get(most_worked, '?')} {most_worked}" if most_worked else None,
        "menos_trabajado": f"{MUSCLE_GROUP_EMOJIS.get(least_worked, '?')} {least_worked}" if least_worked else None,
        "detalle": exercise_detail,
    }
