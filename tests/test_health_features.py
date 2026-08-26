import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot.hevy_parser import format_hevy_summary, is_hevy_workout_text, parse_hevy_text
from bot import db
from bot.logic import compare_workout, get_protein_streak, get_season_summary
from bot.webhook import normalize_payload_dict, parse_float


class HealthFeatureTests(unittest.TestCase):
    def test_hevy_parses_the_export_format_with_multiple_inline_sets(self):
        parsed = parse_hevy_text(
            "Pecho, Hombros y Tríceps lunes, ago 24, 2026 a las 9:56am\n"
            "Press de Banca Inclinado (Mancuerna) Serie 1: 20 kg x 15 [Calentamiento] "
            "Serie 2: 30 kg x 12 Serie 3: 35 kg x 12 Serie 4: 35 kg x 9 Serie 5: 35 kg x 8\n"
            "Press de Banca (Mancuerna) Serie 1: 40 kg x 8\n"
            "Aperturas (Máquina) Serie 1: 32 kg x 13 Serie 2: 32 kg x 13 Serie 3: 32 kg x 10\n"
            "Press de Hombros (Mancuerna) Serie 1: 18 kg x 12 Serie 2: 18 kg x 10 Serie 3: 18 kg x 10\n"
            "Elevacion Laterales (Mancuerna) Serie 1: 12 kg x 14 Serie 2: 12 kg x 12 Serie 3: 12 kg x 12\n"
            "Extensión de tríceps en polea Serie 1: 18 kg x 15 Serie 2: 18 kg x 13 Serie 3: 18 kg x 10\n"
            "Curl con Barra EZ Serie 1: 20 kg x 12 Serie 2: 20 kg x 12 Serie 3: 20 kg x 10"
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["workout_name"], "Pecho, Hombros y Tríceps")
        self.assertEqual(parsed["date"], "2026-08-24")
        self.assertEqual(parsed["total_sets"], 21)
        self.assertEqual(parsed["exercises"][0]["name"], "Press de Banca Inclinado (Mancuerna)")
        self.assertEqual(len(parsed["exercises"]), 7)
        self.assertEqual(parsed["exercises"][0]["sets"][-1]["weight_kg"], 35.0)
        self.assertEqual(parsed["total_volume_kg"], 5543.0)

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

    def test_season_summary_calculates_real_monthly_metrics(self):
        workouts = [{"id": "one"}, {"id": "two"}]
        nutrition = [
            {"date": "2026-08-01", "protein_g": 170},
            {"date": "2026-08-02", "protein_g": 150},
        ]
        current_sets = [
            {"date": "2026-08-01", "exercise_name": "Press banca", "weight_kg": 82.5, "reps": 5},
            {"date": "2026-08-01", "exercise_name": "Remo", "weight_kg": 60.0, "reps": 12},
        ]
        all_sets = [
            {"date": "2026-07-20", "exercise_name": "Press banca", "weight_kg": 80.0, "reps": 5},
            *current_sets,
        ]

        async def run_summary():
            logic_globals = get_season_summary.__globals__
            with patch.object(logic_globals["db"], "get_workouts_range", new=AsyncMock(return_value=workouts)), \
                 patch.object(logic_globals["db"], "get_nutrition_range", new=AsyncMock(return_value=nutrition)), \
                 patch.object(logic_globals["db"], "get_exercise_sets_range", new=AsyncMock(return_value=current_sets)), \
                 patch.object(logic_globals["db"], "get_all_exercise_sets", new=AsyncMock(return_value=all_sets)), \
                 patch.object(logic_globals["db"], "get_targets", new=AsyncMock(return_value={"protein_target_g": 160})):
                return await get_season_summary("2026-08")

        result = asyncio.run(run_summary())
        self.assertEqual(result["num_workouts"], 2)
        self.assertEqual(result["protein_days"], 1)
        self.assertEqual(result["total_volume_kg"], 1132.5)
        self.assertEqual(result["personal_records"], 1)
        self.assertEqual(result["star_exercise"], "Remo")

    def test_muscle_groups_maps_exercises_correctly(self):
        from bot.muscle_groups import get_muscle_groups_for_exercise
        
        # Test exact matches
        self.assertEqual(set(get_muscle_groups_for_exercise("Press de Banca")), {"pecho", "hombros", "brazos"})
        self.assertEqual(set(get_muscle_groups_for_exercise("Remo")), {"espalda", "brazos"})
        self.assertEqual(set(get_muscle_groups_for_exercise("Peso Muerto")), {"espalda", "glúteos", "isquiotibiales", "core"})
        
        # Test case insensitivity
        self.assertEqual(set(get_muscle_groups_for_exercise("press de banca")), {"pecho", "hombros", "brazos"})
        self.assertEqual(set(get_muscle_groups_for_exercise("PRESS DE BANCA")), {"pecho", "hombros", "brazos"})
        
        # Test partial match fallback
        muscles = get_muscle_groups_for_exercise("Curl barra")
        self.assertIn("brazos", muscles)
        
        # Test unknown exercise defaults to core
        unknown_muscles = get_muscle_groups_for_exercise("Ejercicio desconocido")
        self.assertEqual(unknown_muscles, ["core"])

    def test_muscle_fatigue_aggregation(self):
        from bot.muscle_groups import aggregate_muscle_fatigue
        
        sets = [
            {"exercise_name": "Press de Banca", "weight_kg": 80.0, "reps": 10, "date": "2026-08-24"},
            {"exercise_name": "Press de Banca", "weight_kg": 80.0, "reps": 8, "date": "2026-08-24"},
            {"exercise_name": "Remo", "weight_kg": 60.0, "reps": 12, "date": "2026-08-24"},
        ]
        
        fatigue = aggregate_muscle_fatigue(sets, "2026-08-24")
        
        # Pecho worked 2 sets with volume 80*10 + 80*8 = 1440
        self.assertEqual(fatigue["pecho"]["sets"], 2)
        self.assertEqual(fatigue["pecho"]["volumen_kg"], 1440.0)
        self.assertEqual(fatigue["pecho"]["reps"], 18)
        
        # Espalda worked 1 set with volume 60*12 = 720
        self.assertEqual(fatigue["espalda"]["sets"], 1)
        self.assertEqual(fatigue["espalda"]["volumen_kg"], 720.0)

    def test_recovery_status_determination(self):
        from bot.muscle_groups import recovery_status
        
        # Very fatigued (< 24 hrs)
        emoji, status = recovery_status(12.0)
        self.assertEqual(emoji, "🔴")
        self.assertIn("Muy", status)
        
        # Fatigued (24-48 hrs)
        emoji, status = recovery_status(36.0)
        self.assertEqual(emoji, "🟠")
        self.assertIn("Fatigado", status)
        
        # Recovering (48-72 hrs)
        emoji, status = recovery_status(60.0)
        self.assertEqual(emoji, "🟡")
        self.assertIn("recuperación", status)
        
        # Recovered (72+ hrs)
        emoji, status = recovery_status(96.0)
        self.assertEqual(emoji, "🟢")
        self.assertIn("Recuperado", status)

    def test_muscle_fatigue_map_async(self):
        from bot.logic import get_muscle_fatigue_map
        
        async def run_fatigue():
            sets = [
                {"exercise_name": "Press de Banca", "weight_kg": 80.0, "reps": 10, "date": "2026-08-24"},
                {"exercise_name": "Remo", "weight_kg": 60.0, "reps": 12, "date": "2026-08-23"},
            ]
            logic_globals = get_muscle_fatigue_map.__globals__
            with patch.object(logic_globals["db"], "get_exercise_sets_range", new=AsyncMock(return_value=sets)):
                return await get_muscle_fatigue_map(days=7)
        
        result = asyncio.run(run_fatigue())
        self.assertTrue(result["has_data"])
        self.assertIn("muscles", result)
        self.assertTrue(len(result["muscles"]) > 0)

    def test_exercise_library_insights(self):
        from bot.logic import get_exercise_library_insights
        
        async def run_insights():
            exercises = ["Press de Banca", "Press de Banca", "Remo", "Curl barra"]
            return await get_exercise_library_insights(exercises)
        
        result = asyncio.run(run_insights())
        self.assertEqual(result["ejercicios_analizados"], 4)
        # Las claves tienen formato "emoji grupo" (e.g., "💪 brazos")
        self.assertTrue(any("pecho" in k for k in result["frecuencia_por_grupo"] or {}))


if __name__ == "__main__":
    unittest.main()
