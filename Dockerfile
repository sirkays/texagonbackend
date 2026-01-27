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

COPY . .

# copy entrypoint and make it executable
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# non-root user
RUN useradd -m -u 10001 django && chown -R django:django /app
USER django

# IMPORTANT:
# - Do NOT hardcode PORT here (Render injects PORT dynamically)
# - Keep settings module only
ENV DJANGO_SETTINGS_MODULE=texagonbackend.settings

# healthcheck (use PORT; Render will set it, local defaults come from entrypoint)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import os, socket; p=int(os.getenv('PORT','8000')); s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', p))"

ENTRYPOINT ["/app/entrypoint.sh"]

# default command (used for web; entrypoint switches by PROCESS_TYPE)
CMD ["gunicorn", "texagonbackend.wsgi:application"]
