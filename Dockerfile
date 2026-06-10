# ── Stage 1: Build ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Instalar dependencias del sistema necesarias para compilar paquetes
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias en un directorio separado
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Metadata
LABEL maintainer="diego.guzman"
LABEL description="Python WhatsApp Bot - Flask API"

# Variables de entorno para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Crear usuario no-root para mayor seguridad
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

# Copiar dependencias instaladas desde el stage builder
COPY --from=builder /install /usr/local

# Copiar el código fuente de la aplicación
COPY --chown=appuser:appgroup . .

# Cambiar al usuario no-root
USER appuser

# Exponer el puerto que usa Flask (definido en run.py)
EXPOSE 8000

# Health check para Dokploy
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Comando de inicio con Gunicorn (producción) en lugar del servidor de desarrollo de Flask
# Gunicorn: workers = (2 * CPUs) + 1  →  ajusta según tu servidor
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "run:app"]
