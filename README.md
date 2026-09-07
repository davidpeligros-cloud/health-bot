# 🏋️‍♂️ Health & Fitness Telegram Bot + Webhook

Bot personal de Telegram y servidor FastAPI para seguimiento de recomposición corporal (pérdida de grasa y ganancia de masa muscular) con datos automáticos de Apple Health (Yazio, Hevy, Polar H10) mediante Atajos de iOS y un entrenador con IA basado en evidencia científica.

---

## 🚀 Características y Novedades

1. **🏆 Récords Personales y 1RM Estimado (`/records` / `/prs`)**:
   - Calcula tu 1RM estimado (fórmula de Epley) para cada ejercicio.
   - Detecta automáticamente cuando rompes un récord personal al subir un entreno y te avisa con una felicitación especial.
2. **📊 Volumen Semanal por Grupo Muscular (`/volumen`)**:
   - Clasifica tus series por grupo muscular (Pecho, Espalda, Cuádriceps, Isquios/Glúteo, Hombros, Brazos, Core).
   - Compara tus series efectivas con las referencias científicas de hipertrofia (*Schoenfeld et al.*, 10-20 series semanales).
3. **🍳 Sugerencias Inteligentes de Comida (`/quecomo`, `/cena`, `/comida`)**:
   - Analiza en tiempo real las calorías y proteína que te faltan hoy y te propone 3 recetas rápidas, deliciosas y con ingredientes sencillos adaptadas exactamente a tus números.
4. **📈 Gráficos Visuales de Progreso (`/grafica`, `/progreso`)**:
   - Envía a Telegram una imagen con la curva de peso con media móvil de 7 días y barras de calorías y proteína vs objetivos.
5. **🥩 Racha de Proteína (`/racha`)**:
   - Contador de días consecutivos cumpliendo tu meta de proteína y récord histórico.
6. **🏋️‍♂️ Parser de Hevy con Progresión**:
   - Pega tu rutina directamente en Telegram: detecta ejercicios, series, pesos, repeticiones, RPE y calcula la progresión contra tu sesión anterior.
7. **❤️ Pulsaciones Polar H10**:
   - Registra frecuencia cardíaca media y máxima en cada entrenamiento.

---

## 📋 Comandos del Bot

| Comando | Descripción |
|---|---|
| `/racha` | Muestra tu racha actual y récord histórico de proteína |
| `/records` o `/prs` | Lista tus récords personales y 1RM estimado por ejercicio |
| `/volumen` | Series semanales por grupo muscular y estado de hipertrofia |
| `/quecomo` o `/cena` | 3 opciones de comida/cena ajustadas a tus macros restantes |
| `/grafica` | Gráfico de evolución de peso y adherencia nutricional |
| `/hoy` | Resumen del día (calorías, proteína, carbos, grasas, peso, racha) |
| `/semana` | Informe semanal de adherencia, peso medio, tendencia y entrenos |
| `/entreno` | Último entreno con FC Polar H10, series y comparativa |
| `/hevy` | Ayuda e importación manual de rutinas de Hevy |
| `/objetivo` | Ver o ajustar objetivos (`/objetivo cal 1800 prot 135`) |
| `/consejo` | Consejo personalizado del entrenador IA |
| `/recuerdame` | Programar recordatorios (`/recuerdame 10:00 Tomar creatina`) |
| `/olvidar` | Limpiar la memoria del chat con la IA |
| *Chat libre* | Habla con el entrenador IA o pega un entreno de Hevy directamente |

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
  "calories": 1800,
  "protein_g": 135,
  "carbs_g": 180,
  "fat_g": 55,
  "weight_kg": 81.5,
  "date": "2026-09-07"
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
  "date": "2026-09-07"
}
```

---

## 💻 Ejecución local

```bash
# Activar entorno virtual
.venv\Scripts\Activate.ps1

# Iniciar bot y webhook
python -m bot.main
```
