# Health Bot — Bot de Telegram para seguimiento nutricional y entrenamiento

Bot personal que recibe datos de Apple Health (vía Atajos iOS), los analiza y te envía resúmenes y consejos en Telegram para ayudarte en tu proceso de recomposición corporal.

## Requisitos

- Python 3.11+
- Una cuenta de Telegram
- Acceso a la API de Claude (Anthropic)
- iPhone con la app **Yazio** y/o **Hevy** (que exportan a Apple Health) y acceso a **Atajos**

---

## Instalación rápida

### 1. Clonar el repositorio e instalar dependencias

```bash
git clone <tu-repo>
cd <tu-repo>

# Crear entorno virtual (recomendado)
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
.venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
```

Edita `.env` con tus valores reales:

| Variable | Descripción |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token de tu bot (obtenido con @BotFather) |
| `TELEGRAM_CHAT_ID` | Tu chat ID personal (usa @userinfobot) |
| `WEBHOOK_TOKEN` | Token secreto para autenticar Atajos |
| `ANTHROPIC_API_KEY` | Tu clave de API de Anthropic |
| `TDEE_ESTIMATE` | Tu TDEE estimado en kcal/día |

#### Cómo crear tu bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Escribe `/newbot`
3. Sigue las instrucciones: elige nombre y username (debe terminar en `bot`)
4. Copia el token que te da y pégalo en `TELEGRAM_BOT_TOKEN`
5. Para obtener tu `TELEGRAM_CHAT_ID`: busca **@userinfobot** en Telegram y escríbele, te responderá con tu ID

#### Generar un WEBHOOK_TOKEN seguro

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Arrancar el bot

```bash
python -m bot.main
```

Deberías ver en la consola que el servidor webhook arranca en el puerto 8000 y el bot comienza el polling. Recibirás un mensaje en Telegram confirmando el inicio.

---

## Probar el webhook con curl

Antes de configurar Atajos, verifica que el servidor funciona:

```bash
# 1. Ping (sin autenticación)
curl http://localhost:8000/webhook/ping

# 2. Enviar nutrición de prueba
curl -X POST http://localhost:8000/webhook/health-data \
  -H "X-Webhook-Token: TU_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "nutrition_daily",
    "date": "2026-08-23",
    "nutrition": {
      "calories": 1850,
      "protein_g": 145,
      "carbs_g": 180,
      "fat_g": 55
    }
  }'

# 3. Enviar entreno de prueba
curl -X POST http://localhost:8000/webhook/health-data \
  -H "X-Webhook-Token: TU_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "workout",
    "date": "2026-08-23",
    "workout": {
      "duration_min": 62,
      "name": "Push Day",
      "active_energy_kcal": 410
    }
  }'

# 4. Enviar peso corporal
curl -X POST http://localhost:8000/webhook/health-data \
  -H "X-Webhook-Token: TU_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "body_weight",
    "date": "2026-08-23",
    "body": { "weight_kg": 82.4 }
  }'

# 5. Ver estado actual
curl -H "X-Webhook-Token: TU_WEBHOOK_TOKEN" \
  http://localhost:8000/webhook/status
```

Tras enviar el entreno, deberías recibir inmediatamente un mensaje en Telegram con la confirmación.

---

## Comandos del bot

| Comando | Descripción |
|---|---|
| `/ping` | Verifica que el bot está activo |
| `/hoy` | Calorías y proteína consumidas hoy vs. objetivo |
| `/semana` | Resumen semanal: peso, adherencia, entrenamientos |
| `/objetivo` | Ver objetivos actuales |
| `/objetivo cal 1700 prot 160` | Editar objetivos (también: `carbs`, `gras`, `tdee`) |
| `/entreno` | Último entreno + comparación con el anterior del mismo tipo |
| `/consejo` | Consejo personalizado generado por IA (Claude) |

---

## Configurar Atajos (Shortcuts) en iPhone — Guía paso a paso

Esta es la parte que conecta Apple Health con tu bot. Crearás una automatización que se ejecuta automáticamente cada noche y envía tus datos del día al webhook.

### Requisitos previos

- Yazio registrando tu comida diaria y exportando a Apple Health (activar en Yazio: Ajustes → Salud → Sincronizar con Apple Health)
- Hevy registrando tus entrenos y exportando a Apple Health (activar en Hevy: Perfil → Integraciones → Apple Health)
- Tu servidor debe ser accesible desde el iPhone:
  - **En la misma red WiFi**: usa la IP local de tu ordenador (ej. `192.168.1.10`)
  - **Desde cualquier red**: necesitas exponer el puerto 8000 con un servicio como [ngrok](https://ngrok.com/) o configurar tu router

### Atajo 1: Enviar nutrición diaria

1. Abre **Atajos** → toca **+** para nuevo atajo
2. Añade la acción **"Obtener muestras de salud"**:
   - Tipo: **Calorías activas** → guarda en variable `CaloriasActivas`
   - *(repite para cada métrica que necesites)*
3. Más fácil: usa **"Calcular estadísticas"** sobre muestras de Energía alimentaria:
   - Tipo: **Energía alimentaria**
   - Intervalo: **Hoy**
   - Estadística: **Suma**
   - Guarda resultado en variable `TotalCalorias`
4. Repite el paso 3 para:
   - **Proteínas** → `TotalProteina`
   - **Carbohidratos** → `TotalCarbs`
   - **Grasas totales** → `TotalGrasa`
5. Añade acción **"Diccionario"** y construye el JSON:
   ```
   type: "nutrition_daily"
   date: [Fecha actual en formato "yyyy-MM-dd"]
   nutrition:
     calories: [TotalCalorias]
     protein_g: [TotalProteina]
     carbs_g: [TotalCarbs]
     fat_g: [TotalGrasa]
   ```
6. Añade acción **"Obtener contenido de URL"**:
   - URL: `http://TU_IP:8000/webhook/health-data`
   - Método: **POST**
   - Cabeceras: `X-Webhook-Token` = `TU_WEBHOOK_TOKEN`
   - Cuerpo de solicitud: **JSON** → selecciona el Diccionario del paso 5
7. (Opcional) Añade **"Mostrar resultado"** para ver la respuesta
8. Guarda el atajo con el nombre "Enviar nutrición a bot"

### Atajo 2: Enviar peso corporal

1. Nuevo atajo → acción **"Obtener muestras de salud"**:
   - Tipo: **Peso corporal**
   - Ordenar por: **Fecha de inicio, descendente**
   - Límite: 1
   - Guarda en `UltimoPeso`
2. Construye diccionario:
   ```
   type: "body_weight"
   date: [Fecha actual "yyyy-MM-dd"]
   body:
     weight_kg: [UltimoPeso]  ← convierte a kg si está en lb
   ```
3. Mismo paso de **"Obtener contenido de URL"** que el Atajo 1

> **Nota sobre unidades**: Apple Health puede devolver el peso en kg o lb según tu configuración regional. Para asegurarte, añade una acción **"Convertir"** entre el resultado y el diccionario: convierte el valor a **kilogramos**.

### Automatizar la ejecución

1. En Atajos → pestaña **"Automatización"** → **+** → **"Automatización personal"**
2. Elige **"Hora del día"**:
   - Hora: **21:30** (o cuando suelas haber terminado de cenar)
   - Repetir: **Todos los días**
3. Acción: **"Ejecutar atajo"** → selecciona "Enviar nutrición a bot"
4. Desactiva **"Preguntar antes de ejecutar"** para que corra en segundo plano
5. Repite con el atajo de peso corporal

### Atajo para entrenos (manual o automático)

Los entrenos de Hevy se sincronizan con Apple Health como "Entrenamiento". Para enviarlos:

1. Nuevo atajo → **"Obtener muestras de salud"**:
   - Tipo: **Entrenamientos** (Workouts)
   - Filtrar por: Fecha de inicio = Hoy
   - Ordenar: Más reciente primero
   - Límite: 1
2. Usa **"Obtener detalles del entrenamiento"** para extraer:
   - Nombre del entrenamiento → `NombreEntreno`
   - Duración → `DuracionEntreno` (en minutos)
   - Calorías activas → `CaloriasEntreno`
3. Construye el diccionario y envíalo como los anteriores con `type: "workout"`

> **Tip**: Para los entrenos, también puedes ejecutar el atajo manualmente justo después de terminar en el gym, pulsando el widget de Atajos.

---

## Estructura del proyecto

```
/
├── bot/
│   ├── main.py              # Punto de entrada: arranca todo
│   ├── config.py            # Variables de entorno y configuración
│   ├── db.py                # Acceso a SQLite (async)
│   ├── webhook.py           # Endpoints FastAPI
│   ├── telegram_handlers.py # Comandos del bot
│   ├── scheduler_jobs.py    # Jobs programados
│   ├── logic.py             # Cálculos y resúmenes
│   └── ai_advice.py         # Consejos con Claude
├── data/                    # Creado automáticamente — contiene health_bot.db
├── requirements.txt
├── .env.example
└── README.md
```

---

## Despliegue en un VPS

Para que funcione desde cualquier red (no solo WiFi local):

```bash
# Instalar en el VPS como servicio systemd (Linux)
sudo nano /etc/systemd/system/healthbot.service
```

```ini
[Unit]
Description=Health Tracking Telegram Bot
After=network.target

[Service]
Type=simple
User=tu_usuario
WorkingDirectory=/ruta/al/proyecto
EnvironmentFile=/ruta/al/proyecto/.env
ExecStart=/ruta/al/proyecto/.venv/bin/python -m bot.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable healthbot
sudo systemctl start healthbot
sudo systemctl status healthbot
```

En Atajos, cambia la URL de `http://IP_LOCAL:8000` a `http://IP_VPS:8000`.

---

## Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| 401 Unauthorized | Token incorrecto | Verifica `X-Webhook-Token` == `WEBHOOK_TOKEN` en `.env` |
| El bot no responde | `TELEGRAM_BOT_TOKEN` inválido | Revisa el token con @BotFather |
| No llegan datos de Atajos | IP o puerto incorrecto | Prueba primero con `curl` desde el mismo dispositivo |
| Errores de Claude | API key inválida o sin saldo | Verifica en console.anthropic.com |
| El peso no actualiza targets | Diferencia < 1 kg | Normal; solo recalcula si cambia más de 1 kg |
