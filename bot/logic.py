"""
logic.py — Lógica de negocio: TDEE, déficit, proteína, racha, volumen muscular, récords y sugerencias.
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

PROTEIN_RATIO = 1.7  # g por kg de peso corporal (rango óptimo basado en evidencia 1.6-1.8)
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
    prot_target = (targets or {}).get("protein_target_g") or 135.0
    all_nutrition = await db.get_all_daily_nutrition(limit=365)

    today_str = _today()
    prot_by_date = {
        row["date"]: (row.get("protein_g") or 0.0)
        for row in all_nutrition
        if row.get("date")
    }

    today_prot = prot_by_date.get(today_str, 0.0)
    today_met = today_prot >= prot_target

    current_streak = 0
    start_date = datetime.now(ZoneInfo("Europe/Madrid")).date()

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
    valid_dates = []
    for d_str in prot_by_date.keys():
        try:
            cur_d = date.fromisoformat(d_str)
            valid_dates.append((cur_d, d_str))
        except (ValueError, TypeError):
            continue

    valid_dates.sort(key=lambda x: x[0])
    max_streak = 0
    temp_streak = 0
    prev_d: date | None = None

    for cur_d, d_str in valid_dates:
        p = prot_by_date[d_str]
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

    if current_streak == 0:
        motivation = "🌱 ¡Hoy es el momento perfecto para iniciar tu racha! La proteína construye y protege tu masa muscular."
    elif current_streak == 1:
        motivation = "🔥 ¡Primer día completado! El hábito se consolida día a día."
    elif current_streak < 4:
        motivation = "⚡ ¡Gran comienzo! Mantén la consistencia y tu fuerza estará a salvo."
    elif current_streak < 7:
        motivation = "🚀 ¡Ritmo imparable! Tu constancia con la nutrición da resultados directos."
    elif current_streak < 14:
        motivation = "🏆 ¡Más de una semana perfecta! Disciplina de acero protegiendo tu masa magra."
    else:
        motivation = "👑 ¡Nivel Leyenda! Tu disciplina nutricional es ejemplar."

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
    Retorna un resumen semanal completo de nutrición, peso y entrenos.
    """
    end = _today()
    start = _n_days_ago(days - 1)
    targets = await db.get_targets()

    prot_target = (targets or {}).get("protein_target_g") or 135.0
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


# ─────────────────────────────────────────────────────────────────────────────
# Volumen Muscular Semanal (/volumen)
# ─────────────────────────────────────────────────────────────────────────────


async def get_weekly_muscle_volume(days: int = 7) -> dict[str, Any]:
    """Calcula las series efectivas semanales por grupo muscular comparadas con referencias científicas."""
    from bot.muscle_groups import MUSCLE_GROUP_ORDER, MUSCLE_GROUP_EMOJIS, get_volume_status, aggregate_muscle_fatigue

    end = _today()
    start = _n_days_ago(days - 1)

    all_sets = await db.get_exercise_sets_range(start, end)
    fatigue = aggregate_muscle_fatigue(all_sets, end)

    groups_summary = []
    total_sets = 0
    total_volume_kg = 0.0

    for muscle in MUSCLE_GROUP_ORDER:
        data = fatigue.get(muscle, {"sets": 0, "volumen_kg": 0.0, "reps": 0})
        s_count = data["sets"]
        v_kg = data["volumen_kg"]
        total_sets += s_count
        total_volume_kg += v_kg

        status_emoji, status_txt = get_volume_status(s_count)
        groups_summary.append({
            "muscle": muscle,
            "emoji": MUSCLE_GROUP_EMOJIS.get(muscle, "🏋️‍♂️"),
            "sets": s_count,
            "volume_kg": round(v_kg, 1),
            "status_emoji": status_emoji,
            "status_text": status_txt,
        })

    return {
        "period_start": start,
        "period_end": end,
        "total_sets": total_sets,
        "total_volume_kg": round(total_volume_kg, 1),
        "groups": groups_summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Detección y Consulta de Récords Personales (PRs / 1RM)
# ─────────────────────────────────────────────────────────────────────────────


async def detect_workout_prs(
    workout_id: str,
    exercises: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Detecta si alguna serie del entrenamiento actual bate un récord histórico (e1RM).
    """
    from bot.muscle_groups import estimate_1rm

    all_sets = await db.get_all_exercise_sets()
    prev_prs: dict[str, dict[str, Any]] = {}

    for s in all_sets:
        if s.get("workout_id") == workout_id:
            continue
        name = (s.get("exercise_name") or "").strip().lower()
        if not name:
            continue
        w = s.get("weight_kg") or 0.0
        r = s.get("reps") or 0
        e1rm = estimate_1rm(w, r)
        if e1rm > 0:
            if name not in prev_prs or e1rm > prev_prs[name]["e1rm"]:
                prev_prs[name] = {"e1rm": e1rm, "weight_kg": w, "reps": r, "date": s.get("date")}

    detected_prs: list[dict[str, Any]] = []

    for ex in exercises:
        ex_name = (ex.get("name") or "").strip()
        if not ex_name:
            continue
        name_key = ex_name.lower()
        best_set_e1rm = 0.0
        best_set_data = None

        for s in ex.get("sets", []):
            w = s.get("weight_kg") or 0.0
            r = s.get("reps") or 0
            cur_e1rm = estimate_1rm(w, r)
            if cur_e1rm > best_set_e1rm:
                best_set_e1rm = cur_e1rm
                best_set_data = {"weight_kg": w, "reps": r, "e1rm": cur_e1rm}

        if best_set_e1rm > 0 and best_set_data:
            prev = prev_prs.get(name_key)
            if prev:
                diff = round(best_set_e1rm - prev["e1rm"], 1)
                if diff >= 0.5:
                    detected_prs.append({
                        "exercise_name": ex_name,
                        "is_pr": True,
                        "new_1rm": best_set_e1rm,
                        "prev_1rm": prev["e1rm"],
                        "diff": diff,
                        "best_set": f"{best_set_data['weight_kg']:.1f} kg × {best_set_data['reps']} reps",
                        "prev_best_set": f"{prev['weight_kg']:.1f} kg × {prev['reps']} reps",
                    })
            else:
                detected_prs.append({
                    "exercise_name": ex_name,
                    "is_pr": True,
                    "is_first_time": True,
                    "new_1rm": best_set_e1rm,
                    "best_set": f"{best_set_data['weight_kg']:.1f} kg × {best_set_data['reps']} reps",
                })

    return detected_prs


async def get_all_personal_records() -> list[dict[str, Any]]:
    """Devuelve las mejores marcas históricas ordenadas por grupo muscular y 1RM."""
    from bot.muscle_groups import estimate_1rm, get_muscle_group

    all_sets = await db.get_all_exercise_sets()
    prs: dict[str, dict[str, Any]] = {}

    for s in all_sets:
        name = (s.get("exercise_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        w = s.get("weight_kg") or 0.0
        r = s.get("reps") or 0
        e1rm = estimate_1rm(w, r)
        if e1rm <= 0:
            continue

        if key not in prs or e1rm > prs[key]["e1rm"]:
            prs[key] = {
                "exercise_name": name,
                "muscle_group": get_muscle_group(name),
                "e1rm": e1rm,
                "best_weight": w,
                "best_reps": r,
                "date": s.get("date"),
            }

    return sorted(prs.values(), key=lambda x: (x["muscle_group"], -x["e1rm"]))


# ─────────────────────────────────────────────────────────────────────────────
# Sugerencias Inteligentes de Comidas (/quecomo)
# ─────────────────────────────────────────────────────────────────────────────


async def get_meal_suggestions(meal_type: str = "cena") -> str:
    """Genera 3 sugerencias de comidas prácticas adaptadas a los macros restantes de hoy."""
    from groq import AsyncGroq
    from bot.ai_advice import SYSTEM_PROMPT

    today = await get_today_summary()
    cal_rem = today["calories_remaining"]
    prot_rem = today["protein_remaining"]

    if cal_rem <= 100 and prot_rem <= 5:
        return (
            "🎯 <b>¡Objetivos de hoy ya cumplidos!</b>\n\n"
            f"Has alcanzado tus calorías ({today['calories_consumed']:.0f} kcal) y proteína ({today['protein_consumed']:.0f} g).\n"
            "Si tienes hambre o antojo, te recomiendo una infusión relajante (manzanilla, rooibos), agua fría con limón o gelatinas 0%."
        )

    prompt = f"""\
El usuario te pide 3 opciones prácticas de {meal_type} basadas en sus macros restantes de hoy.

DATOS NUTRICIONALES RESTANTES HOY:
- Calorías restantes: {cal_rem:.0f} kcal
- Proteína restante: {prot_rem:.0f} g
- Carbos consumidos hoy: {today['carbs_consumed']:.0f} g
- Grasas consumidas hoy: {today['fat_consumed']:.0f} g

INSTRUCCIONES:
1. Proporciona exactamente 3 opciones de {meal_type} deliciosas, sencillas y rápidas de preparar (menos de 10-15 min) usando ingredientes comunes (huevos, pechuga de pollo, lomo, atún, queso fresco batido, yogur griego, verduras, patata/arroz microondas, frutos secos, etc.).
2. Para cada opción incluye:
   - Nombre apetitoso
   - Ingredientes y cantidades aproximadas
   - Estimación de Kcal y Proteína
3. Adapta las porciones para que sumen aproximadamente los macros restantes.
4. Tono directo, enérgico y profesional en español, listo para Telegram (con emojis discretos y formato claro).
"""

    if not settings.groq_api_key or settings.groq_api_key.startswith("dummy"):
        return (
            "💡 <b>Sugerencia básica (sin IA configurada):</b>\n\n"
            f"Te faltan <b>{prot_rem:.0f} g de proteína</b> y <b>{cal_rem:.0f} kcal</b>.\n"
            "• Opción 1: Tortilla de 2 huevos + 3 claras con lata de atún al natural (~35g prot, 280 kcal).\n"
            "• Opción 2: 200g de pechuga de pollo a la plancha con ensalada verde (~44g prot, 250 kcal).\n"
            "• Opción 3: 250g de yogur griego o queso fresco batido 0% con frutos secos (~25g prot, 220 kcal)."
        )

    try:
        model_name = settings.groq_model or "llama-3.3-70b-versatile"
        client = AsyncGroq(api_key=settings.groq_api_key)
        response = await client.chat.completions.create(
            model=model_name,
            max_tokens=650,
            temperature=0.6,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content
    except Exception as exc:
        logger.error("Error generando sugerencias de comidas: %s", exc)
        return f"⚠️ Error al conectar con la IA: {exc}"


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
    volume = await get_weekly_muscle_volume(days)

    return {
        "today": _today(),
        "targets": targets,
        "protein_streak": streak,
        "weekly_summary": weekly,
        "muscle_volume_last_7_days": volume,
        "nutrition_last_7_days": nutrition_rows,
        "workouts_last_7_days": [
            {k: v for k, v in w.items() if k != "raw_json"}
            for w in workout_rows
        ],
        "weight_last_7_days": weight_rows,
    }
