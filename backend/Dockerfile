# One image for both the API and the worker (Cloud Run sets the command per service).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /srv
COPY pyproject.toml ./
# Installs the dependencies listed in pyproject.toml (plus web push support).
RUN pip install --upgrade pip && pip install . pywebpush
COPY . .

RUN useradd --create-home appuser
USER appuser

# API by default: bring the database schema up to date, then serve. Running migrations here (not only as a
# platform "pre-deploy" step) means a missing dashboard setting can't leave the database empty. Safe with one
# API replica; `alembic upgrade head` is a no-op when nothing is pending.
# The worker service overrides this with: python -m app.worker --schedule
CMD alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
