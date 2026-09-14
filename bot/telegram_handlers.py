"""
telegram_handlers.py — Handlers async para todos los comandos y mensajes de Telegram.
Incluye soporte para /racha, /volumen, /records, /quecomo, /grafica, Hevy con PRs, Polar H10 y chat IA.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from bot import db, logic
from bot.ai_advice import get_advice
from bot.audio_transcriber import transcribe_audio
from bot.backup_service import generate_csv_export, get_sqlite_backup_bytes
from bot.charts import generate_progress_chart
from bot.hevy_parser import is_hevy_workout_text, parse_hevy_text, format_hevy_summary
from bot.nlp_parser import parse_weight_intent, parse_explicit_macros_intent, is_food_description_intent, estimate_food_macros_ai
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
# /start y /ayuda
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensaje de bienvenida y guía completa del bot."""
    msg = (
        "👋 <b>¡Bienvenido a tu Entrenador y Nutricionista Personal de Élite!</b>\n\n"
        "Este bot analiza automáticamente tus datos de salud (Apple Health, Yazio, Hevy, Polar H10) "
        "para optimizar tu recomposición corporal basada 100% en evidencia científica.\n\n"
        "📋 <b>Comandos principales:</b>\n"
        "• /hoy — Resumen de calorías, proteína y racha de hoy\n"
        "• /racha — Tu racha de días cumpliendo proteína\n"
        "• /volumen — Series semanales por grupo muscular\n"
        "• /records — Récords personales y 1RM estimado (Epley)\n"
        "• /grafica — Gráfico visual de peso y adherencia\n"
        "• /quecomo — 3 sugerencias de comidas adaptadas a tus macros\n"
        "• /semana — Informe semanal de consistencia y peso\n"
        "• /entreno — Último entreno registrado con FC Polar H10\n"
        "• /exportar o /backup — Descargar historial en CSV y backup SQLite\n"
        "• /objetivo — Ver o editar tus metas (/objetivo cal 1800 prot 135)\n"
        "• /consejo — Pide consejo o escribe cualquier duda al chat\n\n"
        "🎙️ <b>Audios de voz:</b> ¡Envíame una nota de voz hablando y te responderé al instante!\n"
        "📝 <b>Registro rápido:</b> Puedes escribir <i>'Hoy peso 79.5 kg'</i> o <i>'Apunta 30g de proteína'</i> directamente.\n"
        "🏋️‍♂️ <b>Hevy:</b> Pega directamente el texto de tu rutina para analizar PRs y progresión."
    )
    await update.message.reply_html(msg)


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
# /volumen (Series semanales por grupo muscular)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_volumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el volumen de series semanales por grupo muscular vs referencias óptimas."""
    vol_data = await logic.get_weekly_muscle_volume(days=7)

    lines = [
        f"📊 <b>Volumen Semanal por Grupo Muscular</b>",
        f"🗓 Periodo: {vol_data['period_start']} → {vol_data['period_end']}",
        f"🏋️‍♂️ Total series: <b>{vol_data['total_sets']}</b> | Volumen: <b>{vol_data['total_volume_kg']:,.0f} kg</b>\n".replace(",", "."),
    ]

    for g in vol_data["groups"]:
        lines.append(f"{g['emoji']} <b>{g['muscle']}</b>: {g['status_emoji']} {g['status_text']}")

    lines.append("\n💡 <i>Referencia científica (Schoenfeld et al.): 10-20 series semanales por músculo maximizan la hipertrofia.</i>")
    await update.message.reply_html("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /records o /prs (Mejores marcas personales históricas)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra las mejores marcas y 1RM estimado por ejercicio."""
    prs = await logic.get_all_personal_records()

    if not prs:
        await update.message.reply_text("🏆 Aún no hay ejercicios registrados en la base de datos para calcular récords.")
        return

    lines = ["🏆 <b>Récords Personales (1RM Estimado - Epley)</b>\n"]
    current_group = None

    for pr in prs:
        if pr["muscle_group"] != current_group:
            current_group = pr["muscle_group"]
            lines.append(f"\n📂 <b>{current_group}</b>")

        w_str = f"{pr['best_weight']:.1f}".rstrip("0").rstrip(".")
        lines.append(
            f"  • <b>{pr['exercise_name']}</b>: {w_str} kg × {pr['best_reps']} reps "
            f"(1RM est: <b>{pr['e1rm']:.1f} kg</b>)"
        )

    await update.message.reply_html("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /quecomo o /cena (Sugerencias inteligentes de comida)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_quecomo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Genera 3 opciones de comida adaptadas a los macros restantes de hoy."""
    meal_type = " ".join(context.args) if context.args else "cena"
    wait_msg = await update.message.reply_text("🍳 Analizando tus macros restantes y buscando 3 opciones ideales… un momento.")

    suggestions = await logic.get_meal_suggestions(meal_type=meal_type)
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=wait_msg.message_id,
        text=suggestions,
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /grafica o /progreso (Gráficos visuales con Matplotlib)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_grafica(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Genera y envía una imagen gráfica con la evolución de peso y adherencia nutricional."""
    days = 21
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])

    wait_msg = await update.message.reply_text("📈 Generando gráfico de evolución… un momento.")

    chart_bytes = await generate_progress_chart(days=days)
    if not chart_bytes:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text="⚠️ No hay suficientes datos de peso o nutrición para generar el gráfico.",
        )
        return

    # Eliminar mensaje de espera y enviar la foto
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=io.BytesIO(chart_bytes),
        caption=f"📈 <b>Evolución de los últimos {days} días</b> (Peso con media móvil y balance nutricional)",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /temporada
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_temporada(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el progreso mensual gamificado con nivel y XP."""
    requested_month = context.args[0] if context.args else None
    try:
        data = await logic.get_season_summary(requested_month)
    except ValueError:
        await update.message.reply_text("⚠️ Formato de mes inválido. Usa YYYY-MM (ej. 2026-08)")
        return

    month_name = datetime.strptime(data["month"], "%Y-%m").strftime("%B %Y").capitalize()
    star_line = f"⭐ Ejercicio estrella: <b>{data['star_exercise']}</b>" if data["star_exercise"] else "⭐ Ejercicio estrella: <i>sin datos</i>"
    lines = [
        f"🏆 <b>Temporada {month_name}</b>",
        f"🏅 Nivel: <b>{data['level']}</b> ({data['xp']} XP)",
        "",
        f"💪 Entrenos completados: <b>{data['num_workouts']}</b>",
        f"📦 Volumen total: <b>{data['total_volume_kg']:,.0f} kg</b>".replace(",", "."),
        f"🥩 Días cumpliendo proteína: <b>{data['protein_days']}</b>",
        f"🔥 Récords superados: <b>{data['personal_records']}</b>",
        star_line,
    ]
    if data["personal_record_names"]:
        lines.append("   • " + ", ".join(data["personal_record_names"]))
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
        msg += "\n<i>Para editar: /objetivo cal 1800 prot 135 carbs 180 gras 55</i>"
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
            "⚠️ No entendí los argumentos.\nUso: /objetivo cal 1800 prot 135 carbs 180 gras 55"
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
        lines.append(f"❤️ FC: {' | '.join(hr_parts)} <i>(Polar H10)</i>")

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
            lines.append(f"   • Serie {s['set_number']}: {w_str} kg × {s['reps']} reps{rpe_txt}")

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
            "El bot detectará automáticamente los ejercicios, series, pesos, reps, PRs y calculará tu progresión contra la sesión anterior."
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

    prs = await logic.detect_workout_prs(workout_id, parsed["exercises"])
    progression = await logic.compare_exercise_progression(workout_id, parsed["exercises"])
    await db.save_workout_exercises(workout_id, parsed["date"], parsed["exercises"])

    summary_html = format_hevy_summary(parsed, progression, prs=prs)
    await update.message.reply_html(summary_html)

    # Registrar en memoria para que el asistente IA lo sepa en detalle
    ex_details = []
    for ex in parsed["exercises"]:
        best = ex.get("best_set")
        best_txt = f" (máx {best['weight_kg']:g}kg x {best['reps']})" if best else ""
        ex_details.append(f"{ex['name']} ({len(ex['sets'])} series{best_txt})")
    ex_summary_str = "; ".join(ex_details)
    await db.add_chat_message(
        "user",
        f"[Entrenamiento registrado: {parsed['workout_name']} ({parsed['date']}) — {parsed['total_sets']} series, {parsed['total_volume_kg']:,.0f} kg volumen total. Ejercicios: {ex_summary_str}]"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper para envío seguro de respuestas de IA (evita fallos de HTML y divide si > 4000 chars)
# ─────────────────────────────────────────────────────────────────────────────


async def _reply_ai_safe(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    wait_message_id: int | None,
    advice: str,
    title: str = "🧠 <b>Consejo personalizado:</b>\n\n",
) -> None:
    """Envía la respuesta de la IA de forma segura, dividiéndola en trozos si excede el límite de Telegram."""
    full_text = f"{title}{advice}"
    
    # Telegram max limit is 4096 chars. Split by paragraph if longer.
    max_chunk = 3900
    chunks = []
    
    if len(full_text) <= max_chunk:
        chunks = [full_text]
    else:
        current_chunk = ""
        for line in full_text.splitlines(keepends=True):
            if len(current_chunk) + len(line) > max_chunk:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += line
        if current_chunk:
            chunks.append(current_chunk)

    for idx, chunk in enumerate(chunks):
        if idx == 0 and wait_message_id is not None:
            # Intentar editar el mensaje de espera
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=wait_message_id,
                    text=chunk,
                    parse_mode="HTML",
                )
            except Exception:
                # Si falla por entidades HTML inválidas en el texto de la IA, enviar como texto plano
                clean_chunk = chunk.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=wait_message_id,
                    text=clean_chunk,
                )
        else:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="HTML",
                )
            except Exception:
                clean_chunk = chunk.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=clean_chunk,
                )


# ─────────────────────────────────────────────────────────────────────────────
# /consejo
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_consejo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_prompt = (
        " ".join(context.args)
        if context.args
        else "Analiza mi progreso reciente y dame una recomendación concreta."
    )

    wait_msg = await update.message.reply_text("🤔 Analizando tus datos y nuestra conversación… un momento.")
    advice = await get_advice(user_prompt=user_prompt, context_days=7)
    await _reply_ai_safe(
        context=context,
        chat_id=update.effective_chat.id,
        wait_message_id=wait_msg.message_id,
        advice=advice,
        title="🧠 <b>Consejo personalizado:</b>\n\n",
    )


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


# ─────────────────────────────────────────────────────────────────────────────
# /exportar y /backup (Exportación CSV y copia de seguridad de Base de Datos)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Genera y envía una exportación completa en CSV y la copia de seguridad SQLite."""
    wait_msg = await update.message.reply_text("📦 Generando archivos de exportación… un momento.")

    try:
        # 1. Enviar CSV para Excel / Google Sheets
        csv_bytes, csv_filename = await generate_csv_export()
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(csv_bytes),
            filename=csv_filename,
            caption="📊 <b>Historial completo exportado en CSV</b>\n(Abre directamente en Excel o Google Sheets).",
            parse_mode="HTML",
        )

        # 2. Enviar SQLite DB Backup
        db_bytes, db_filename = get_sqlite_backup_bytes()
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(db_bytes),
            filename=db_filename,
            caption="💾 <b>Copia de seguridad completa (.db)</b>\nGuarda este archivo para restaurar tu base de datos.",
            parse_mode="HTML",
        )

        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
    except Exception as exc:
        logger.error("Error en exportación/backup: %s", exc, exc_info=True)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text=f"⚠️ Error al generar el backup: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Procesamiento de Notas de Voz (Whisper) & Texto Unificado (NLP + IA)
# ─────────────────────────────────────────────────────────────────────────────


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Descarga el audio de Telegram, lo transcribe con Groq Whisper y lo procesa.
    """
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    wait_msg = await update.message.reply_text("🎙️ Transcribiendo audio con Whisper… un momento.")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_bytearray = await file.download_as_bytearray()
        audio_bytes = bytes(audio_bytearray)

        transcription = await transcribe_audio(audio_bytes, filename="voice.ogg")
        if not transcription:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=wait_msg.message_id,
                text="⚠️ No pude entender el audio o hubo un problema al transcribir.",
            )
            return

        # Eliminar el mensaje de espera de transcripción
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)

        # Mostrar lo que entendió y procesarlo
        await update.message.reply_html(f"🗣️ <i>\"{transcription}\"</i>")
        await _process_text_message(update, context, transcription)

    except Exception as exc:
        logger.error("Error procesando nota de voz: %s", exc, exc_info=True)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text="❌ Hubo un error al procesar tu nota de voz.",
        )


async def _process_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str) -> None:
    """
    Pipeline unificado de procesamiento:
    1. Hevy Workout text import
    2. NLP Registro de peso
    3. NLP Registro de macros explícitos (kcal / g proteína)
    4. NLP Estimación de alimentos descritos con IA
    5. Asistente y Entrenador Personal IA
    """
    # 1. Comprobar si es un texto de entrenamiento de Hevy
    if is_hevy_workout_text(user_message):
        await _process_and_reply_hevy(update, user_message)
        return

    # 2. Comprobar si es un registro de peso (ej. 'Hoy peso 79.5 kg')
    weight = parse_weight_intent(user_message)
    if weight is not None:
        today_str = datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()
        await db.upsert_body_weight(today_str, weight)
        recalculated = await logic.recalculate_targets_if_needed()
        trend = await logic.get_weight_trend(7)
        targets = await db.get_targets()
        prot_target = (targets or {}).get("protein_target_g") or (weight * 2.0)

        trend_txt = f" (media 7d: {trend:.1f} kg)" if trend else ""
        recalc_txt = f"\n🎯 Proteína objetivo recalculada: <b>{prot_target:.0f} g</b>" if recalculated else ""
        await update.message.reply_html(
            f"⚖️ <b>Peso registrado:</b> <b>{weight:.1f} kg</b>{trend_txt}{recalc_txt}\n\n"
            f"<i>Registrado para hoy ({today_str}).</i>"
        )
        await db.add_chat_message("user", f"[Registro de peso]: {weight:.1f} kg.")
        return

    # 3. Comprobar si son macros explícitos (ej. 'Apunta 300 kcal y 35g prote')
    macros = parse_explicit_macros_intent(user_message)
    if macros is not None:
        today_str = datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()
        updated = await db.add_to_daily_nutrition(
            date_str=today_str,
            calories=macros["calories"],
            protein_g=macros["protein_g"],
            carbs_g=macros["carbs_g"],
            fat_g=macros["fat_g"],
            source="quick_nlp",
        )
        summary = await logic.get_today_summary()
        prot_rem = summary["protein_remaining"]
        cal_rem = summary["calories_remaining"]

        added_parts = []
        if macros["calories"]: added_parts.append(f"🔥 +{macros['calories']:.0f} kcal")
        if macros["protein_g"]: added_parts.append(f"🥩 +{macros['protein_g']:.0f}g prote")
        if macros["carbs_g"]: added_parts.append(f"🍞 +{macros['carbs_g']:.0f}g carb")
        if macros["fat_g"]: added_parts.append(f"🫒 +{macros['fat_g']:.0f}g gras")

        await update.message.reply_html(
            f"✅ <b>Nutrición añadida:</b>\n"
            f"{' | '.join(added_parts)}\n\n"
            f"📊 <b>Total acumulado hoy:</b> {updated['calories']:.0f} kcal | {updated['protein_g']:.0f}g proteína\n"
            f"🎯 <i>Restante: {max(0, cal_rem):.0f} kcal | {max(0, prot_rem):.0f}g proteína</i>"
        )
        await db.add_chat_message("user", f"[Nutrición añadida]: +{macros['calories']:.0f} kcal, +{macros['protein_g']:.0f}g prote.")
        return

    # 4. Comprobar si describe alimentos para estimación nutricional con IA
    if is_food_description_intent(user_message):
        wait_food = await update.message.reply_text("🍳 Estimando información nutricional del plato con IA…")
        estimated = await estimate_food_macros_ai(user_message)
        if estimated and (estimated["calories"] > 0 or estimated["protein_g"] > 0):
            today_str = datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()
            updated = await db.add_to_daily_nutrition(
                date_str=today_str,
                calories=estimated["calories"],
                protein_g=estimated["protein_g"],
                carbs_g=estimated["carbs_g"],
                fat_g=estimated["fat_g"],
                source="quick_nlp_ai",
            )
            summary = await logic.get_today_summary()
            notes_txt = f"\n💡 <i>{estimated['notes']}</i>" if estimated.get("notes") else ""

            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=wait_food.message_id,
                text=(
                    f"🍽️ <b>{estimated['food_name']}</b> registrada:\n"
                    f"🔥 <b>{estimated['calories']:.0f} kcal</b> | 🥩 <b>{estimated['protein_g']:.0f}g prote</b> | "
                    f"🍞 <b>{estimated['carbs_g']:.0f}g carb</b> | 🫒 <b>{estimated['fat_g']:.0f}g gras</b>{notes_txt}\n\n"
                    f"📊 <b>Total acumulado hoy:</b> {updated['calories']:.0f} kcal | {updated['protein_g']:.0f}g proteína\n"
                    f"🎯 <i>Restante: {max(0, summary['calories_remaining']):.0f} kcal | {max(0, summary['protein_remaining']):.0f}g proteína</i>"
                ),
                parse_mode="HTML",
            )
            await db.add_chat_message("user", f"[Comida registrada: {estimated['food_name']} ({estimated['calories']:.0f} kcal, {estimated['protein_g']:.0f}g prote)]")
            return
        else:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_food.message_id)

    # 5. Conversación natural con la IA (Entrenador / Nutricionista)
    wait_msg = await update.message.reply_text("🤔 Analizando tus datos y nuestra conversación… un momento.")

    try:
        advice = await get_advice(user_prompt=user_message, context_days=7)
        await _reply_ai_safe(
            context=context,
            chat_id=update.effective_chat.id,
            wait_message_id=wait_msg.message_id,
            advice=advice,
            title="🧠 <b>Consejo personalizado:</b>\n\n",
        )
    except Exception as exc:
        logger.error("Error en chat libre con IA: %s", exc)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text="❌ Hubo un error procesando tu mensaje con la IA.",
        )


async def handle_free_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja mensajes de texto libre dirigiéndolos al pipeline unificado."""
    user_message = update.message.text
    if not user_message:
        return
    await _process_text_message(update, context, user_message)


# ─────────────────────────────────────────────────────────────────────────────
# Registro de handlers
# ─────────────────────────────────────────────────────────────────────────────


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejo centralizado de errores para evitar caídas silenciosas."""
    logger.error("Excepción manejando actualización %s: %s", update, context.error, exc_info=context.error)


def register_handlers(application: Application) -> None:
    """Registra todos los handlers en la aplicación del bot."""
    application.add_handler(CommandHandler(["start", "ayuda", "help"], cmd_start))
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("racha", cmd_racha))
    application.add_handler(CommandHandler("hoy", cmd_hoy))
    application.add_handler(CommandHandler("semana", cmd_semana))
    application.add_handler(CommandHandler("volumen", cmd_volumen))
    application.add_handler(CommandHandler(["records", "prs", "record"], cmd_records))
    application.add_handler(CommandHandler(["quecomo", "cena", "comida"], cmd_quecomo))
    application.add_handler(CommandHandler(["grafica", "progreso", "grafico"], cmd_grafica))
    application.add_handler(CommandHandler("temporada", cmd_temporada))
    application.add_handler(CommandHandler("objetivo", cmd_objetivo))
    application.add_handler(CommandHandler("entreno", cmd_entreno))
    application.add_handler(CommandHandler("hevy", cmd_hevy))
    application.add_handler(CommandHandler(["exportar", "backup", "csv"], cmd_exportar))
    application.add_handler(CommandHandler("consejo", cmd_consejo))
    application.add_handler(CommandHandler("recuerdame", cmd_recuerdame))
    application.add_handler(CommandHandler("olvidar", cmd_olvidar))

    # Mensajes de voz y audio (Groq Whisper)
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message))

    # Mensajes de texto libre (NLP + Hevy + Asistente IA)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_message))
    
    application.add_error_handler(error_handler)

    logger.info("Handlers de Telegram registrados (con /start, /racha, /volumen, /records, /quecomo, /grafica, /exportar, Whisper y NLP).")
