"""
charts.py — Generación de gráficos visuales de progreso con Matplotlib.
Produce imágenes en memoria para enviar directamente a Telegram.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Backend no interactivo para entornos headless
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from bot import db, logic

logger = logging.getLogger(__name__)

# Estilo visual moderno y limpio
plt.rcParams.update({
    "figure.facecolor": "#181e24",
    "axes.facecolor": "#212a33",
    "axes.edgecolor": "#3b4856",
    "axes.labelcolor": "#e0e6ed",
    "text.color": "#e0e6ed",
    "xtick.color": "#a8b5c2",
    "ytick.color": "#a8b5c2",
    "grid.color": "#2c3844",
    "grid.linestyle": "--",
    "grid.alpha": 0.6,
    "font.sans-serif": ["DejaVu Sans", "Segoe UI", "Arial"],
})


async def generate_progress_chart(days: int = 21) -> bytes | None:
    """
    Genera un dashboard visual con 2 gráficos:
    1. Evolución del Peso Corporal con Media Móvil de 7 días.
    2. Adherencia a Calorías y Proteína vs Objetivos.
    Retorna los bytes PNG de la imagen.
    """
    end_date = logic._today()
    start_date = logic._n_days_ago(days - 1)

    # 1. Obtener datos
    weights_raw = await db.get_weight_range(start_date, end_date)
    nutrition_raw = await db.get_nutrition_range(start_date, end_date)
    targets = await db.get_targets() or {}
    cal_target = targets.get("calorie_target", 1800)
    prot_target = targets.get("protein_target_g", 135)

    if not weights_raw and not nutrition_raw:
        logger.info("Sin datos suficientes para generar gráfico.")
        return None

    # Mapear rango completo de fechas
    cur = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    all_dates = []
    while cur <= end:
        all_dates.append(cur)
        cur += timedelta(days=1)

    weight_dict = {
        datetime.fromisoformat(r["date"]).date(): r["weight_kg"]
        for r in weights_raw if r.get("weight_kg")
    }
    nut_dict = {
        datetime.fromisoformat(r["date"]).date(): r
        for r in nutrition_raw
    }

    # Series de datos
    w_dates, w_vals = [], []
    for d in all_dates:
        if d in weight_dict:
            w_dates.append(d)
            w_vals.append(weight_dict[d])

    cal_dates = [d for d in all_dates if d in nut_dict and nut_dict[d].get("calories")]
    cal_vals = [nut_dict[d]["calories"] for d in cal_dates]
    prot_vals = [nut_dict[d].get("protein_g", 0) for d in cal_dates]

    # Crear figura con 2 subplots verticales
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True, gridspec_kw={"height_ratios": [1.2, 1]})
    fig.subplots_adjust(hspace=0.25)

    # ── Panel 1: Peso y Media Móvil ──────────────────────────────────────────
    if w_vals:
        ax1.plot(w_dates, w_vals, marker="o", color="#4fc3f7", linewidth=1.5, markersize=5, label="Peso diario (kg)", alpha=0.8)
        
        # Calcular media móvil si hay suficientes puntos
        if len(w_vals) >= 3:
            # Media móvil acumulativa / ventana 5
            rolling_w = []
            window = 5
            for i in range(len(w_vals)):
                sub = w_vals[max(0, i - window + 1):i + 1]
                rolling_w.append(sum(sub) / len(sub))
            ax1.plot(w_dates, rolling_w, color="#ffd54f", linewidth=2.5, label="Tendencia (media móvil)")

        trend_txt = f"Último: {w_vals[-1]:.1f} kg"
        if len(w_vals) >= 2:
            diff = w_vals[-1] - w_vals[0]
            sign = "+" if diff >= 0 else ""
            trend_txt += f" ({sign}{diff:.1f} kg en el periodo)"

        ax1.set_title(f"Evolución de Peso Corporal — {trend_txt}", fontsize=13, fontweight="bold", pad=10, color="#ffffff")
        ax1.set_ylabel("Kg", fontsize=11, fontweight="bold")
        ax1.grid(True)
        ax1.legend(loc="upper right", framealpha=0.4, facecolor="#181e24")
    else:
        ax1.text(0.5, 0.5, "Sin registros de peso en los últimos días", ha="center", va="center", color="#a8b5c2")
        ax1.set_title("Evolución de Peso Corporal", fontsize=13, fontweight="bold", color="#ffffff")

    # ── Panel 2: Calorías y Proteína ─────────────────────────────────────────
    if cal_vals:
        width = 0.4
        x_nums = mdates.date2num(cal_dates)
        
        # Barras de Calorías (eje primario)
        bars = ax2.bar(x_nums - width/2, cal_vals, width=width, color="#ff8a65", alpha=0.85, label="Kcal consumidas")
        ax2.axhline(cal_target, color="#ff5722", linestyle="--", linewidth=1.5, label=f"Objetivo Kcal ({cal_target:.0f})")
        ax2.set_ylabel("Calorías (kcal)", color="#ff8a65", fontsize=11, fontweight="bold")
        ax2.tick_params(axis="y", labelcolor="#ff8a65")

        # Eje secundario para Proteína
        ax2_prot = ax2.twinx()
        bars_prot = ax2_prot.bar(x_nums + width/2, prot_vals, width=width, color="#81c784", alpha=0.85, label="Proteína (g)")
        ax2_prot.axhline(prot_target, color="#4caf50", linestyle=":", linewidth=1.8, label=f"Objetivo Prot ({prot_target:.0f} g)")
        ax2_prot.set_ylabel("Proteína (g)", color="#81c784", fontsize=11, fontweight="bold")
        ax2_prot.tick_params(axis="y", labelcolor="#81c784")
        ax2_prot.grid(False)

        ax2.set_title("Nutrición Diaria vs Objetivos", fontsize=13, fontweight="bold", pad=10, color="#ffffff")
        ax2.grid(True)
    else:
        ax2.text(0.5, 0.5, "Sin registros nutricionales en el periodo", ha="center", va="center", color="#a8b5c2")
        ax2.set_title("Nutrición Diaria", fontsize=13, fontweight="bold", color="#ffffff")

    # Formato de fechas en el eje X
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax2.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(all_dates) // 8)))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha="right")

    plt.tight_layout()

    # Guardar en buffer de bytes
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=160, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
