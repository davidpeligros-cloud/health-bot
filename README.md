# 🏋️‍♂️ Health & Fitness Telegram Bot + Webhook

Bot personal de Telegram y servidor FastAPI para seguimiento de recomposición corporal (pérdida de grasa y mantenimiento/ganancia de masa muscular) con datos automáticos de Apple Health (Yazio, Hevy, Polar H10) mediante Atajos de iOS.

---

## 🚀 Novedades y Características

1. **🥩 Racha de Proteína (`/racha`)**:
   - Muestra tu racha actual de días consecutivos cumpliendo tu objetivo diario de proteína.
   - Registra tu récord histórico y te muestra el avance del día y mensajes motivacionales.
2. **🏋️‍♂️ Parser de Entrenamientos Hevy con Progresión**:
   - Pega directamente el texto compartido desde Hevy en el chat de Telegram o envíalo por webhook.
   - Extrae automáticamente ejercicios, series, pesos, repeticiones y RPE.
   - Reconoce series de peso corporal y conserva el lastre (`+kg`) por separado.
   - Calcula el volumen total de la sesión y la **progresión automática** (incremento de kilos y volumen) comparando cada ejercicio con su sesión anterior.
3. **❤️ Frecuencia Cardíaca Polar H10**:
   - Registra FC media y FC máxima de las sesiones de entrenamiento.
   - Se muestra en `/entreno` y en las alertas inmediatas.
4. **📱 Webhook tolerante a fallos para Atajos de iOS**:
   - Admite payloads planos o anidados.
   - Resuelve el campo `type` con inferencia automática (sensible y no sensible a mayúsculas: `workout`, `nutrition_daily`, `body_weight`).
   - Normaliza automáticamente fechas en español (ej. *"24 ago 2026, 18:30"*) y números decimales con comas (*"78,5"*).

---

## 📋 Comandos del Bot

| Comando | Descripción |
|---|---|
| `/racha` | Muestra tu racha actual y récord de días consecutivos cumpliendo la proteína |
| `/hoy` | Resumen del día (calorías, proteína, carbos, grasas, peso, racha) |
| `/semana` | Informe semanal de adherencia, peso medio, tendencia y entrenos |
| `/entreno` | Último entreno registrado con FC Polar H10, detalle de series y comparativa |
| `/hevy` | Ayuda e importación manual de rutinas de Hevy |
| `/objetivo` | Ver o ajustar objetivos (`/objetivo cal 1800 prot 160`) |
| `/consejo` | Solicitar análisis o recomendación a la IA |
| `/recuerdame` | Programar recordatorios (`/recuerdame 10:00 Tomar creatina`) |
| `/olvidar` | Limpiar la memoria del chat con la IA |
| `/backup` | Crear una copia manual de la base de datos |
| *Chat libre* | Escribe cualquier duda o pega un entreno de Hevy directamente |

---

## 🛠️ Configuración de Atajos de iOS (Shortcuts)

### 1. Nutrición (Yazio / Apple Health)
- **URL**: `http://TU-IP:8000/webhook/health-data`
- **Método**: `POST`
- **Cabeceras**: `X-Webhook-Token: TU_TOKEN`
- **JSON**:
```json
{
  "type": "nutrition_daily",
  "calories": 1850,
  "protein_g": 165,
  "carbs_g": 180,
  "fat_g": 55,
  "weight_kg": 78.5,
  "date": "2026-08-24"
}
```

### 2. Entrenamientos (Hevy + Polar H10)
- **JSON**:
```json
{
  "type": "workout",
  "name": "Torso Pesado",
  "duration_min": 60,
  "active_energy_kcal": 450,
  "avg_hr_bpm": 142,
  "max_hr_bpm": 174,
  "date": "2026-08-24"
}
```

También acepta `workout.exercises` con series estructuradas, o `workout.raw_text`
con el texto exportado de Hevy.

La base de datos se copia automáticamente cada día a `data/backups` a las 03:30
(hora de Madrid) y se conservan las últimas 14 copias. Puedes crear una copia
manual con `/backup`. El horario, directorio y retención se pueden cambiar con
`BACKUP_TIME`, `BACKUP_DIR` y `BACKUP_RETENTION_DAYS` en `.env`.

---

## 💻 Ejecución local

```bash
# Activar entorno virtual
.venv\Scripts\Activate.ps1

# Iniciar bot y webhook
python -m bot.main
```

## ✅ Comprobaciones locales

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m compileall -q bot
```
