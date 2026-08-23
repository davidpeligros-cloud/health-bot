FROM python:3.10-slim

WORKDIR /code

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copiar requerimientos e instalarlos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código del bot
COPY . /code/

# Crear directorio de datos con permisos adecuados para la base de datos SQLite
RUN mkdir -p /code/data && chmod 777 /code/data

# Hugging Face Spaces expone por defecto el puerto 7860
ENV PORT=7860
EXPOSE 7860

# Comando para arrancar tu aplicación principal
CMD ["python", "-m", "bot.main"]