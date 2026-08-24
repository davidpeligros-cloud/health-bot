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
        await telegram_app.start()

        # Notificación de inicio
        try:
            await telegram_app.bot.send_message(
                chat_id=settings.telegram_chat_id,
                text="🚀 <b>Bot iniciado correctamente.</b>\nEscribe /ping para verificar.",
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
