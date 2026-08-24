import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot.hevy_parser import format_hevy_summary, is_hevy_workout_text, parse_hevy_text
from bot import db
from bot.logic import compare_workout, get_protein_streak
from bot.webhook import normalize_payload_dict, parse_float


class HealthFeatureTests(unittest.TestCase):
    def test_hevy_parses_bodyweight_and_added_load(self):
        self.assertTrue(is_hevy_workout_text("Dominadas\nSet 1: BW x 8"))
        parsed = parse_hevy_text(
            "Dominadas\n"
            "Set 1: BW x 8\n"
            "Set 2: +10 kg x 6"
        )

        self.assertIsNotNone(parsed)
        sets = parsed["exercises"][0]["sets"]
        self.assertTrue(sets[0]["is_bodyweight"])
        self.assertEqual(sets[0]["added_weight_kg"], 0.0)
        self.assertEqual(sets[1]["added_weight_kg"], 10.0)
        self.assertIn("peso corporal +10 kg", format_hevy_summary(parsed))

    def test_webhook_normalizes_nested_structured_exercises(self):
        normalized = normalize_payload_dict({
            "Type": "WORKOUT",
            "Fecha": "24 ago 2026, 18:30",
            "Workout": {
                "Nombre": "Torso",
                "Exercises": [{
                    "Nombre": "Press banca",
                    "Series": [{"Serie": 1, "Peso": "80,5", "Repeticiones": 8, "RPE": "8,5"}],
                }],
            },
        })

        workout = normalized["workout"]
        self.assertEqual(normalized["date"], "2026-08-24")
        self.assertEqual(workout["name"], "Torso")
        self.assertEqual(workout["exercises"][0]["sets"][0]["weight_kg"], 80.5)
        self.assertEqual(workout["exercises"][0]["sets"][0]["rpe"], 8.5)

    def test_parse_float_accepts_both_number_conventions(self):
        self.assertEqual(parse_float("1.234,5"), 1234.5)
        self.assertEqual(parse_float("1,234.5"), 1234.5)
        self.assertEqual(parse_float("78,5"), 78.5)

    def test_workout_comparison_includes_maximum_heart_rate(self):
        current = {
            "id": "current",
            "avg_hr_bpm": 145,
            "max_hr_bpm": 180,
            "duration_min": 60,
            "active_energy_kcal": 400,
        }
        previous = {
            "id": "previous",
            "avg_hr_bpm": 140,
            "max_hr_bpm": 172,
            "duration_min": 55,
            "active_energy_kcal": 380,
        }

        async def run_comparison():
            with patch.object(compare_workout.__globals__["db"], "get_last_workout", new=AsyncMock(return_value=current)), \
                 patch.object(compare_workout.__globals__["db"], "get_previous_same_workout", new=AsyncMock(return_value=previous)):
                return await compare_workout("current", "Torso")

        result = asyncio.run(run_comparison())
        self.assertEqual(result["diff"]["max_hr_bpm"], 8)

    def test_protein_record_uses_full_history(self):
        nutrition = [
            {"date": "2024-01-02", "protein_g": 160},
            {"date": "2024-01-01", "protein_g": 160},
        ]

        async def run_streak():
            with patch.object(get_protein_streak.__globals__["db"], "get_targets", new=AsyncMock(return_value={"protein_target_g": 160})), \
                 patch.object(get_protein_streak.__globals__["db"], "get_all_daily_nutrition", new=AsyncMock(return_value=nutrition)) as get_history:
                result = await get_protein_streak()
                return result, get_history

        result, get_history = asyncio.run(run_streak())
        get_history.assert_awaited_once_with(limit=None)
        self.assertEqual(result["max_streak"], 2)

    def test_database_backup_is_readable_and_rotated(self):
        async def run_backup(source_path, backup_dir):
            with patch.object(db.settings, "database_path", str(source_path)), \
                 patch.object(db.settings, "backup_dir", str(backup_dir)), \
                 patch.object(db.settings, "backup_retention_days", 1):
                first = await db.backup_database()
                second = await db.backup_database()
                return first, second

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "health_bot.db"
            backup_dir = root / "backups"
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE marker (value TEXT)")
                connection.execute("INSERT INTO marker VALUES ('ok')")
                connection.commit()

            first, second = asyncio.run(run_backup(source, backup_dir))
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            connection = sqlite3.connect(second)
            try:
                self.assertEqual(connection.execute("SELECT value FROM marker").fetchone()[0], "ok")
            finally:
                connection.close()

    def test_scheduled_reminder_persists_in_sqlite(self):
        async def run_persistence(database_path):
            with patch.object(db.settings, "database_path", str(database_path)):
                await db.add_scheduled_reminder("reminder_test", "2026-08-24T18:00:00+02:00", "Beber agua")
                reminders = await db.get_scheduled_reminders()
                await db.delete_scheduled_reminder("reminder_test")
                remaining = await db.get_scheduled_reminders()
                return reminders, remaining

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "reminders.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(db.SCHEMA_SQL)
                connection.commit()
            finally:
                connection.close()
            reminders, remaining = asyncio.run(run_persistence(database_path))
            self.assertEqual(reminders[0]["message"], "Beber agua")
            self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
