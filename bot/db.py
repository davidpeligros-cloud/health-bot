"""
db.py — Acceso asíncrono a SQLite con aiosqlite.
Incluye inicialización del esquema y todas las funciones CRUD.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
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
    avg_hr_bpm         REAL,
    max_hr_bpm         REAL,
    source             TEXT DEFAULT 'hevy_healthkit',
    raw_json           TEXT
);

CREATE TABLE IF NOT EXISTS workout_exercises (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id    TEXT NOT NULL,
    date          TEXT NOT NULL,
    exercise_name TEXT NOT NULL,
    set_number    INTEGER NOT NULL,
    weight_kg     REAL,
    is_bodyweight INTEGER NOT NULL DEFAULT 0,
    added_weight_kg REAL,
    reps          INTEGER,
    rpe           REAL,
    FOREIGN KEY(workout_id) REFERENCES workouts(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS scheduled_reminders (
    id         TEXT PRIMARY KEY,
    run_at     TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db() -> None:
    """Crea el directorio de datos, las tablas y aplica migraciones si no existen."""
    db_path = settings.database_path
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()

        # Migraciones dinámicas seguras para tablas existentes
        async with db.execute("PRAGMA table_info(workouts)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
            if "avg_hr_bpm" not in columns:
                await db.execute("ALTER TABLE workouts ADD COLUMN avg_hr_bpm REAL")
                logger.info("Migración aplicada: columna avg_hr_bpm añadida a workouts")
            if "max_hr_bpm" not in columns:
                await db.execute("ALTER TABLE workouts ADD COLUMN max_hr_bpm REAL")
                logger.info("Migración aplicada: columna max_hr_bpm añadida a workouts")

        async with db.execute("PRAGMA table_info(workout_exercises)") as cur:
            exercise_columns = [row[1] for row in await cur.fetchall()]
            if "is_bodyweight" not in exercise_columns:
                await db.execute(
                    "ALTER TABLE workout_exercises ADD COLUMN is_bodyweight INTEGER NOT NULL DEFAULT 0"
                )
            if "added_weight_kg" not in exercise_columns:
                await db.execute(
                    "ALTER TABLE workout_exercises ADD COLUMN added_weight_kg REAL"
                )

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


async def backup_database() -> Path:
    """Crea una copia consistente de SQLite y elimina copias antiguas."""
    source_path = Path(settings.database_path)
    backup_dir = Path(settings.backup_dir)

    def create_backup() -> Path:
        if not source_path.exists():
            raise FileNotFoundError(f"No existe la base de datos: {source_path}")

        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"health_bot_{timestamp}.db"
        with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)

        backups = sorted(backup_dir.glob("health_bot_*.db"), reverse=True)
        for old_backup in backups[settings.backup_retention_days:]:
            old_backup.unlink(missing_ok=True)
        return backup_path

    return await asyncio.to_thread(create_backup)


# ─────────────────────────────────────────────────────────────────────────────
# chat_history (Memoria para el asistente IA)
# ─────────────────────────────────────────────────────────────────────────────


async def add_chat_message(role: str, content: str) -> None:
    """Guarda un mensaje en el historial del chat (user o assistant)."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "INSERT INTO chat_history (role, content) VALUES (?, ?)",
            (role, content),
        )
        await db.commit()


async def get_recent_chat_history(limit: int = 10) -> list[dict[str, str]]:
    """Devuelve los últimos N mensajes para pasarlos como contexto a Claude."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content FROM chat_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            messages = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
            return messages


async def clear_chat_history() -> None:
    """Borra el historial de la conversación con el bot."""
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("DELETE FROM chat_history")
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# daily_nutrition
# ─────────────────────────────────────────────────────────────────────────────


async def upsert_daily_nutrition(
    date_str: str,
    calories: float | None = None,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    source: str = "healthkit",
) -> None:
    now = _now_iso()
    is_complete = int(all(x is not None for x in (calories, protein_g, carbs_g, fat_g)))
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
            (date_str, calories, protein_g, carbs_g, fat_g, is_complete, source, now),
        )
        await db.commit()


async def add_to_daily_nutrition(
    date_str: str,
    calories: float = 0.0,
    protein_g: float = 0.0,
    carbs_g: float = 0.0,
    fat_g: float = 0.0,
    source: str = "quick_nlp",
) -> dict[str, float]:
    """Suma calorías y macronutrientes al registro del día especificado y devuelve el total acumulado."""
    existing = await get_daily_nutrition(date_str)
    new_cal = round(((existing.get("calories") or 0.0) if existing else 0.0) + calories, 1)
    new_prot = round(((existing.get("protein_g") or 0.0) if existing else 0.0) + protein_g, 1)
    new_carbs = round(((existing.get("carbs_g") or 0.0) if existing else 0.0) + carbs_g, 1)
    new_fat = round(((existing.get("fat_g") or 0.0) if existing else 0.0) + fat_g, 1)

    await upsert_daily_nutrition(
        date_str=date_str,
        calories=new_cal,
        protein_g=new_prot,
        carbs_g=new_carbs,
        fat_g=new_fat,
        source=source,
    )
    return {
        "calories": new_cal,
        "protein_g": new_prot,
        "carbs_g": new_carbs,
        "fat_g": new_fat,
    }


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


async def get_all_daily_nutrition(limit: int | None = 365) -> list[dict[str, Any]]:
    """Devuelve los registros diarios ordenados por fecha descendente."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM daily_nutrition ORDER BY date DESC"
        params: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        async with db.execute(query, params) as cur:
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
    avg_hr_bpm: float | None = None,
    max_hr_bpm: float | None = None,
    source: str = "hevy_healthkit",
    raw: dict | None = None,
) -> str:
    workout_id = _workout_id(date_str, name)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workouts
                (id, date, name, duration_min, active_energy_kcal, avg_hr_bpm, max_hr_bpm, source, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                duration_min       = COALESCE(excluded.duration_min, workouts.duration_min),
                active_energy_kcal = COALESCE(excluded.active_energy_kcal, workouts.active_energy_kcal),
                avg_hr_bpm         = COALESCE(excluded.avg_hr_bpm, workouts.avg_hr_bpm),
                max_hr_bpm         = COALESCE(excluded.max_hr_bpm, workouts.max_hr_bpm),
                source             = excluded.source,
                raw_json           = COALESCE(excluded.raw_json, workouts.raw_json)
            """,
            (
                workout_id,
                date_str,
                name,
                duration_min,
                active_energy_kcal,
                avg_hr_bpm,
                max_hr_bpm,
                source,
                json.dumps(raw, ensure_ascii=False) if raw else None,
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
# workout_exercises (Series y ejercicios de Hevy)
# ─────────────────────────────────────────────────────────────────────────────


async def save_workout_exercises(workout_id: str, date_str: str, exercises: list[dict[str, Any]]) -> None:
    """Guarda las series individuales de cada ejercicio de una sesión."""
    async with aiosqlite.connect(settings.database_path) as db:
        # Limpiar series previas para este workout_id si se está re-procesando
        await db.execute("DELETE FROM workout_exercises WHERE workout_id = ?", (workout_id,))

        for ex in exercises:
            ex_name = ex.get("name", "Desconocido")
            for s in ex.get("sets", []):
                await db.execute(
                    """
                    INSERT INTO workout_exercises
                        (workout_id, date, exercise_name, set_number, weight_kg,
                         is_bodyweight, added_weight_kg, reps, rpe)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workout_id,
                        date_str,
                        ex_name,
                        s.get("set_number", 1),
                        s.get("weight_kg"),
                        int(bool(s.get("is_bodyweight", False))),
                        s.get("added_weight_kg", 0.0),
                        s.get("reps"),
                        s.get("rpe"),
                    ),
                )
        await db.commit()


async def get_workout_exercises(workout_id: str) -> list[dict[str, Any]]:
    """Devuelve las series registradas para un entreno específico."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
                 SELECT exercise_name, set_number, weight_kg, is_bodyweight,
                     added_weight_kg, reps, rpe
            FROM workout_exercises
            WHERE workout_id = ?
            ORDER BY exercise_name, set_number
            """,
            (workout_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_exercise_sets_range(start: str, end: str) -> list[dict[str, Any]]:
    """Devuelve series de ejercicios registradas dentro de un periodo."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT workout_id, date, exercise_name, set_number, weight_kg,
                   is_bodyweight, added_weight_kg, reps, rpe
            FROM workout_exercises
            WHERE date BETWEEN ? AND ?
            ORDER BY date, workout_id, exercise_name, set_number
            """,
            (start, end),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_all_exercise_sets() -> list[dict[str, Any]]:
    """Devuelve todas las series para calcular récords históricos."""
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT workout_id, date, exercise_name, set_number, weight_kg,
                   is_bodyweight, added_weight_kg, reps, rpe
            FROM workout_exercises
            ORDER BY date, workout_id, exercise_name, set_number
            """
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_previous_exercise_sets(exercise_name: str, current_workout_id: str | None = None) -> list[dict[str, Any]]:
    """
    Busca la sesión más reciente anterior donde se realizó este ejercicio
    y devuelve sus series.
    """
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        # 1. Encontrar la última fecha/workout_id en la que se hizo el ejercicio
        query = """
            SELECT workout_id, date
            FROM workout_exercises
            WHERE LOWER(exercise_name) = LOWER(?)
        """
        params = [exercise_name]
        if current_workout_id:
            query += " AND workout_id != ?"
            params.append(current_workout_id)

        query += " ORDER BY date DESC, id DESC LIMIT 1"

        async with db.execute(query, params) as cur:
            last_session = await cur.fetchone()

        if not last_session:
            return []

        prev_wid = last_session["workout_id"]
        async with db.execute(
            """
            SELECT set_number, weight_kg, reps, rpe, date
            FROM workout_exercises
            WHERE workout_id = ? AND LOWER(exercise_name) = LOWER(?)
            ORDER BY set_number
            """,
            (prev_wid, exercise_name),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


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


async def add_scheduled_reminder(reminder_id: str, run_at: str, message: str) -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO scheduled_reminders (id, run_at, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (reminder_id, run_at, message, _now_iso()),
        )
        await db.commit()


async def get_scheduled_reminders() -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, run_at, message FROM scheduled_reminders ORDER BY run_at"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def delete_scheduled_reminder(reminder_id: str) -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute("DELETE FROM scheduled_reminders WHERE id = ?", (reminder_id,))
        await db.commit()
