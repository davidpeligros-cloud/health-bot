"""
telegram_handlers.py — Handlers async para todos los comandos del bot de Telegram.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot import db, logic
from bot.ai_advice import get_advice

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers de formato
# ─────────────────────────────────────────────────────────────────────────────


def _progress_bar(value: float, target: float, width: int = 10) -> str:
    """Genera una barra de progreso emoji."""
    if target <= 0:
        return "▓" * width
    pct = min(value / target, 1.0)
    filled = round(pct * width)
    return "▓" * filled + "░" * (width - filled)


def _sign(v: float) -> str:
    return "+" if v >= 0 else ""


def _fmt_diff(value: float, unit: str = "") -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}{unit}"


# ─────────────────────────────────────────────────────────────────────────────
# /ping
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🟢 Bot activo y funcionando.")


# ─────────────────────────────────────────────────────────────────────────────
# /hoy
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = await logic.get_today_summary()

    if not summary["has_data"]:
        msg = (
            f"📅 <b>Resumen de hoy</b> — {summary['date']}\n\n"
            "⚠️ Aún no hay datos de nutrición para hoy.\n"
            "Los datos llegarán cuando Atajos ejecute la automatización."
        )
        await update.message.reply_html(msg)
        return

    cal_pct = int(summary["calories_consumed"] / summary["calories_target"] * 100) if summary["calories_target"] else 0
    prot_pct = int(summary["protein_consumed"] / summary["protein_target"] * 100) if summary["protein_target"] else 0

    cal_bar = _progress_bar(summary["calories_consumed"], summary["calories_target"])
    prot_bar = _progress_bar(summary["protein_consumed"], summary["protein_target"])

    cal_remaining = summary["calories_remaining"]
    prot_remaining = summary["protein_remaining"]

    weight_line = (
        f"\n⚖️ Peso: <b>{summary['latest_weight']} kg</b>"
        if summary["latest_weight"]
        else ""
    )

    incomplete_notice = "\n⚠️ <i>Datos incompletos (faltan algunos macros)</i>" if not summary["is_complete"] else ""

    msg = (
        f"📅 <b>Resumen de hoy</b> — {summary['date']}{weight_line}\n\n"
        f"🔥 <b>Calorías</b>: {summary['calories_consumed']:.0f} / {summary['calories_target']:.0f} kcal ({cal_pct}%)\n"
        f"   {cal_bar} "
        + (f"✅ <i>objetivo cumplido</i>" if cal_remaining <= 0 else f"({cal_remaining:.0f} kcal restantes)")
        + f"\n\n"
        f"🥩 <b>Proteína</b>: {summary['protein_consumed']:.0f} / {summary['protein_target']:.0f} g ({prot_pct}%)\n"
        f"   {prot_bar} "
        + (f"✅ <i>objetivo cumplido</i>" if prot_remaining <= 0 else f"({prot_remaining:.0f} g restantes)")
        + f"\n\n"
        f"🍞 <b>Carbos</b>: {summary['carbs_consumed']:.0f} g\n"
        f"🫒 <b>Grasa</b>: {summary['fat_consumed']:.0f} g"
        + incomplete_notice
    )
    await update.message.reply_html(msg)


# ─────────────────────────────────────────────────────────────────────────────
# /semana
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    weekly = await logic.get_weekly_summary()

    weight_line = f"⚖️ Peso medio: <b>{weekly['avg_weight_kg']} kg</b>" if weekly["avg_weight_kg"] else "⚖️ Sin datos de peso esta semana"

    trend_line = ""
    if weekly["weight_trend_kg"] is not None:
        trend = weekly["weight_trend_kg"]
        emoji = "📉" if trend < 0 else ("📈" if trend > 0 else "➡️")
        trend_line = f"\n   {emoji} Tendencia: {_fmt_diff(trend, ' kg')} respecto al inicio de la semana"

    prot_line = (
        f"🥩 Adherencia proteína: <b>{weekly['protein_adherence_pct']}%</b> de días ≥ {weekly['protein_target']:.0f} g"
        if weekly["protein_adherence_pct"] is not None
        else "🥩 Sin datos de nutrición esta semana"
    )

    cal_line = (
        f"🔥 Adherencia calórica: <b>{weekly['calorie_adherence_pct']}%</b> de días en rango"
        if weekly["calorie_adherence_pct"] is not None
        else ""
    )

    workout_line = f"💪 Entrenamientos: <b>{weekly['num_workouts']}</b>"
    if weekly["workout_names"]:
        workout_line += f" ({', '.join(weekly['workout_names'])})"

    kcal_line = (
        f"🔥 Total calorías activas quemadas: {weekly['total_active_kcal']:.0f} kcal"
        if weekly["total_active_kcal"]
        else ""
    )

    data_line = f"📊 Días con datos: {weekly['days_with_nutrition_data']} / 7"

    lines = [
        f"📆 <b>Resumen semanal</b> ({weekly['period_start']} → {weekly['period_end']})\n",
        weight_line + trend_line,
        prot_line,
    ]
    if cal_line:
        lines.append(cal_line)
    lines.append(workout_line)
    if kcal_line:
        lines.append(kcal_line)
    lines.append(data_line)

    await update.message.reply_html("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /objetivo
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_objetivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sin argumentos: muestra objetivos actuales.
    Con argumentos: edita campos.
    Uso: /objetivo cal 1700 prot 160 carbs 180 gras 55
    """
    targets = await db.get_targets()

    args = context.args or []

    if not args:
        # Mostrar objetivos
        if not targets:
            await update.message.reply_text("⚠️ No hay objetivos configurados.")
            return
        msg = (
            "🎯 <b>Objetivos actuales</b>\n\n"
            f"🔥 Calorías: <b>{targets.get('calorie_target', '—'):.0f} kcal</b>\n"
            f"🥩 Proteína: <b>{targets.get('protein_target_g', '—'):.0f} g</b>\n"
        )
        if targets.get("carbs_target_g"):
            msg += f"🍞 Carbos: <b>{targets['carbs_target_g']:.0f} g</b>\n"
        if targets.get("fat_target_g"):
            msg += f"🫒 Grasa: <b>{targets['fat_target_g']:.0f} g</b>\n"
        if targets.get("tdee_estimate"):
            msg += f"📊 TDEE estimado: <b>{targets['tdee_estimate']:.0f} kcal</b>\n"
        msg += "\n<i>Para editar: /objetivo cal 1700 prot 160 carbs 180 gras 55</i>"
        await update.message.reply_html(msg)
        return

    # Parsear argumentos clave-valor
    allowed_keys = {
        "cal": "calorie_target",
        "prot": "protein_target_g",
        "carbs": "carbs_target_g",
        "gras": "fat_target_g",
        "tdee": "tdee_estimate",
    }
    updates: dict[str, float] = {}
    i = 0
    errors = []
    while i < len(args) - 1:
        key = args[i].lower()
        val_str = args[i + 1]
        if key in allowed_keys:
            try:
                updates[allowed_keys[key]] = float(val_str)
                i += 2
            except ValueError:
                errors.append(f"Valor inválido para '{key}': {val_str}")
                i += 2
        else:
            errors.append(f"Clave desconocida: '{key}'")
            i += 1

    if not updates:
        await update.message.reply_text(
            "⚠️ No entendí los argumentos.\nUso: /objetivo cal 1700 prot 160 carbs 180 gras 55"
        )
        return

    await db.update_targets(**updates)

    confirm = "✅ <b>Objetivos actualizados:</b>\n"
    for db_key, value in updates.items():
        labels = {v: k for k, v in allowed_keys.items()}
        confirm += f"  • {labels.get(db_key, db_key)}: {value:.0f}\n"
    if errors:
        confirm += "\n⚠️ " + "; ".join(errors)

    await update.message.reply_html(confirm)


# ─────────────────────────────────────────────────────────────────────────────
# /entreno
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_entreno(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    last = await db.get_last_workout()
    if not last:
        await update.message.reply_text("💪 No hay entrenos registrados todavía.")
        return

    lines = [f"💪 <b>Último entreno</b> — {last['date']}"]
    if last.get("name"):
        lines.append(f"🏷 <b>{last['name']}</b>")
    if last.get("duration_min"):
        lines.append(f"⏱ Duración: {last['duration_min']:.0f} min")
    if last.get("active_energy_kcal"):
        lines.append(f"🔥 Activas: {last['active_energy_kcal']:.0f} kcal")

    # Comparación
    if last.get("name"):
        comparison = await logic.compare_workout(last["id"], last["name"])
        prev = comparison.get("previous")
        diff = comparison.get("diff")
        if prev and diff:
            lines.append(f"\n📊 <b>Vs. sesión anterior ({prev['date']}):</b>")
            if diff.get("duration_min") is not None:
                lines.append(f"  ⏱ Duración: {_fmt_diff(diff['duration_min'], ' min')}")
            if diff.get("active_energy_kcal") is not None:
                lines.append(f"  🔥 Activas: {_fmt_diff(diff['active_energy_kcal'], ' kcal')}")
        elif not prev:
            lines.append(f"\n📊 Primera sesión de <b>{last['name']}</b> registrada.")

    await update.message.reply_html("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /consejo
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_consejo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /consejo permitiendo preguntas o usando el prompt por defecto."""
    user_prompt = (
        " ".join(context.args)
        if context.args
        else "Analiza mi progreso reciente y dame una recomendación concreta."
    )

    await update.message.reply_text("🤔 Analizando tus datos y nuestra conversación… un momento.")
    advice = await get_advice(user_prompt=user_prompt, context_days=7)
    await update.message.reply_html(f"🧠 <b>Consejo personalizado:</b>\n\n{advice}")


# ─────────────────────────────────────────────────────────────────────────────
# /olvidar
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Borra el historial de la conversación con la IA."""
    await db.clear_chat_history()
    await update.message.reply_text("🧹 Memoria de la conversación borrada correctamente.")


# ─────────────────────────────────────────────────────────────────────────────
# Registro de handlers
# ─────────────────────────────────────────────────────────────────────────────


def register_handlers(application: Application) -> None:
    """Registra todos los command handlers en la aplicación del bot."""
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("hoy", cmd_hoy))
    application.add_handler(CommandHandler("semana", cmd_semana))
    application.add_handler(CommandHandler("objetivo", cmd_objetivo))
    application.add_handler(CommandHandler("entreno", cmd_entreno))
    application.add_handler(CommandHandler("consejo", cmd_consejo))
    application.add_handler(CommandHandler("olvidar", cmd_olvidar))
    logger.info("Handlers de Telegram registrados.")