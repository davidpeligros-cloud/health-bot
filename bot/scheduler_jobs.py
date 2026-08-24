"""
scheduler_jobs.py — Jobs programados con APScheduler.
Resumen matutino, aviso nocturno, resumen semanal y recordatorios dinámicos.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from bot import db
from bot.config import settings

logger = logging.getLogger(__name__)

_bot_app = None  # Referencia inyectada desde main.py
_global_scheduler = None  # Referencia al scheduler activo para recordatorios dinámicos


def set_bot_app(app) -> None:
    global _bot_app
    _bot_app = app


async def _send(text: str) -> bool:
    if _bot_app is None:
        logger.warning("Bot no disponible para scheduler.")
        return False
    try:
        await _bot_app.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            parse_mode="HTML",
        )
        return True
    except Exception as exc:
        logger.error("Scheduler no pudo enviar mensaje: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Job: Recordatorio personalizado dinámico
# ─────────────────────────────────────────────────────────────────────────────


async def send_reminder_job(text: str) -> None:
    """Envía un recordatorio único programado por el usuario."""
    await _send(f"⏰ <b>¡Recordatorio!</b>\n\n{text}")
    logger.info("Recordatorio dinámico enviado: %s", text)


async def schedule_custom_reminder(run_date: datetime, text: str) -> str:
    """Persiste y programa un recordatorio único."""
    global _global_scheduler
    reminder_id = f"reminder_{uuid4().hex}"
    await db.add_scheduled_reminder(reminder_id, run_date.isoformat(), text)
    if _global_scheduler and _global_scheduler.running:
        _global_scheduler.add_job(
            send_persisted_reminder_job,
            trigger=DateTrigger(run_date=run_date),
            args=[reminder_id, text],
            id=reminder_id,
            replace_existing=True,
        )
        logger.info("Recordatorio programado con éxito para: %s", run_date)
    else:
        logger.warning("Recordatorio guardado; se programará al arrancar el scheduler.")
    return reminder_id


async def send_persisted_reminder_job(reminder_id: str, text: str) -> None:
    """Envía un recordatorio persistido y lo elimina tras el envío."""
    sent = await _send(f"⏰ <b>¡Recordatorio!</b>\n\n{text}")
    if sent:
        await db.delete_scheduled_reminder(reminder_id)
        logger.info("Recordatorio dinámico enviado: %s", text)
        return

    if _global_scheduler and _global_scheduler.running:
        retry_at = datetime.now(ZoneInfo("Europe/Madrid")) + timedelta(minutes=5)
        _global_scheduler.add_job(
            send_persisted_reminder_job,
            trigger=DateTrigger(run_date=retry_at),
            args=[reminder_id, text],
            id=reminder_id,
            replace_existing=True,
        )
        logger.warning("Recordatorio %s reintentará enviarse a las %s", reminder_id, retry_at)


async def restore_scheduled_reminders(scheduler: AsyncIOScheduler) -> None:
    """Restaura recordatorios guardados tras un reinicio del proceso."""
    now = datetime.now(ZoneInfo("Europe/Madrid"))
    for reminder in await db.get_scheduled_reminders():
        run_date = datetime.fromisoformat(reminder["run_at"])
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=ZoneInfo("Europe/Madrid"))
        if run_date <= now:
            run_date = now + timedelta(seconds=1)
        scheduler.add_job(
            send_persisted_reminder_job,
            trigger=DateTrigger(run_date=run_date),
            args=[reminder["id"], reminder["message"]],
            id=reminder["id"],
            replace_existing=True,
        )
    logger.info("Recordatorios persistidos restaurados.")


# ─────────────────────────────────────────────────────────────────────────────
# Job: Resumen matutino
# ─────────────────────────────────────────────────────────────────────────────


async def job_morning_summary() -> None:
    today = date.today().isoformat()

    if await db.was_reminder_sent(today, "morning"):
        return

    from bot.logic import get_today_summary
    summary = await get_today_summary()
    targets = await db.get_targets()

    cal_target = (targets or {}).get("calorie_target", settings.default_calorie_target)
    prot_target = (targets or {}).get("protein_target_g", 160)

    weight_line = (
        f"\n⚖️ Último peso registrado: <b>{summary['latest_weight']} kg</b>"
        if summary["latest_weight"]
        else ""
    )

    msg = (
        f"☀️ <b>¡Buenos días!</b> Objetivo de hoy:\n"
        f"{weight_line}\n\n"
        f"🔥 Calorías objetivo: <b>{cal_target:.0f} kcal</b>\n"
        f"🥩 Proteína objetivo: <b>{prot_target:.0f} g</b>\n\n"
        f"<i>Usa /hoy para ver tu progreso en cualquier momento.</i>"
    )

    await _send(msg)
    await db.mark_reminder_sent(today, "morning")
    logger.info("Resumen matutino enviado para %s", today)


# ─────────────────────────────────────────────────────────────────────────────
# Job: Aviso nocturno
# ─────────────────────────────────────────────────────────────────────────────


async def job_night_check() -> None:
    today = date.today().isoformat()

    if await db.was_reminder_sent(today, "night"):
        return

    nutrition = await db.get_daily_nutrition(today)

    if nutrition is None:
        msg = (
            f"🌙 <b>Aviso nocturno</b>\n\n"
            f"⚠️ No se han recibido datos de nutrición de hoy ({today}).\n\n"
            f"Si ya registraste tu comida, abre Atajos y ejecuta manualmente "
            f"la automatización de envío, o espera a que se ejecute sola."
        )
        await _send(msg)
        await db.mark_reminder_sent(today, "night")
        logger.info("Aviso nocturno (sin datos) enviado para %s", today)
    else:
        from bot.logic import get_today_summary
        summary = await get_today_summary()
        cal_remaining = summary["calories_remaining"]
        prot_remaining = summary["protein_remaining"]

        cal_status = "✅" if cal_remaining <= 0 else f"⚠️ Faltan {cal_remaining:.0f} kcal"
        prot_status = "✅" if prot_remaining <= 0 else f"⚠️ Faltan {prot_remaining:.0f} g"

        msg = (
            f"🌙 <b>Resumen del día</b> — {today}\n\n"
            f"🔥 Calorías: {summary['calories_consumed']:.0f} / {summary['calories_target']:.0f} kcal — {cal_status}\n"
            f"🥩 Proteína: {summary['protein_consumed']:.0f} / {summary['protein_target']:.0f} g — {prot_status}"
        )
        await _send(msg)
        await db.mark_reminder_sent(today, "night")
        logger.info("Resumen nocturno enviado para %s", today)


# ─────────────────────────────────────────────────────────────────────────────
# Job: Resumen semanal
# ─────────────────────────────────────────────────────────────────────────────


async def job_weekly_summary() -> None:
    today = date.today().isoformat()

    if await db.was_reminder_sent(today, "weekly"):
        return

    from bot.logic import get_weekly_summary
    from bot.ai_advice import get_advice

    weekly = await get_weekly_summary()

    weight_line = f"⚖️ Peso medio: <b>{weekly['avg_weight_kg']} kg</b>" if weekly["avg_weight_kg"] else "⚖️ Sin datos de peso"
    prot_line = (
        f"🥩 Adherencia proteína: <b>{weekly['protein_adherence_pct']}%</b>"
        if weekly["protein_adherence_pct"] is not None
        else "🥩 Sin datos de nutrición"
    )
    workout_line = f"💪 Entrenamientos: <b>{weekly['num_workouts']}</b>"

    trend_line = ""
    if weekly.get("weight_trend_kg") is not None:
        trend = weekly["weight_trend_kg"]
        emoji = "📉" if trend < 0 else ("📈" if trend > 0 else "➡️")
        trend_line = f"\n   {emoji} Tendencia de peso: {'+' if trend >= 0 else ''}{trend:.2f} kg"

    summary_text = (
        f"📆 <b>Resumen semanal</b>\n\n"
        f"{weight_line}{trend_line}\n"
        f"{prot_line}\n"
        f"{workout_line}\n\n"
        f"<i>Obteniendo consejo de IA…</i>"
    )
    await _send(summary_text)

    advice = await get_advice(context_days=7)
    await _send(f"🧠 <b>Análisis semanal:</b>\n\n{advice}")

    await db.mark_reminder_sent(today, "weekly")
    logger.info("Resumen semanal enviado para %s", today)


async def job_database_backup() -> None:
    """Genera la copia diaria sin bloquear el event loop del bot."""
    try:
        backup_path = await db.backup_database()
        logger.info("Copia de seguridad creada: %s", backup_path)
    except Exception:
        logger.exception("No se pudo crear la copia de seguridad")


# ─────────────────────────────────────────────────────────────────────────────
# Configuración del scheduler
# ─────────────────────────────────────────────────────────────────────────────


def create_scheduler() -> AsyncIOScheduler:
    global _global_scheduler
    scheduler = AsyncIOScheduler(timezone="Europe/Madrid")
    _global_scheduler = scheduler  # Guardamos la instancia globalmente

    morning_h, morning_m = settings.get_morning_hour_minute()
    night_h, night_m = settings.get_night_hour_minute()
    backup_h, backup_m = settings.get_backup_hour_minute()
    weekly_day = settings.weekly_summary_day

    scheduler.add_job(
        job_morning_summary,
        trigger=CronTrigger(hour=morning_h, minute=morning_m),
        id="morning_summary",
        replace_existing=True,
    )

    scheduler.add_job(
        job_night_check,
        trigger=CronTrigger(hour=night_h, minute=night_m),
        id="night_check",
        replace_existing=True,
    )

    scheduler.add_job(
        job_weekly_summary,
        trigger=CronTrigger(day_of_week=weekly_day, hour=21, minute=0),
        id="weekly_summary",
        replace_existing=True,
    )

    scheduler.add_job(
        job_database_backup,
        trigger=CronTrigger(hour=backup_h, minute=backup_m),
        id="database_backup",
        replace_existing=True,
    )

    logger.info(
        "Scheduler configurado: mañana %02d:%02d, noche %02d:%02d, backup %02d:%02d, semanal día %d a las 21:00",
        morning_h, morning_m, night_h, night_m, backup_h, backup_m, weekly_day,
    )
    return scheduler