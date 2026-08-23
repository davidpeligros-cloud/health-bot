"""
config.py — Carga y valida todas las variables de entorno con Pydantic Settings.
"""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str
    telegram_chat_id: int

    # ── Webhook ───────────────────────────────────────────────────────────────
    webhook_token: str
    webhook_port: int = 8000

    # ── IA (Groq) ─────────────────────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Objetivos ─────────────────────────────────────────────────────────────
    tdee_estimate: float = 2200.0
    calorie_deficit: float = 400.0
    activity_multiplier: float = 1.55

    # ── Scheduler ─────────────────────────────────────────────────────────────
    morning_summary_time: str = "08:00"
    night_check_time: str = "22:00"
    weekly_summary_day: int = 6  # 0=lunes, 6=domingo

    # ── Base de datos ─────────────────────────────────────────────────────────
    database_path: str = "./data/health_bot.db"

    @field_validator("morning_summary_time", "night_check_time", mode="before")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(f"Formato de hora inválido: '{v}'. Usa HH:MM")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Hora fuera de rango: '{v}'")
        return v

    @field_validator("weekly_summary_day", mode="before")
    @classmethod
    def validate_day(cls, v: int) -> int:
        if not (0 <= int(v) <= 6):
            raise ValueError("weekly_summary_day debe estar entre 0 (lunes) y 6 (domingo)")
        return v

    def get_morning_hour_minute(self) -> tuple[int, int]:
        h, m = self.morning_summary_time.split(":")
        return int(h), int(m)

    def get_night_hour_minute(self) -> tuple[int, int]:
        h, m = self.night_check_time.split(":")
        return int(h), int(m)

    @property
    def default_calorie_target(self) -> float:
        return self.tdee_estimate - self.calorie_deficit


# Instancia global — importar desde aquí en el resto de módulos
settings = Settings()