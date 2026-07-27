# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build-Stufe: Abhaengigkeiten in ein virtuelles Environment installieren.
# Die Trennung haelt das Laufzeit-Image klein und frei von Build-Werkzeugen.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Zuerst nur die Metadaten kopieren, damit der Layer-Cache bei Codeaenderungen haelt.
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip && pip install .

# ---------------------------------------------------------------------------
# Laufzeit-Stufe
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

# curl wird vom Healthcheck benoetigt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Anwendung laeuft nicht als root.
RUN groupadd --system --gid 1000 oracle \
    && useradd --system --uid 1000 --gid oracle --create-home oracle

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY --chown=oracle:oracle app ./app
COPY --chown=oracle:oracle alembic ./alembic
COPY --chown=oracle:oracle scripts ./scripts
COPY --chown=oracle:oracle alembic.ini pyproject.toml README.md ./

USER oracle

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
