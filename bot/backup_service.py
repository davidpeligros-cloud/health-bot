"""
backup_service.py — Servicio de exportación y copias de seguridad de datos.
Permite descargar backups en SQLite y CSV para Excel / Google Sheets.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

from bot import db
from bot.config import settings

logger = logging.getLogger(__name__)


async def generate_csv_export() -> tuple[bytes, str]:
    """
    Genera un archivo CSV en memoria con todas las métricas históricas del usuario.
    """
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")

    # 1. Nutrición diaria
    writer.writerow(["=== HISTORIAL DE NUTRICIÓN DIARIA ==="])
    writer.writerow(["Fecha", "Calorías (kcal)", "Proteína (g)", "Carbos (g)", "Grasa (g)", "Completo"])
    nutrition_rows = await db.get_all_daily_nutrition(limit=365)
    for n in nutrition_rows:
        writer.writerow([
            n.get("date"),
            n.get("calories", ""),
            n.get("protein_g", ""),
            n.get("carbs_g", ""),
            n.get("fat_g", ""),
            "Sí" if n.get("is_complete") else "No",
        ])

    writer.writerow([])
    # 2. Pesos
    writer.writerow(["=== HISTORIAL DE PESO ==="])
    writer.writerow(["Fecha", "Peso (kg)"])
    weight_rows = await db.get_weight_range("2020-01-01", "2099-12-31")
    for w in weight_rows:
        writer.writerow([w.get("date"), w.get("weight_kg", "")])

    writer.writerow([])
    # 3. Series de entrenamiento y marcas
    writer.writerow(["=== SERIES Y EJERCICIOS REGISTRADOS ==="])
    writer.writerow(["Fecha", "Entrenamiento", "Ejercicio", "Serie", "Peso (kg)", "Reps", "RPE", "Volumen (kg)"])
    sets = await db.get_all_exercise_sets()
    for s in sets:
        writer.writerow([
            s.get("date"),
            s.get("workout_name", ""),
            s.get("exercise_name", ""),
            s.get("set_number", ""),
            s.get("weight_kg", ""),
            s.get("reps", ""),
            s.get("rpe", ""),
            round((s.get("weight_kg") or 0) * (s.get("reps") or 0), 1),
        ])

    now_str = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y%m%d_%H%M")
    filename = f"health_bot_export_{now_str}.csv"
    csv_bytes = output.getvalue().encode("utf-8-sig")  # utf-8 con BOM para que Excel lo abra con tildes perfectas
    return csv_bytes, filename


def get_sqlite_backup_bytes() -> tuple[bytes, str]:
    """
    Lee el archivo SQLite actual y devuelve sus bytes y el nombre sugerido.
    """
    db_path = settings.database_path
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Base de datos no encontrada en {db_path}")

    with open(db_path, "rb") as f:
        db_bytes = f.read()

    now_str = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y%m%d_%H%M")
    filename = f"health_bot_backup_{now_str}.db"
    return db_bytes, filename
