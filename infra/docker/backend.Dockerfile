# Backend image for the tradebot API + scheduler.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps. build-essential is needed for some wheels on slim; curl for healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy project files and install in one layer (CI can split for caching).
COPY backend/pyproject.toml /app/pyproject.toml
COPY backend/app /app/app

RUN pip install --upgrade pip \
    && pip install .

# Strip build deps to slim the final image
RUN apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && apt-get clean

# Optional: include migrations if present (copied with fallback so build won't fail
# before alembic is initialised).
COPY backend/alembic.ini* /app/
COPY backend/alembic /app/alembic

# Non-root runtime user
RUN useradd -r -u 10001 -m tradebot \
    && chown -R tradebot:tradebot /app
USER tradebot

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Default command runs the API. Worker uses the same image with a different command
# (`python -m app.workers.scheduler`) configured in docker-compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
