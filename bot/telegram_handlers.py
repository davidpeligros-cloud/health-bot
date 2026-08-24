"""
telegram_handlers.py — Handlers async para todos los comandos y mensajes de Telegram.
Incluye soporte para /racha, parseo automático de Hevy con progresión, Polar H10 y chat IA.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from bot import db, logic
from bot.ai_advice import get_advice
from bot.hevy_parser import is_hevy_workout_text, parse_hevy_text, format_hevy_summary
from bot.scheduler_jobs import schedule_custom_reminder

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
# /racha (Racha de días cumpliendo proteína)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_racha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra la racha actual y récord histórico cumpliendo el objetivo de proteína."""
    streak_data = await logic.get_protein_streak()

    current = streak_data["current_streak"]
    max_s = streak_data["max_streak"]
    target = streak_data["protein_target"]
    today_p = streak_data["today_protein"]
    today_rem = streak_data["today_remaining"]
    today_met = streak_data["today_met"]
    motivation = streak_data["motivation"]

    fire_icons = "🔥" * min(current, 10) if current > 0 else "💤"
    bar = _progress_bar(today_p, target, width=8)
    pct = int(today_p / target * 100) if target else 0

    lines = [
        "🥩 <b>Racha de Proteína</b> 🥩\n",
        f"{fire_icons} Racha actual: <b>{current} día{'s' if current != 1 else ''}</b>",
        f"🏆 Récord histórico: <b>{max_s} día{'s' if max_s != 1 else ''}</b>\n",
        f"🎯 Objetivo diario: <b>{target:.0f} g</b>",
        f"📊 Progreso de hoy: <b>{today_p:.0f} g</b> ({pct}%)",
        f"   {bar} " + ("✅ <i>¡Cumplido hoy!</i>" if today_met else f"<i>(faltan {today_rem:.0f} g)</i>"),
        f"\n💡 <i>{motivation}</i>",
    ]

    await update.message.reply_html("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /hoy
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = await logic.get_today_summary()
    streak = await logic.get_protein_streak()

    weight_line = (
        f"\n⚖️ Peso: <b>{summary['latest_weight']} kg</b>"
        if summary.get("latest_weight")
        else ""
    )

    if not summary["has_data"]:
        msg = (
            f"📅 <b>Resumen de hoy</b> — {summary['date']}{weight_line}\n\n"
            "⚠️ Aún no hay datos de nutrición para hoy.\n"
            "Los datos llegarán cuando Atajos ejecute la automatización o registres en Yazio."
        )
        await update.message.reply_html(msg)
        return

    cal_pct = int(summary["calories_consumed"] / summary["calories_target"] * 100) if summary["calories_target"] else 0
    prot_pct = int(summary["protein_consumed"] / summary["protein_target"] * 100) if summary["protein_target"] else 0

    cal_bar = _progress_bar(summary["calories_consumed"], summary["calories_target"])
    prot_bar = _progress_bar(summary["protein_consumed"], summary["protein_target"])

    cal_remaining = summary["calories_remaining"]
    prot_remaining = summary["protein_remaining"]

    incomplete_notice = "\n⚠️ <i>Datos incompletos (faltan algunos macros)</i>" if not summary["is_complete"] else ""
    streak_line = f"\n🔥 Racha de proteína: <b>{streak['current_streak']} días</b>" if streak["current_streak"] > 0 else ""

    msg = (
        f"📅 <b>Resumen de hoy</b> — {summary['date']}{weight_line}\n\n"
        f"🔥 <b>Calorías</b>: {summary['calories_consumed']:.0f} / {summary['calories_target']:.0f} kcal ({cal_pct}%)\n"
        f"   {cal_bar} "
        + (f"✅ <i>objetivo cumplido</i>" if cal_remaining <= 0 else f"({cal_remaining:.0f} kcal restantes)")
        + f"\n\n"
        f"🥩 <b>Proteína</b>: {summary['protein_consumed']:.0f} / {summary['protein_target']:.0f} g ({prot_pct}%)\n"
        f"   {prot_bar} "
        + (f"✅ <i>objetivo cumplido</i>" if prot_remaining <= 0 else f"({prot_remaining:.0f} g restantes)")
        + f"{streak_line}\n\n"
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
    streak = await logic.get_protein_streak()

    weight_line = f"⚖️ Peso medio: <b>{weekly['avg_weight_kg']} kg</b>" if weekly["avg_weight_kg"] else "⚖️ Sin datos de peso esta semana"

    trend_line = ""
    if weekly["weight_trend_kg"] is not None:
        trend = weekly["weight_trend_kg"]
        emoji = "📉" if trend < 0 else ("📈" if trend > 0 else "➡️")
        trend_line = f"\n   {emoji} Tendencia: {_fmt_diff(trend, ' kg')} respecto al inicio de la semana"

    prot_line = (
        f"🥩 Adherencia proteína: <b>{weekly['protein_adherence_pct']}%</b> de días ≥ {weekly['protein_target']:.0f} g (🔥 Racha actual: {streak['current_streak']}d)"
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
        f"🔥 Total calorías activas: {weekly['total_active_kcal']:.0f} kcal"
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
    targets = await db.get_targets()
    args = context.args or []

    if not args:
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
        lines.append(f"⏱ Duración: <b>{last['duration_min']:.0f} min</b>")
    if last.get("active_energy_kcal"):
        lines.append(f"🔥 Activas: <b>{last['active_energy_kcal']:.0f} kcal</b>")

    # Mostrar Polar H10 FC
    if last.get("avg_hr_bpm") or last.get("max_hr_bpm"):
        hr_parts = []
        if last.get("avg_hr_bpm"):
            hr_parts.append(f"<b>{last['avg_hr_bpm']:.0f} bpm</b> media")
        if last.get("max_hr_bpm"):
            hr_parts.append(f"<b>{last['max_hr_bpm']:.0f} bpm</b> máx")
        source = last.get("source") or "fuente no especificada"
        source_label = "Polar H10" if any(
            marker in source.lower() for marker in ("polar", "h10")
        ) else source
        lines.append(f"❤️ FC: {' | '.join(hr_parts)} <i>({source_label})</i>")

    # Cargar series si están registradas
    exercises = await db.get_workout_exercises(last["id"])
    if exercises:
        lines.append("\n📋 <b>Detalle de ejercicios:</b>")
        current_ex = None
        for s in exercises:
            if s["exercise_name"] != current_ex:
                current_ex = s["exercise_name"]
                lines.append(f"🔹 <b>{current_ex}</b>")
            w_str = f"{s['weight_kg']:.1f}".rstrip("0").rstrip(".") if s.get("weight_kg") is not None else "0"
            rpe_txt = f" @ RPE {s['rpe']}" if s.get("rpe") else ""
            if s.get("is_bodyweight"):
                added = s.get("added_weight_kg") or 0.0
                load_text = f"peso corporal{f' +{added:g} kg' if added else ''}"
            else:
                load_text = f"{w_str} kg"
            lines.append(f"   • Serie {s['set_number']}: {load_text} × {s['reps']} reps{rpe_txt}")

    if last.get("name"):
        comparison = await logic.compare_workout(last["id"], last["name"])
        prev = comparison.get("previous")
        diff = comparison.get("diff")
        if prev and diff:
            lines.append(f"\n📊 <b>Vs. sesión anterior ({prev['date']}):</b>")
            if diff.get("duration_min") is not None:
                lines.append(f"   ⏱ Duración: {_fmt_diff(diff['duration_min'], ' min')}")
            if diff.get("active_energy_kcal") is not None:
                lines.append(f"   🔥 Activas: {_fmt_diff(diff['active_energy_kcal'], ' kcal')}")
            if diff.get("avg_hr_bpm") is not None:
                lines.append(f"   ❤️ FC media: {_fmt_diff(diff['avg_hr_bpm'], ' bpm')}")
            if diff.get("max_hr_bpm") is not None:
                lines.append(f"   ❤️ FC máxima: {_fmt_diff(diff['max_hr_bpm'], ' bpm')}")
        elif not prev:
            lines.append(f"\n📊 Primera sesión de <b>{last['name']}</b> registrada.")

    await update.message.reply_html("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /hevy (Comando manual o ayuda para texto de Hevy)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_hevy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Permite pegar texto de Hevy directamente como argumento o da instrucciones."""
    args_text = " ".join(context.args) if context.args else ""
    if not args_text:
        await update.message.reply_html(
            "🏋️‍♂️ <b>Registro de Hevy:</b>\n\n"
            "Puedes pegar directamente el texto exportado de tu rutina de Hevy en este chat (sin necesidad de escribir /hevy).\n"
            "El bot detectará automáticamente los ejercicios, series, pesos, reps y calculará tu progresión contra la sesión anterior."
        )
        return

    await _process_and_reply_hevy(update, args_text)


async def _process_and_reply_hevy(update: Update, text: str) -> None:
    parsed = parse_hevy_text(text)
    if not parsed or not parsed.get("exercises"):
        await update.message.reply_text("⚠️ No pude reconocer las series o ejercicios en el texto de Hevy proporcionado.")
        return

    workout_id = await db.upsert_workout(
        date_str=parsed["date"],
        name=parsed["workout_name"],
        duration_min=parsed.get("duration_min"),
        active_energy_kcal=None,
        source="hevy_text_import",
        raw=parsed,
    )

    progression = await logic.compare_exercise_progression(workout_id, parsed["exercises"])
    await db.save_workout_exercises(workout_id, parsed["date"], parsed["exercises"])

    summary_html = format_hevy_summary(parsed, progression)
    await update.message.reply_html(summary_html)

    # Registrar en memoria para que el asistente IA lo sepa
    await db.add_chat_message("user", f"[Entrenamiento Hevy registrado]: {parsed['workout_name']} con {parsed['total_sets']} series y {parsed['total_volume_kg']} kg de volumen.")


# ─────────────────────────────────────────────────────────────────────────────
# /consejo
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_consejo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_prompt = (
        " ".join(context.args)
        if context.args
        else "Analiza mi progreso reciente y dame una recomendación concreta."
    )

    await update.message.reply_text("🤔 Analizando tus datos y nuestra conversación… un momento.")
    advice = await get_advice(user_prompt=user_prompt, context_days=7)
    await update.message.reply_html(f"🧠 <b>Consejo personalizado:</b>\n\n{advice}")


# ─────────────────────────────────────────────────────────────────────────────
# /recuerdame
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_recuerdame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Uso: /recuerdame 10:00 Tomar la creatina y desayunar"""
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("⚠️ Uso correcto: /recuerdame 10:00 Texto del recordatorio")
        return

    time_str = args[0]
    message_text = " ".join(args[1:])

    now = datetime.now(ZoneInfo("Europe/Madrid"))
    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Formato de hora inválido. Usa HH:MM (ej. 10:00)")
        return

    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_time < now:
        target_time += timedelta(days=1)

    await schedule_custom_reminder(target_time, message_text)
    await update.message.reply_text(f"✅ ¡Apuntado! Te recordaré: '{message_text}' a las {time_str}.")


# ─────────────────────────────────────────────────────────────────────────────
# /olvidar
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await db.clear_chat_history()
    await update.message.reply_text("🧹 Memoria de la conversación borrada correctamente.")


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Crea una copia manual de la base de datos y conserva las últimas copias."""
    try:
        backup_path = await db.backup_database()
    except Exception:
        logger.exception("No se pudo crear una copia manual desde Telegram")
        await update.message.reply_text("❌ No se pudo crear la copia de seguridad.")
        return

    await update.message.reply_text(
        f"✅ Copia de seguridad creada: {backup_path.name}\n"
        f"Se conservan las últimas {db.settings.backup_retention_days} copias."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chat libre con IA & Detección de Hevy
# ─────────────────────────────────────────────────────────────────────────────


async def handle_free_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Detecta automáticamente si el usuario pegó una rutina de Hevy.
    Si no, procesa la consulta con la IA.
    """
    user_message = update.message.text
    if not user_message:
        return

    # 1. Comprobar si es un texto de entrenamiento de Hevy
    if is_hevy_workout_text(user_message):
        await _process_and_reply_hevy(update, user_message)
        return

    # 2. Conversación natural con la IA
    wait_msg = await update.message.reply_text("🤔 Analizando tus datos y nuestra conversación… un momento.")

    try:
        advice = await get_advice(user_prompt=user_message, context_days=7)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text=f"🧠 <b>Consejo personalizado:</b>\n\n{advice}",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("Error en chat libre con IA: %s", exc)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text="❌ Hubo un error procesando tu mensaje con la IA.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Registro de handlers
# ─────────────────────────────────────────────────────────────────────────────


def register_handlers(application: Application) -> None:
    """Registra todos los handlers en la aplicación del bot."""
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("racha", cmd_racha))
    application.add_handler(CommandHandler("hoy", cmd_hoy))
    application.add_handler(CommandHandler("semana", cmd_semana))
    application.add_handler(CommandHandler("objetivo", cmd_objetivo))
    application.add_handler(CommandHandler("entreno", cmd_entreno))
    application.add_handler(CommandHandler("hevy", cmd_hevy))
    application.add_handler(CommandHandler("consejo", cmd_consejo))
    application.add_handler(CommandHandler("recuerdame", cmd_recuerdame))
    application.add_handler(CommandHandler("olvidar", cmd_olvidar))
    application.add_handler(CommandHandler("backup", cmd_backup))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_message))

    logger.info("Handlers de Telegram registrados (con /racha, Hevy parser y Polar H10).")
