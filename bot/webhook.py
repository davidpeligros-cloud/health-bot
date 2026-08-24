"""
webhook.py — Endpoints FastAPI para recibir datos desde Atajos (Shortcuts) de iOS.
Robusto ante variaciones de mayúsculas, formatos planos o anidados, fechas en español,
frecuencia cardíaca (Polar H10) y rutinas de Hevy.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from bot.config import settings
from bot import db
from bot.hevy_parser import parse_hevy_text

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Normalización de fechas y números
# ─────────────────────────────────────────────────────────────────────────────

MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_to_iso_date(date_str: Any) -> str:
    """Convierte fechas como '24 ago 2026, 17:12' o ISO a '2026-08-24' usando hora de España."""
    if not date_str:
        return datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()

    s = str(date_str).strip().lower()

    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]

    match = re.search(r"(\d{1,2})\s+([a-z]{3,})\s+(\d{4})", s)
    if match:
        day = int(match.group(1))
        month_str = match.group(2)[:3]
        year = int(match.group(3))
        month = MONTHS_ES.get(month_str, datetime.now(ZoneInfo("Europe/Madrid")).month)
        return f"{year:04d}-{month:02d}-{day:02d}"

    return datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()


def parse_float(val: Any) -> Optional[float]:
    """Convierte números con formato español o internacional a float."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_clean = val.strip().replace(" ", "")
        if "," in val_clean and "." in val_clean:
            if val_clean.rfind(",") > val_clean.rfind("."):
                val_clean = val_clean.replace(".", "").replace(",", ".")
            else:
                val_clean = val_clean.replace(",", "")
        else:
            val_clean = val_clean.replace(",", ".")
        try:
            return float(val_clean)
        except ValueError:
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Modelos Pydantic
# ─────────────────────────────────────────────────────────────────────────────


class NutritionData(BaseModel):
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None


class WorkoutData(BaseModel):
    duration_min: Optional[float] = None
    source: Optional[str] = "hevy_healthkit"
    name: Optional[str] = None
    active_energy_kcal: Optional[float] = None
    avg_hr_bpm: Optional[float] = None
    max_hr_bpm: Optional[float] = None
    raw_text: Optional[str] = None
    exercises: Optional[list[dict[str, Any]]] = None


def _normalize_exercises(raw_exercises: Any) -> list[dict[str, Any]]:
    """Normaliza ejercicios/series estructurados enviados por Atajos."""
    if not isinstance(raw_exercises, list):
        return []

    normalized: list[dict[str, Any]] = []
    for raw_exercise in raw_exercises:
        if not isinstance(raw_exercise, dict):
            continue
        exercise = {str(k).strip().lower(): v for k, v in raw_exercise.items()}
        name = exercise.get("name") or exercise.get("nombre") or exercise.get("exercise_name")
        raw_sets = exercise.get("sets") or exercise.get("series") or []
        sets: list[dict[str, Any]] = []
        if isinstance(raw_sets, list):
            for index, raw_set in enumerate(raw_sets, start=1):
                if not isinstance(raw_set, dict):
                    continue
                current = {str(k).strip().lower(): v for k, v in raw_set.items()}
                weight = parse_float(
                    current.get("weight_kg") or current.get("weight") or current.get("peso")
                )
                reps = current.get("reps") or current.get("repetitions") or current.get("repeticiones")
                try:
                    reps_value = int(reps) if reps is not None else None
                except (TypeError, ValueError):
                    reps_value = None
                if reps_value is None:
                    continue
                raw_set_number = current.get("set_number") or current.get("serie") or index
                try:
                    set_number = int(raw_set_number)
                except (TypeError, ValueError):
                    set_number = index
                raw_bodyweight = current.get("is_bodyweight", False)
                is_bodyweight = raw_bodyweight in (True, 1, "1", "true", "yes", "si", "sí")
                sets.append({
                    "set_number": set_number,
                    "weight_kg": weight,
                    "is_bodyweight": is_bodyweight,
                    "added_weight_kg": parse_float(current.get("added_weight_kg")) or 0.0,
                    "reps": reps_value,
                    "rpe": parse_float(current.get("rpe")),
                })
        if name and sets:
            total_volume = round(
                sum((s.get("weight_kg") or 0.0) * s["reps"] for s in sets), 1
            )
            best_set = max(sets, key=lambda s: ((s.get("weight_kg") or 0.0), s["reps"]))
            normalized.append({
                "name": str(name),
                "sets": sets,
                "total_volume_kg": total_volume,
                "best_set": {
                    "weight_kg": best_set.get("weight_kg") or 0.0,
                    "reps": best_set["reps"],
                },
            })
    return normalized


class BodyData(BaseModel):
    weight_kg: Optional[float] = None


class HealthPayload(BaseModel):
    type: Literal["nutrition_daily", "workout", "body_weight"]
    date: str
    timestamp: Optional[str] = None
    nutrition: Optional[NutritionData] = None
    workout: Optional[WorkoutData] = None
    body: Optional[BodyData] = None

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> str:
        return parse_to_iso_date(v)


# ─────────────────────────────────────────────────────────────────────────────
# Referencia al bot de Telegram
# ─────────────────────────────────────────────────────────────────────────────

_telegram_app: Any = None


def set_telegram_app(app: Any) -> None:
    global _telegram_app
    _telegram_app = app


async def _send_telegram(text: str) -> None:
    if _telegram_app is None:
        logger.warning("Bot de Telegram no configurado, no se puede enviar mensaje.")
        return
    try:
        await _telegram_app.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("Error enviando mensaje de Telegram: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# App FastAPI
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Health Bot Webhook", version="1.1.0")


def _check_token(x_webhook_token: str | None) -> None:
    if x_webhook_token != settings.webhook_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de webhook inválido o ausente.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/webhook/ping")
async def ping():
    return {"status": "ok", "message": "Health Bot webhook activo 🟢"}


@app.get("/webhook/status")
async def status_endpoint(x_webhook_token: Optional[str] = Header(default=None)):
    _check_token(x_webhook_token)
    today = datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()
    nutrition = await db.get_daily_nutrition(today)
    workout = await db.get_last_workout()
    weight = await db.get_body_weight(today)
    targets = await db.get_targets()
    return {
        "today": today,
        "nutrition_today": nutrition,
        "last_workout": workout,
        "weight_today": weight,
        "targets": targets,
    }


def normalize_payload_dict(raw_json: dict[str, Any]) -> dict[str, Any]:
    """
    Normaliza diccionarios provenientes de Atajos de iOS independientemente
    de si vienen anidados, planos, con mayúsculas o con nombres de campo alternativos.
    """
    # 1. Desenvolver si viene encapsulado en clave vacía "" (quirk de iOS Shortcuts)
    if "" in raw_json and isinstance(raw_json[""], dict):
        raw_json = raw_json[""]

    # 2. Normalizar todas las claves a minúsculas y sin espacios
    cleaned: dict[str, Any] = {}
    for k, v in raw_json.items():
        if isinstance(k, str):
            cleaned[k.strip().lower()] = v

    # 3. Detectar 'type' flexible
    raw_type = (
        cleaned.get("type")
        or cleaned.get("tipo")
        or cleaned.get("data_type")
        or ""
    )
    raw_type_str = str(raw_type).strip().lower()

    # Inferencia automática de tipo si falta
    target_type: Literal["nutrition_daily", "workout", "body_weight"] = "nutrition_daily"

    if any(w in raw_type_str for w in ["workout", "entreno", "hevy", "training", "ejercicio"]):
        target_type = "workout"
    elif any(w in raw_type_str for w in ["weight", "peso", "body", "bascula"]):
        target_type = "body_weight"
    elif any(w in raw_type_str for w in ["nutrition", "nutricion", "comida", "yazio"]):
        target_type = "nutrition_daily"
    else:
        # Inferir por presencia de campos
        if any(k in cleaned for k in ["workout", "duration", "duration_min", "duracion", "active_energy_kcal", "polar", "fc_media"]):
            target_type = "workout"
        elif any(k in cleaned for k in ["weight_kg", "weight_g", "peso", "weight"]):
            target_type = "body_weight"
        else:
            target_type = "nutrition_daily"

    # 4. Extraer fecha
    raw_date = cleaned.get("date") or cleaned.get("fecha") or cleaned.get("timestamp")
    iso_date = parse_to_iso_date(raw_date)

    result: dict[str, Any] = {
        "type": target_type,
        "date": iso_date,
    }

    # 5. Normalizar campos según target_type
    if target_type == "nutrition_daily":
        nut_raw = cleaned.get("nutrition") or cleaned.get("nutricion")
        if isinstance(nut_raw, dict):
            c_nut = {str(k).strip().lower(): v for k, v in nut_raw.items()}
            cal = parse_float(c_nut.get("calories") or c_nut.get("kcal") or c_nut.get("calorias"))
            prot = parse_float(c_nut.get("protein_g") or c_nut.get("protein") or c_nut.get("proteina"))
            carbs = parse_float(c_nut.get("carbs_g") or c_nut.get("carbs") or c_nut.get("hidratos") or c_nut.get("carbohidratos"))
            fat = parse_float(c_nut.get("fat_g") or c_nut.get("fat") or c_nut.get("grasas") or c_nut.get("grasa"))
        else:
            cal = parse_float(cleaned.get("calories") or cleaned.get("kcal") or cleaned.get("calorias"))
            prot = parse_float(cleaned.get("protein_g") or cleaned.get("protein") or cleaned.get("proteina"))
            carbs = parse_float(cleaned.get("carbs_g") or cleaned.get("carbs") or cleaned.get("hidratos") or cleaned.get("carbohidratos"))
            fat = parse_float(cleaned.get("fat_g") or cleaned.get("fat") or cleaned.get("grasas") or cleaned.get("grasa"))

        result["nutrition"] = {
            "calories": cal,
            "protein_g": prot,
            "carbs_g": carbs,
            "fat_g": fat,
        }

        # Extraer peso complementario si vino en la misma llamada
        weight_val = (
            cleaned.get("weight_kg")
            or cleaned.get("weight_g")
            or cleaned.get("peso")
            or (cleaned.get("body") or {}).get("weight_kg")
        )
        if weight_val is not None:
            w_float = parse_float(weight_val)
            if w_float is not None:
                if w_float > 300:
                    w_float = w_float / 1000.0
                result["body"] = {"weight_kg": w_float}

    elif target_type == "workout":
        w_raw = cleaned.get("workout") or cleaned.get("entreno")
        if isinstance(w_raw, dict):
            c_w = {str(k).strip().lower(): v for k, v in w_raw.items()}
            name = c_w.get("name") or c_w.get("nombre") or c_w.get("workout_name")
            dur = parse_float(c_w.get("duration_min") or c_w.get("duration") or c_w.get("duracion"))
            kcal = parse_float(c_w.get("active_energy_kcal") or c_w.get("calories") or c_w.get("kcal") or c_w.get("calorias"))
            avg_hr = parse_float(c_w.get("avg_hr_bpm") or c_w.get("avg_hr") or c_w.get("avg_heart_rate") or c_w.get("fc_media") or c_w.get("heart_rate_avg"))
            max_hr = parse_float(c_w.get("max_hr_bpm") or c_w.get("max_hr") or c_w.get("max_heart_rate") or c_w.get("fc_max") or c_w.get("heart_rate_max"))
            source = c_w.get("source") or "hevy_healthkit"
            raw_text = c_w.get("raw_text") or c_w.get("text")
            exercises = c_w.get("exercises") or c_w.get("ejercicios")
        else:
            name = cleaned.get("name") or cleaned.get("nombre") or cleaned.get("workout_name")
            dur = parse_float(cleaned.get("duration_min") or cleaned.get("duration") or cleaned.get("duracion"))
            kcal = parse_float(cleaned.get("active_energy_kcal") or cleaned.get("calories") or cleaned.get("kcal") or cleaned.get("calorias"))
            avg_hr = parse_float(cleaned.get("avg_hr_bpm") or cleaned.get("avg_hr") or cleaned.get("avg_heart_rate") or cleaned.get("fc_media") or cleaned.get("heart_rate_avg") or cleaned.get("polar_avg_hr"))
            max_hr = parse_float(cleaned.get("max_hr_bpm") or cleaned.get("max_hr") or cleaned.get("max_heart_rate") or cleaned.get("fc_max") or cleaned.get("heart_rate_max") or cleaned.get("polar_max_hr"))
            source = cleaned.get("source") or "hevy_healthkit"
            raw_text = cleaned.get("raw_text") or cleaned.get("text") or cleaned.get("hevy_text")
            exercises = cleaned.get("exercises") or cleaned.get("ejercicios")

        result["workout"] = {
            "name": str(name) if name else "Entrenamiento",
            "duration_min": dur,
            "active_energy_kcal": kcal,
            "avg_hr_bpm": avg_hr,
            "max_hr_bpm": max_hr,
            "source": str(source),
            "raw_text": raw_text,
            "exercises": _normalize_exercises(exercises),
        }

    elif target_type == "body_weight":
        b_raw = cleaned.get("body") or cleaned.get("cuerpo")
        if isinstance(b_raw, dict):
            w_val = b_raw.get("weight_kg") or b_raw.get("weight") or b_raw.get("peso")
        else:
            w_val = cleaned.get("weight_kg") or cleaned.get("weight") or cleaned.get("peso") or cleaned.get("weight_g")

        w_float = parse_float(w_val)
        if w_float is not None and w_float > 300:
            w_float = w_float / 1000.0

        result["body"] = {"weight_kg": w_float}

    return result


@app.post("/webhook/health-data", status_code=status.HTTP_202_ACCEPTED)
async def receive_health_data(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None),
):
    _check_token(x_webhook_token)

    raw_json = await request.json()
    logger.info("JSON RECIBIDO DESDE IPHONE / ATAJOS: %s", raw_json)

    # Normalización completa y tolerante a fallos
    payload_dict = normalize_payload_dict(raw_json)
    logger.info("PAYLOAD NORMALIZADO: %s", payload_dict)

    payload = HealthPayload(**payload_dict)

    if payload.type == "nutrition_daily":
        await _handle_nutrition(payload)
        if payload.body:
            await _handle_body_weight(payload)
    elif payload.type == "workout":
        await _handle_workout(payload)
    elif payload.type == "body_weight":
        await _handle_body_weight(payload)

    return {"status": "accepted", "type": payload.type, "date": payload.date}


# ─────────────────────────────────────────────────────────────────────────────
# Handlers internos
# ─────────────────────────────────────────────────────────────────────────────


async def _handle_nutrition(payload: HealthPayload) -> None:
    n = payload.nutrition or NutritionData()
    await db.upsert_daily_nutrition(
        date_str=payload.date,
        calories=n.calories,
        protein_g=n.protein_g,
        carbs_g=n.carbs_g,
        fat_g=n.fat_g,
    )
    logger.info(
        "Nutrición guardada para %s: kcal=%s, prot=%s, carb=%s, fat=%s",
        payload.date, n.calories, n.protein_g, n.carbs_g, n.fat_g
    )


async def _handle_workout(payload: HealthPayload) -> None:
    w = payload.workout or WorkoutData()
    raw = payload.model_dump(exclude_none=True)

    workout_id = await db.upsert_workout(
        date_str=payload.date,
        name=w.name,
        duration_min=w.duration_min,
        active_energy_kcal=w.active_energy_kcal,
        avg_hr_bpm=w.avg_hr_bpm,
        max_hr_bpm=w.max_hr_bpm,
        source=w.source or "hevy_healthkit",
        raw=raw,
    )
    logger.info("Entreno guardado: %s (%s) con FC media: %s bpm", w.name, payload.date, w.avg_hr_bpm)

    exercises = w.exercises or []
    if exercises:
        await db.save_workout_exercises(workout_id, payload.date, exercises)
        logger.info("Ejercicios estructurados guardados para workout %s", workout_id)
    elif w.raw_text:
        parsed_hevy = parse_hevy_text(w.raw_text)
        if parsed_hevy and parsed_hevy.get("exercises"):
            await db.save_workout_exercises(workout_id, payload.date, parsed_hevy["exercises"])
            logger.info("Ejercicios de Hevy guardados para workout %s", workout_id)

    await _notify_workout(workout_id, w, payload.date)


async def _notify_workout(workout_id: str, w: WorkoutData, date_str: str) -> None:
    from bot.logic import compare_workout

    lines = [f"💪 <b>Entreno registrado</b> — {date_str}"]
    if w.name:
        lines.append(f"🏷 <b>{w.name}</b>")
    if w.duration_min is not None:
        lines.append(f"⏱ Duración: <b>{w.duration_min:.0f} min</b>")
    if w.active_energy_kcal is not None:
        lines.append(f"🔥 Activas: <b>{w.active_energy_kcal:.0f} kcal</b>")

    # Frecuencia cardíaca Polar H10
    if w.avg_hr_bpm is not None or w.max_hr_bpm is not None:
        hr_parts = []
        if w.avg_hr_bpm is not None:
            hr_parts.append(f"<b>{w.avg_hr_bpm:.0f} bpm</b> media")
        if w.max_hr_bpm is not None:
            hr_parts.append(f"<b>{w.max_hr_bpm:.0f} bpm</b> máx")
        source_label = "Polar H10" if any(
            marker in (w.source or "").lower() for marker in ("polar", "h10")
        ) else (w.source or "fuente no especificada")
        lines.append(f"❤️ FC: {' | '.join(hr_parts)} <i>({source_label})</i>")

    if w.name:
        comparison = await compare_workout(workout_id, w.name)
        prev = comparison.get("previous")
        diff = comparison.get("diff")
        if prev and diff:
            lines.append("")
            lines.append("📊 <b>Vs. sesión anterior:</b>")
            if diff.get("duration_min") is not None:
                sign = "+" if diff["duration_min"] >= 0 else ""
                lines.append(f"  ⏱ Duración: {sign}{diff['duration_min']:.0f} min")
            if diff.get("active_energy_kcal") is not None:
                sign = "+" if diff["active_energy_kcal"] >= 0 else ""
                lines.append(f"  🔥 Activas: {sign}{diff['active_energy_kcal']:.0f} kcal")
            if diff.get("avg_hr_bpm") is not None:
                sign = "+" if diff["avg_hr_bpm"] >= 0 else ""
                lines.append(f"  ❤️ FC media: {sign}{diff['avg_hr_bpm']:.0f} bpm")
            if diff.get("max_hr_bpm") is not None:
                sign = "+" if diff["max_hr_bpm"] >= 0 else ""
                lines.append(f"  ❤️ FC máxima: {sign}{diff['max_hr_bpm']:.0f} bpm")
        elif not prev:
            lines.append(f"\n📊 Primera sesión de <b>{w.name}</b> registrada.")

    await _send_telegram("\n".join(lines))


async def _handle_body_weight(payload: HealthPayload) -> None:
    b = payload.body or BodyData()
    if b.weight_kg is None:
        logger.warning("Payload body_weight sin weight_kg para %s", payload.date)
        return
    await db.upsert_body_weight(payload.date, b.weight_kg)
    logger.info("Peso guardado: %.1f kg para %s", b.weight_kg, payload.date)
    await _send_telegram(f"⚖️ Peso registrado: <b>{b.weight_kg} kg</b>")
