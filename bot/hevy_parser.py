"""
hevy_parser.py — Parser para exportaciones de texto de Hevy y cálculo de volumen.
Soporta formatos en español e inglés, peso corporal, incrementos (+kg) y RPE.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo


MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

MONTHS_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _today_madrid() -> str:
    return datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()


def is_hevy_workout_text(text: str) -> bool:
    """
    Detecta si un texto pegado por el usuario corresponde a una rutina de Hevy.
    Comprueba patrones de series (Set 1, Serie 1, 80 kg x 8, etc.).
    """
    if not text or len(text.strip()) < 10:
        return False

    patterns = [
        r"(?:serie|set)\s*\d+[\s:]+",
        r"\d+[\.,]?\d*\s*kg\s*[x×]\s*\d+",
        r"(?:bw|body\s*weight|peso\s*corporal)\s*[x×]\s*\d+",
        r"(?:hevy|rutina|workout|entrenamiento)",
        r"(?:press|sentadilla|squat|peso muerto|deadlift|remo|dominadas|curl|jalon|elevaciones)",
    ]

    has_set_pattern = bool(
        re.search(r"(?:serie|set)\s*\d+[\s:]+", text, re.IGNORECASE)
        or re.search(r"\d+[\.,]?\d*\s*kg\s*[x×]\s*\d+", text, re.IGNORECASE)
        or re.search(r"(?:bw|body\s*weight|peso\s*corporal)\s*[x×]\s*\d+", text, re.IGNORECASE)
    )

    matches = sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))
    return has_set_pattern and matches >= 2


def parse_hevy_text(text: str) -> Optional[dict[str, Any]]:
    """
    Parsea un texto de entreno de Hevy devolviendo:
    {
        "workout_name": str,
        "date": "YYYY-MM-DD",
        "exercises": [
            {
                "name": str,
                "sets": [
                    {"set_number": 1, "weight_kg": 80.0, "reps": 8, "rpe": 8.5},
                    ...
                ],
                "total_volume_kg": float,
                "best_set": {"weight_kg": 80.0, "reps": 8}
            }
        ],
        "total_sets": int,
        "total_volume_kg": float,
        "duration_min": Optional[float]
    }
    """
    if not text:
        return None

    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    if not lines:
        return None

    workout_name = "Entrenamiento Hevy"
    workout_date = _today_madrid()
    duration_min: Optional[float] = None

    first_line = lines[0]
    if not re.search(r"(?:serie|set|\d+\s*kg)", first_line, re.IGNORECASE):
        clean_title = re.sub(r"^[🏋️‍♂️💪🔥📝\s]+", "", first_line).strip()
        clean_title = re.sub(
            r"\s+(?:lunes|martes|miércoles|jueves|viernes|sábado|domingo|monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+"
            r"(?:ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|jan|apr|aug|dec)\s+\d{1,2},?\s+\d{4}.*$",
            "",
            clean_title,
            flags=re.IGNORECASE,
        ).strip()
        if clean_title:
            workout_name = clean_title

    for line in lines[:4]:
        iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", line)
        if iso_match:
            workout_date = iso_match.group(1)
            break
        es_match = re.search(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)\s+(?:de\s+)?(\d{4})", line, re.IGNORECASE)
        if es_match:
            d = int(es_match.group(1))
            m_str = es_match.group(2).lower()
            y = int(es_match.group(3))
            m = MONTHS_ES.get(m_str, 1)
            workout_date = f"{y:04d}-{m:02d}-{d:02d}"
            break

        en_match = re.search(
            r"(?:lunes|martes|miércoles|jueves|viernes|sábado|domingo|monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+"
            r"([a-z]+)\s+(\d{1,2}),?\s+(\d{4})",
            line,
            re.IGNORECASE,
        )
        if en_match:
            m_str = en_match.group(1).lower()[:3]
            m = MONTHS_ES.get(m_str) or MONTHS_EN.get(m_str)
            if m:
                workout_date = f"{int(en_match.group(3)):04d}-{m:02d}-{int(en_match.group(2)):02d}"
                break

        dur_h_m = re.search(r"(?:(\d+)\s*h)?\s*(\d+)\s*(?:min|m)", line, re.IGNORECASE)
        if dur_h_m:
            hours = int(dur_h_m.group(1)) if dur_h_m.group(1) else 0
            mins = int(dur_h_m.group(2)) if dur_h_m.group(2) else 0
            if hours > 0 or mins > 0:
                duration_min = float(hours * 60 + mins)

    exercises: list[dict[str, Any]] = []
    current_exercise: Optional[dict[str, Any]] = None

    set_pattern = re.compile(
        r"(?:(?:set|serie)\s*(\d+)|\b(\d+))[\s:\-\.]+"
        r"(?:\+?\s*(\d+[\.,]?\d*)\s*(?:kg|lbs|kilos)?)?"
        r"(?:\s*[x×]\s*(\d+)\s*(?:reps?|repeticiones)?)?"
        r"(?:.*?@\s*rpe\s*(\d+[\.,]?\d*)|.*?rpe\s*(\d+[\.,]?\d*))?",
        re.IGNORECASE
    )

    bodyweight_set_pattern = re.compile(
        r"(?:(?:set|serie)\s*(\d+)|\b(\d+))[\s:\-\.]+"
        r"(?:bw|body\s*weight|peso\s*corporal)\s*[x×]\s*"
        r"(\d+)\s*(?:reps?|repeticiones)?",
        re.IGNORECASE,
    )

    alt_set_pattern = re.compile(
        r"(?:(?:\+?\s*(\d+[\.,]?\d*)\s*(?:kg|kilos|lbs))\s*[x×]\s*(\d+))"
        r"|(?:\b(\d+)\s*reps?\s*[x×]\s*(\d+[\.,]?\d*)\s*kg)",
        re.IGNORECASE
    )

    inline_set_marker = re.compile(r"(?:set|serie)\s*(\d+)\s*[:\-]", re.IGNORECASE)

    def add_set(exercise: dict[str, Any], set_number: int, weight_kg: float,
                reps: int, rpe: Optional[float] = None,
                is_bodyweight: bool = False, added_weight_kg: float = 0.0) -> None:
        set_data = {
            "set_number": set_number,
            "weight_kg": weight_kg,
            "is_bodyweight": is_bodyweight,
            "added_weight_kg": added_weight_kg if is_bodyweight else 0.0,
            "reps": reps,
            "rpe": rpe,
            "volume_kg": round(weight_kg * reps, 1),
        }
        exercise["sets"].append(set_data)
        exercise["total_volume_kg"] = round(
            exercise["total_volume_kg"] + weight_kg * reps, 1
        )
        best = exercise["best_set"]
        if not best or (weight_kg > best["weight_kg"]) or (
            weight_kg == best["weight_kg"] and reps > best["reps"]
        ):
            exercise["best_set"] = {"weight_kg": weight_kg, "reps": reps}

    for line in lines:
        if line == first_line and workout_name == first_line:
            continue
        if re.search(r"^(?:lunes|martes|miércoles|jueves|viernes|sábado|domingo|\d{1,2}\s+de)", line, re.IGNORECASE):
            continue
        if re.search(r"^(?:hevy|compartido desde|workout summary)", line, re.IGNORECASE):
            continue

        inline_markers = list(inline_set_marker.finditer(line))
        if inline_markers:
            exercise_name = line[:inline_markers[0].start()].strip()
            exercise_name = re.sub(r"^[\d\.\-\)•\*]+\s*", "", exercise_name).strip()
            if exercise_name:
                current_exercise = {
                    "name": exercise_name,
                    "sets": [],
                    "total_volume_kg": 0.0,
                    "best_set": None,
                }
                exercises.append(current_exercise)

            if current_exercise is None:
                current_exercise = {
                    "name": "Ejercicio General",
                    "sets": [],
                    "total_volume_kg": 0.0,
                    "best_set": None,
                }
                exercises.append(current_exercise)

            for index, marker in enumerate(inline_markers):
                end = inline_markers[index + 1].start() if index + 1 < len(inline_markers) else len(line)
                segment = line[marker.end():end]
                set_number = int(marker.group(1))
                bodyweight_match = re.search(
                    r"(?:bw|body\s*weight|peso\s*corporal)\s*[x×]\s*(\d+)",
                    segment,
                    re.IGNORECASE,
                )
                if bodyweight_match:
                    add_set(current_exercise, set_number, 0.0, int(bodyweight_match.group(1)), is_bodyweight=True)
                    continue

                load_match = re.search(
                    r"(\+?\s*\d+[\.,]?\d*)\s*(kg|kilos|lbs)?\s*[x×]\s*(\d+)\s*(?:reps?|repeticiones)?",
                    segment,
                    re.IGNORECASE,
                )
                if not load_match:
                    continue
                weight = float(load_match.group(1).replace("+", "").replace(",", ".").strip())
                if load_match.group(2) and load_match.group(2).lower() == "lbs":
                    weight = round(weight * 0.45359237, 2)
                reps = int(load_match.group(3))
                rpe_match = re.search(r"rpe\s*(\d+[\.,]?\d*)", segment, re.IGNORECASE)
                rpe = float(rpe_match.group(1).replace(",", ".")) if rpe_match else None
                is_added_load = load_match.group(1).strip().startswith("+")
                add_set(
                    current_exercise,
                    set_number,
                    weight,
                    reps,
                    rpe=rpe,
                    is_bodyweight=is_added_load,
                    added_weight_kg=weight if is_added_load else 0.0,
                )
            continue

        set_num: Optional[int] = None
        weight_kg: Optional[float] = None
        reps: Optional[int] = None
        rpe: Optional[float] = None
        is_bodyweight = False
        added_weight_kg = 0.0

        bodyweight_match = bodyweight_set_pattern.search(line)
        m = set_pattern.search(line)
        if bodyweight_match:
            set_num = int(bodyweight_match.group(1) or bodyweight_match.group(2) or 1)
            weight_kg = 0.0
            reps = int(bodyweight_match.group(3))
            is_bodyweight = True
        elif m and (m.group(3) or m.group(4)):
            if m.group(1):
                set_num = int(m.group(1))
            elif m.group(2) and (current_exercise is not None):
                set_num = int(m.group(2))

            if m.group(3):
                weight_kg = float(m.group(3).replace(",", "."))
                if re.search(r"(?:set|serie|\b\d+)[\s:\-\.]+\+\s*\d", line, re.IGNORECASE):
                    is_bodyweight = True
                    added_weight_kg = weight_kg
            else:
                weight_kg = 0.0

            if m.group(4):
                reps = int(m.group(4))

            rpe_str = m.group(5) or m.group(6)
            if rpe_str:
                try:
                    rpe = float(rpe_str.replace(",", "."))
                except ValueError:
                    rpe = None
        else:
            alt_m = alt_set_pattern.search(line)
            if alt_m:
                if alt_m.group(1) and alt_m.group(2):
                    weight_kg = float(alt_m.group(1).replace(",", "."))
                    reps = int(alt_m.group(2))
                    if re.search(r"\+\s*\d", line):
                        is_bodyweight = True
                        added_weight_kg = weight_kg
                elif alt_m.group(3) and alt_m.group(4):
                    reps = int(alt_m.group(3))
                    weight_kg = float(alt_m.group(4).replace(",", "."))

        if weight_kg is not None and reps is not None:
            if current_exercise is None:
                current_exercise = {
                    "name": "Ejercicio General",
                    "sets": [],
                    "total_volume_kg": 0.0,
                    "best_set": None,
                }
                exercises.append(current_exercise)

            if set_num is None:
                set_num = len(current_exercise["sets"]) + 1

            add_set(
                current_exercise,
                set_num,
                weight_kg,
                reps,
                rpe=rpe,
                is_bodyweight=is_bodyweight,
                added_weight_kg=added_weight_kg,
            )
        else:
            clean_name = re.sub(r"^\d+[\.\-\)]\s*", "", line).strip()
            clean_name = re.sub(r"^[•\-\*🏋️‍♂️💪🔥]\s*", "", clean_name).strip()

            if clean_name and len(clean_name) > 2 and not clean_name.lower().startswith("nota"):
                if current_exercise and not current_exercise["sets"]:
                    current_exercise["name"] = clean_name
                else:
                    current_exercise = {
                        "name": clean_name,
                        "sets": [],
                        "total_volume_kg": 0.0,
                        "best_set": None,
                    }
                    exercises.append(current_exercise)

    valid_exercises = [ex for ex in exercises if ex["sets"]]
    if not valid_exercises:
        return None

    total_sets = sum(len(ex["sets"]) for ex in valid_exercises)
    total_volume = round(sum(ex["total_volume_kg"] for ex in valid_exercises), 1)

    return {
        "workout_name": workout_name,
        "date": workout_date,
        "exercises": valid_exercises,
        "total_sets": total_sets,
        "total_volume_kg": total_volume,
        "duration_min": duration_min,
    }


def format_hevy_summary(
    parsed: dict[str, Any],
    progression: Optional[list[dict[str, Any]]] = None
) -> str:
    """Genera un mensaje formateado en HTML para Telegram con los detalles del entreno y progresión."""
    lines = [
        f"🏋️‍♂️ <b>{parsed['workout_name']}</b> — {parsed['date']}",
        f"📊 Total series: <b>{parsed['total_sets']}</b> | Volumen: <b>{parsed['total_volume_kg']:,.0f} kg</b>".replace(",", "."),
    ]
    if parsed.get("duration_min"):
        lines.append(f"⏱ Duración: <b>{parsed['duration_min']:.0f} min</b>")

    lines.append("")

    prog_map = {p["exercise_name"]: p for p in (progression or [])}

    for ex in parsed["exercises"]:
        ex_name = ex["name"]
        lines.append(f"🔹 <b>{ex_name}</b>")

        for s in ex["sets"]:
            rpe_txt = f" @ RPE {s['rpe']}" if s.get("rpe") else ""
            if s.get("is_bodyweight"):
                added = s.get("added_weight_kg") or 0.0
                load = f" +{added:g} kg" if added else ""
                load_text = f"peso corporal{load}"
            else:
                w_str = f"{s['weight_kg']:.1f}".rstrip("0").rstrip(".")
                load_text = f"{w_str} kg"
            lines.append(f"   • Serie {s['set_number']}: {load_text} × {s['reps']} reps{rpe_txt}")

        if ex_name in prog_map:
            p = prog_map[ex_name]
            if p.get("has_previous"):
                diff_w = p.get("weight_diff")
                diff_vol = p.get("volume_diff")
                prog_parts = []
                if diff_w is not None and diff_w != 0:
                    sign = "+" if diff_w > 0 else ""
                    emoji = "🟢" if diff_w > 0 else "🔴"
                    prog_parts.append(f"{emoji} Peso máx: <b>{sign}{diff_w:.1f} kg</b>")
                if diff_vol is not None and diff_vol != 0:
                    sign = "+" if diff_vol > 0 else ""
                    prog_parts.append(f"📦 Volumen: <b>{sign}{diff_vol:.0f} kg</b>")

                if prog_parts:
                    lines.append(f"   <i>📈 Vs anterior: {' | '.join(prog_parts)}</i>")
                else:
                    lines.append("   <i>📈 Mismo peso y volumen que la sesión anterior.</i>")
            else:
                lines.append("   <i>✨ Primera vez registrada para este ejercicio.</i>")

        lines.append("")

    return "\n".join(lines).strip()
