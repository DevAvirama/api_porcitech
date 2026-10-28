FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV YOLO_CONFIG_DIR=/tmp/Ultralytics

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libxcb1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar PyTorch CPU ligero primero (evita descargar 4GB de librerias CUDA innecesarias)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y opencv-python opencv-python-headless && \
    pip install --no-cache-dir --no-deps opencv-python-headless

COPY . .

EXPOSE 8000

# Inicializa/verifica el esquema de base de datos de forma idempotente antes de levantar la API
CMD ["sh", "-c", "python scripts/init_db.py && uvicorn main:app --host 0.0.0.0 --port 8000"]
