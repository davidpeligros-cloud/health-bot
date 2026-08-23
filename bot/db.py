"""
db.py — Acceso asíncrono a SQLite con aiosqlite.
Incluye inicialización del esquema y todas las funciones CRUD.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import aiosqlite

from bot.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Inicialización
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_nutrition (
    date        TEXT PRIMARY KEY,
    calories    REAL,
    protein_g   REAL,
    carbs_g     REAL,
    fat_g       REAL,
    is_complete INTEGER DEFAULT 1,
    source      TEXT    DEFAULT 'healthkit',
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS workouts (
    id                 TEXT PRIMARY KEY,
    date               TEXT NOT NULL,
    name               TEXT,
    duration_min       REAL,
    active_energy_kcal REAL,
    source             TEXT DEFAULT 'hevy_healthkit',
    raw_json           TEXT
);

CREATE TABLE IF NOT EXISTS body_weight (
    date      TEXT PRIMARY KEY,
    weight_kg REAL
);

CREATE TABLE IF NOT EXISTS user_targets (
    id                 INTEGER PRIMARY KEY DEFAULT 1,
    calorie_target     REAL,
    protein_target_g   REAL,
    carbs_target_g     REAL,
    fat_target_g       REAL,
    tdee_estimate      REAL,
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS reminders_log (
    date            TEXT,
    reminder_type   TEXT,
    sent_at         TEXT,
    PRIMARY KEY (date, reminder_type)
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db() -> None:
    """Crea el directorio de datos y las tablas si no existen."""
    db_path = settings.database_path
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()

    # Insertar fila de targets por defecto si está vacía
    await ensure_default_targets()
    logger.info("Base de datos inicializada: %s", db_path)


async def ensure_default_targets() -> None:
    """Inserta targets iniciales derivados de la configuración si no hay ninguno."""
    async with aiosqlite.connect(settings.database_path) as db:
        async with db.execute("SELECT id FROM user_targets WHERE id = 1") as cur:
            row = await cur.fetchone()
        if row is None:
            now = _now_iso()
            protein = 80 * 2.0  # 160 g como valor por defecto razonable
            await db.execute(
                """
                INSERT INTO user_targets
                    (id, calorie_target, protein_target_g, carbs_target_g,
                     fat_target_g, tdee_estimate, updated_at)
                VALUES (1, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    settings.default_calorie_target,
                    protein,
                    settings.tdee_estimate,
                    now,
                ),
            )
            await db.commit()
            logger.info("Targets por defecto insertados.")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# chat_history (Memoria de la IA)
# ─────────────────────────────────────────────────────────────────────────────


async def add_chat_message(role: str, content: str) -> None:
    """Guarda un mensaje en el historial (role: 'user' o 'assistant')."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "INSERT INTO chat_history (role, content) VALUES (?, ?)",
            (role, content)
        )
        await db.commit()


async def get_recent_chat_history(limit: int = 10) -> list[dict[str, str]]:
    """Obtiene los últimos N mensajes para darle contexto continuo a la IA."""
    async with aiosqlite.connect(settings.database_path) as db:
        async with db.execute(
            "SELECT role, content FROM chat_history ORDER BY id DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"role": row[0], "content": row[1]} for row in reversed(rows)]


async def clear_chat_history() -> None:
    """Limpia el historial de conversación con la IA."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("DELETE FROM chat_history")
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# daily_nutrition
# ─────────────────────────────────────────────────────────────────────────────


async def upsert_daily_nutrition(
    date_str: str,
    calories: float | None,
    protein_g: float | None,
    carbs_g: float | None,
    fat_g: float | None,
    source: str = "healthkit",
) -> None:
    is_complete = all(v is not None for v in [calories, protein_g, carbs_g, fat_g])
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO daily_nutrition
                (date, calories, protein_g, carbs_g, fat_g, is_complete, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                calories    = COALESCE(excluded.calories, daily_nutrition.calories),
                protein_g   = COALESCE(excluded.protein_g, daily_nutrition.protein_g),
                carbs_g     = COALESCE(excluded.carbs_g, daily_nutrition.carbs_g),
                fat_g       = COALESCE(excluded.fat_g, daily_nutrition.fat_g),
                is_complete = excluded.is_complete,
                source      = excluded.source,
                updated_at  = excluded.updated_at
            """,
            (date_str, calories, protein_g, carbs_g, fat_g, int(is_complete), source, _now_iso()),
        )
        await db.commit()


async def get_daily_nutrition(date_str: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM daily_nutrition WHERE date = ?", (date_str,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_nutrition_range(start: str, end: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM daily_nutrition WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# workouts
# ─────────────────────────────────────────────────────────────────────────────


def _workout_id(date_str: str, name: str | None) -> str:
    """ID determinista para idempotencia: date + nombre normalizado."""
    safe_name = (name or "sin_nombre").lower().replace(" ", "_")
    return f"{date_str}_{safe_name}"


async def upsert_workout(
    date_str: str,
    name: str | None,
    duration_min: float | None,
    active_energy_kcal: float | None,
    source: str = "hevy_healthkit",
    raw: dict | None = None,
) -> str:
    workout_id = _workout_id(date_str, name)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workouts
                (id, date, name, duration_min, active_energy_kcal, source, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                duration_min       = COALESCE(excluded.duration_min, workouts.duration_min),
                active_energy_kcal = COALESCE(excluded.active_energy_kcal, workouts.active_energy_kcal),
                source             = excluded.source,
                raw_json           = COALESCE(excluded.raw_json, workouts.raw_json)
            """,
            (
                workout_id,
                date_str,
                name,
                duration_min,
                active_energy_kcal,
                source,
                json.dumps(raw) if raw else None,
            ),
        )
        await db.commit()
    return workout_id


async def get_last_workout(name: str | None = None) -> dict[str, Any] | None:
    """Devuelve el entreno más reciente, opcionalmente filtrado por nombre."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        if name:
            safe_name = name.lower().replace(" ", "_")
            async with db.execute(
                "SELECT * FROM workouts WHERE id LIKE ? ORDER BY date DESC LIMIT 1",
                (f"%_{safe_name}",),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT * FROM workouts ORDER BY date DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None


async def get_workouts_range(start: str, end: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM workouts WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_previous_same_workout(current_id: str, name: str) -> dict[str, Any] | None:
    """Obtiene el entreno anterior del mismo tipo (excluyendo el actual)."""
    safe_name = name.lower().replace(" ", "_")
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM workouts
            WHERE id LIKE ? AND id != ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (f"%_{safe_name}", current_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# body_weight
# ─────────────────────────────────────────────────────────────────────────────


async def upsert_body_weight(date_str: str, weight_kg: float) -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO body_weight (date, weight_kg)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET weight_kg = excluded.weight_kg
            """,
            (date_str, weight_kg),
        )
        await db.commit()


async def get_body_weight(date_str: str) -> float | None:
    async with aiosqlite.connect(settings.database_path) as db:
        async with db.execute(
            "SELECT weight_kg FROM body_weight WHERE date = ?", (date_str,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_weight_range(start: str, end: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM body_weight WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# user_targets
# ─────────────────────────────────────────────────────────────────────────────


async def get_targets() -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_targets WHERE id = 1") as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_targets(**fields: Any) -> None:
    """Actualiza solo los campos proporcionados en user_targets."""
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            f"UPDATE user_targets SET {set_clause} WHERE id = 1", values
        )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# reminders_log
# ─────────────────────────────────────────────────────────────────────────────


async def was_reminder_sent(date_str: str, reminder_type: str) -> bool:
    async with aiosqlite.connect(settings.database_path) as db:
        async with db.execute(
            "SELECT 1 FROM reminders_log WHERE date = ? AND reminder_type = ?",
            (date_str, reminder_type),
        ) as cur:
            return (await cur.fetchone()) is not None


async def mark_reminder_sent(date_str: str, reminder_type: str) -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO reminders_log (date, reminder_type, sent_at)
            VALUES (?, ?, ?)
            """,
            (date_str, reminder_type, _now_iso()),
        )
        await db.commit()