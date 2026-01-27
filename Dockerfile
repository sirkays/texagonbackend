# ---- Build stage ------------------------------------------------------------
FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# system deps for psycopg2, Pillow, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential libpq-dev libjpeg62-turbo-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements*.txt ./
RUN pip install --upgrade pip && pip wheel --wheel-dir=/wheels -r requirements.txt


# ---- Run stage --------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/*

# Copy app source
COPY . .

# Copy entrypoint script (must exist in repo root)
COPY entrypoint.sh /app/entrypoint.sh

# Make it executable
RUN chmod +x /app/entrypoint.sh

# non-root
RUN useradd -m -u 10001 django && chown -R django:django /app
USER django

ENV DJANGO_SETTINGS_MODULE=texagonbackend.settings \
    PORT=8000 \
    GUNICORN_CMD_ARGS="--bind 0.0.0.0:8000 --workers 3 --threads 2 --timeout 60"

# healthcheck (works for web only; workers won't have a listening port, which is OK on Render)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import os, socket; p=int(os.environ.get('PORT','8000')); s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', p))"

# IMPORTANT:
# - Do NOT run migrate/collectstatic in CMD anymore (Render uses preDeployCommand)
# - entrypoint.sh will decide what to run based on PROCESS_TYPE
CMD ["/app/entrypoint.sh"]
