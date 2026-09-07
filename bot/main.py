"""
main.py — Punto de entrada principal.
Arranca FastAPI (webhook), el bot de Telegram (polling) y APScheduler
en un único proceso async compartiendo el mismo event loop.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn
from telegram.ext import Application

from bot.config import settings
from bot.db import init_db
from bot.scheduler_jobs import create_scheduler, restore_scheduled_reminders, set_bot_app
from bot.telegram_handlers import register_handlers
from bot.webhook import app as fastapi_app, set_telegram_app

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    # 1. Inicializar base de datos
    logger.info("Inicializando base de datos…")
    await init_db()

    # 2. Construir bot de Telegram
    logger.info("Construyendo aplicación de Telegram…")
    telegram_app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    register_handlers(telegram_app)

    # 3. Inyectar referencia del bot en webhook y scheduler
    set_telegram_app(telegram_app)
    set_bot_app(telegram_app)

    # 4. Configurar y arrancar scheduler
    scheduler = create_scheduler()
    await restore_scheduled_reminders(scheduler)
    scheduler.start()
    logger.info("Scheduler APScheduler iniciado.")

    # 5. Configurar uvicorn (FastAPI)
    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host="0.0.0.0",
        port=settings.webhook_port,
        log_level="warning",  # Evitar duplicar logs con el nuestro
        access_log=False,
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    # 6. Arrancar todo en paralelo
    logger.info("Arrancando servidor webhook en puerto %d…", settings.webhook_port)
    logger.info("Iniciando polling de Telegram…")

    async with telegram_app:
        await telegram_app.initialize()
        # Registrar comandos en el menú nativo de Telegram
        try:
            from telegram import BotCommand
            commands = [
                BotCommand("hoy", "Resumen de hoy (macros, racha, peso)"),
                BotCommand("semana", "Informe semanal y adherencia"),
                BotCommand("racha", "Racha de proteína y récord"),
                BotCommand("volumen", "Series semanales por grupo muscular"),
                BotCommand("records", "Récords personales y 1RM estimado"),
                BotCommand("grafica", "Gráfico visual de peso y macros"),
                BotCommand("quecomo", "Ideas de comida según macros restantes"),
                BotCommand("entreno", "Último entreno y FC Polar H10"),
                BotCommand("objetivo", "Ver o editar metas calóricas/proteína"),
                BotCommand("consejo", "Asesoramiento del entrenador IA"),
                BotCommand("recuerdame", "Programar recordatorio (ej. 10:00 Creatina)"),
                BotCommand("ayuda", "Guía y manual completo del bot"),
            ]
            await telegram_app.bot.set_my_commands(commands)
            logger.info("Menú de comandos de Telegram configurado.")
        except Exception as exc:
            logger.warning("No se pudieron registrar los comandos en Telegram: %s", exc)

        # Notificación de inicio
        try:
            await telegram_app.bot.send_message(
                chat_id=settings.telegram_chat_id,
                text="🚀 <b>Bot iniciado correctamente.</b>\nEscribe /ayuda para ver los comandos.",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("No se pudo enviar mensaje de inicio: %s", exc)

        try:
            await asyncio.gather(
                uvicorn_server.serve(),
                telegram_app.updater.start_polling(drop_pending_updates=True),
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Señal de parada recibida.")
        finally:
            logger.info("Deteniendo servicios…")
            scheduler.shutdown(wait=False)
            await telegram_app.updater.stop()
            await telegram_app.stop()
            logger.info("Bot detenido limpiamente.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bye!")
