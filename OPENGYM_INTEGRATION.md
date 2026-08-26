# 🏋️ Integración openGym: Mapeo Muscular y Análisis de Fatiga

## 🎯 Objetivo General

Enriquecer el bot actual **sin romper nada**: añadir análisis de fatiga muscular, mapeo de grupos trabajados y planificación basada en recuperación, aprovechando los entrenamientos que ya llegan de Hevy/Atajos.

---

## 📊 Componentes Implementados

### 1. **Módulo de Mapeo: `bot/muscle_groups.py`**

Define el mapeo bidireccional entre ejercicios y grupos musculares:

```python
EXERCISE_MUSCLE_MAP = {
    "press de banca": ["pecho", "hombros", "brazos"],
    "remo": ["espalda", "brazos"],
    "peso muerto": ["espalda", "glúteos", "isquiotibiales", "core"],
    # ... ~50+ ejercicios más
}
```

**Funciones principales:**
- `get_muscle_groups_for_exercise(name: str)` → Devuelve lista de grupos musculares
  - Búsqueda exacta
  - Fallback: búsqueda parcial (contiene)
  - Default: `["core"]` si no se encuentra
  
- `aggregate_muscle_fatigue(sets: list, date: str)` → Agrupa por músculo:
  ```python
  {
    "pecho": {
      "sets": 5,           # Total de series
      "volumen_kg": 2500,  # Peso × reps
      "reps": 45,
      "último_entreno": "2026-08-24"
    },
    "espalda": { ... }
  }
  ```

- `recovery_status(hours: float)` → Determina estado visual:
  - 🔴 < 24h: Muy fatigado
  - 🟠 24-48h: Fatigado
  - 🟡 48-72h: En recuperación
  - 🟢 72+h: Recuperado

---

### 2. **Lógica de Análisis: `bot/logic.py`**

#### `get_muscle_fatigue_map(days: int = 7)`
Analiza entrenamientos del período y devuelve:
```python
{
  "has_data": True,
  "period": "2026-08-17 → 2026-08-24",
  "muscles": {
    "pecho": {
      "emoji": "🫀",
      "sets": 5,
      "volumen_kg": 2500.0,
      "último_entreno": "2026-08-24",
      "horas_desde": 0,
      "status_emoji": "🔴",
      "status": "Muy fatigado"
    },
    ...
  },
  "summary": ["🫀 pecho: Muy fatigado (0h)", ...]
}
```

#### `get_exercise_library_insights(exercises: list)`
Detecta desbalances en la rutina:
```python
{
  "ejercicios_analizados": 7,
  "grupos_musculares_únicos": 8,
  "frecuencia_por_grupo": {
    "💪 brazos": 4,
    "🫀 pecho": 2,
    "🔙 espalda": 1
  },
  "más_trabajado": "💪 brazos",
  "menos_trabajado": "🔙 espalda",
  "detalle": { "Press de Banca": ["pecho", "hombros", "brazos"], ... }
}
```

---

### 3. **Comandos de Telegram**

#### `/musculos`
Análisis de qué grupos musculares trabajas y desbalances:
```
💪 Análisis de Grupos Musculares
Ejercicios únicos: 7
Grupos trabajados: 8

Frecuencia por grupo:
💪 brazos: ▓▓▓▓░░░░░░ (4)
🫀 pecho: ▓▓░░░░░░░░ (2)
🔙 espalda: ▓░░░░░░░░░ (1)

⭐ Más trabajado: 💪 brazos
⚠️ Menos trabajado: 🔙 espalda
```

#### `/fatiga` (o `/fatiga 14` para 14 días)
Mapa visual de recuperación muscular:
```
🏋️ Mapa de Fatiga/Recuperación (últimos 7 días)
Período: 2026-08-17 → 2026-08-24

Estado por grupo muscular:
🫀 Pecho: 🔴 Muy fatigado (0h) · 5 series, 2500 kg
🔙 Espalda: 🟠 Fatigado (24h) · 3 series, 1800 kg
💪 Brazos: 🟢 Recuperado (72h) · 8 series, 2100 kg
```

---

## 🔄 Flujo de Datos: Intacto

```
iOS Atajos
    ↓
Webhook FastAPI (/webhook/health-data)
    ↓
Parser Hevy + Normalización
    ↓
SQLite: workout_exercises table
    ↓
Telegram Commands (existentes + nuevos)
```

**Lo que NO cambió:**
- ✅ Atajos de iOS → Webhook: Idéntico
- ✅ Parser de Hevy: Sigue soportando formato inline y texto normal
- ✅ Comandos `/hoy`, `/semana`, `/temporada`: Sin cambios
- ✅ `/consejo` IA: Sin cambios (próxima integración opcional)

---

## 🗄️ Base de Datos

**Tablas utilizadas:**
- `workout_exercises` - Existente, sin cambios de schema
  - `exercise_name`, `weight_kg`, `reps`, `date`, etc.

**No se crearon tablas nuevas** para esta integración.

---

## 📝 Cambios de Archivos Realizados

| Archivo | Cambios |
|---------|---------|
| `bot/muscle_groups.py` | ✨ **NUEVO**: Mapeo y utilidades |
| `bot/logic.py` | `+get_muscle_fatigue_map()`, `+get_exercise_library_insights()` |
| `bot/telegram_handlers.py` | `+cmd_musculos()`, `+cmd_fatiga()`, registración en handlers |
| `README.md` | Documentación de `/musculos` y `/fatiga` |
| `tests/test_health_features.py` | 6 tests nuevos (muscle_groups, recovery, library) |

---

## 🚀 Roadmap Futuro

### Fase 2: Integración con `/consejo` IA
```python
# ai_advice.py: build_ai_context() mejorado
muscle_fatigue = await logic.get_muscle_fatigue_map()
context["muscle_recovery"] = muscle_fatigue
# Groq: "Considera que los brazos están muy fatigados..."
```

### Fase 3: Recomendaciones de Entrenamiento
- `/entrena_hoy` → Sugiere ejercicios según grupos recuperados
- Plantillas de rutina por nivel de fatiga

### Fase 4: Historial de Tendencias
- Gráficas de frecuencia muscular semanal/mensual
- Detección automática de desbalances patrones

---

## ✅ Validación

**Tests implementados:**
```
✓ test_muscle_groups_maps_exercises_correctly
✓ test_muscle_fatigue_aggregation
✓ test_recovery_status_determination
✓ test_muscle_fatigue_map_async
✓ test_exercise_library_insights
✓ 9 tests previos continúan pasando
```

**Compilación:**
```
python -m compileall -q bot tests
# ✓ Sin errores
```

**Ejecución:**
```
python -m unittest discover -s tests
# ✓ 14 tests OK
```

---

## 🎮 Ejemplo de Uso Completo

1. **Usuario entrena en Hevy:**
   - Pecho, Espalda, Brazos (7 ejercicios, 21 series)
   - Envía a Telegram o por Atajos

2. **Bot recibe y parsea:**
   - Crea `workout_exercises` records
   - Asocia cada ejercicio a grupos musculares via `muscle_groups.py`

3. **Usuario solicita `/musculos`:**
   - Bot consulta todos los ejercicios históricos
   - Agrupa por grupo muscular
   - Detecta que brazos domina (4 ejercicios vs espalda 1)
   - Responde con análisis visual

4. **Usuario solicita `/fatiga`:**
   - Bot consulta última semana
   - Calcula horas desde último entreno por grupo
   - Devuelve mapa de recuperación con emojis
   - Usuario sabe: "brazos están muertos, me toca espalda hoy"

5. **Próximo paso:** `/consejo` integra el contexto de fatiga en sus recomendaciones de IA

---

## 📌 Notas Técnicas

- **Compatibilidad:** Totalmente backward-compatible con Hevy existente
- **Performance:** Consultas O(n) sobre `workout_exercises`, no hay n+1
- **Extensibilidad:** Fácil añadir ejercicios nuevos a `EXERCISE_MUSCLE_MAP`
- **Localización:** Mapeo en español, pero `recovery_status()` y emojis universales
- **Timezone:** Usa `Europe/Madrid` vía `_today()` en logic.py

---

## 📚 Referencias

- `openGym`: Proyecto de biblioteca de ejercicios (GitLab: DuarteSantos8/openGym)
- Hevy: App de entrenamientos con export de texto e inline series
- Telegram Python: python-telegram-bot para handlers
- SQLite: aiosqlite async para consultas no-bloqueantes

---

**Estado:** ✅ Implementado, testeado, listo para producción.
