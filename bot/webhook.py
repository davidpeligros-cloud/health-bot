"""
webhook.py — Endpoints FastAPI para recibir datos desde Atajos (Shortcuts) de iOS.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from bot.config import settings
from bot import db

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helper para normalizar la fecha a formato YYYY-MM-DD
# ─────────────────────────────────────────────────────────────────────────────

MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12
}

def parse_to_iso_date(date_str: Any) -> str:
    """Convierte fechas como '23 ago 2026, 17:12' o ISO a '2026-08-23'."""
    if not date_str:
        return date.today().isoformat()
    
    s = str(date_str).strip().lower()

    # Si ya empieza con formato YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]

    # Intentar parsear estilo '23 ago 2026, 17:12' o '23 ago 2026'
    match = re.search(r"(\d{1,2})\s+([a-z]{3})\s+(\d{4})", s)
    if match:
        day = int(match.group(1))
        month_str = match.group(2)
        year = int(match.group(3))
        month = MONTHS_ES.get(month_str, date.today().month)
        return f"{year:04d}-{month:02d}-{day:02d}"

    return date.today().isoformat()


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

app = FastAPI(title="Health Bot Webhook", version="1.0.0")


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
    today = date.today().isoformat()
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


@app.post("/webhook/health-data", status_code=status.HTTP_202_ACCEPTED)
async def receive_health_data(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None),
):
    _check_token(x_webhook_token)

    raw_json = await request.json()
    logger.info("JSON RECIBIDO DESDE IPHONE: %s", raw_json)

    if "" in raw_json and isinstance(raw_json[""], dict):
        raw_json = raw_json[""]

    cleaned_json = {}
    for k, v in raw_json.items():
        cleaned_json[k.strip()] = v

    data_type = cleaned_json.get("type")

    if data_type == "nutrition_daily":
        nut_raw = cleaned_json.get("nutrition")
        nut_obj = None

        if isinstance(nut_raw, str) and nut_raw:
            parts = [float(x.strip()) for x in nut_raw.split(",") if x.strip()]
            if len(parts) >= 4:
                # Si el iPhone envió valores inflados (ej: 4642 -> 46.42 g de proteína)
                cal = parts[0]
                prot = parts[1] / 100.0 if parts[1] > 500 else parts[1]
                carbs = parts[2] / 1000.0 if parts[2] > 1000 else parts[2]
                fat = parts[3] / 1000.0 if parts[3] > 1000 else parts[3]
                
                nut_obj = {
                    "calories": cal,
                    "protein_g": prot,
                    "carbs_g": carbs,
                    "fat_g": fat,
                }
        elif isinstance(nut_raw, dict):
            nut_obj = {
                "calories": nut_raw.get("calories") or nut_raw.get("kcal"),
                "protein_g": nut_raw.get("protein_g") or nut_raw.get("protein"),
                "carbs_g": nut_raw.get("carbs_g") or nut_raw.get("carbs"),
                "fat_g": nut_raw.get("fat_g") or nut_raw.get("fat"),
            }

        weight_val = cleaned_json.get("weight_g") or cleaned_json.get("weight_kg")
        body_obj = None
        if weight_val:
            w_float = float(weight_val)
            if w_float > 300:
                w_float = w_float / 1000.0
            body_obj = {"weight_kg": w_float}

        payload_dict = {
            "type": "nutrition_daily",
            "date": parse_to_iso_date(cleaned_json.get("date")),
            "nutrition": nut_obj,
            "body": body_obj,
        }
    else:
        payload_dict = cleaned_json

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
    logger.info("Nutrición guardada para %s: kcal=%s, prot=%s", payload.date, n.calories, n.protein_g)


async def _handle_workout(payload: HealthPayload) -> None:
    w = payload.workout or WorkoutData()
    raw = payload.model_dump(exclude_none=True)

    workout_id = await db.upsert_workout(
        date_str=payload.date,
        name=w.name,
        duration_min=w.duration_min,
        active_energy_kcal=w.active_energy_kcal,
        source=w.source or "hevy_healthkit",
        raw=raw,
    )
    logger.info("Entreno guardado: %s (%s)", w.name, payload.date)

    await _notify_workout(workout_id, w, payload.date)


async def _notify_workout(workout_id: str, w: WorkoutData, date_str: str) -> None:
    from bot.logic import compare_workout

    lines = [f"💪 <b>Entreno registrado</b> — {date_str}"]
    if w.name:
        lines.append(f"🏷 <b>{w.name}</b>")
    if w.duration_min is not None:
        lines.append(f"⏱ Duración: {w.duration_min:.0f} min")
    if w.active_energy_kcal is not None:
        lines.append(f"🔥 Activas: {w.active_energy_kcal:.0f} kcal")

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
        elif not prev:
            lines.append("\n📊 Primera sesión de <b>{}</b> registrada.".format(w.name))

    await _send_telegram("\n".join(lines))


async def _handle_body_weight(payload: HealthPayload) -> None:
    b = payload.body or BodyData()
    if b.weight_kg is None:
        logger.warning("Payload body_weight sin weight_kg para %s", payload.date)
        return
    await db.upsert_body_weight(payload.date, b.weight_kg)
    logger.info("Peso guardado: %.1f kg para %s", b.weight_kg, payload.date)

    from bot.logic import recalculate_targets_if_needed
    recalculated = await recalculate_targets_if_needed()
    if recalculated:
        weight = b.weight_kg
        new_prot = round(weight * 2.0, 1)
        await _send_telegram(
            f"⚖️ Peso actualizado: <b>{weight} kg</b>\n"
            f"🥩 Objetivo de proteína recalculado: <b>{new_prot} g/día</b>"
        )