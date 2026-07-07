#!/bin/bash
# Launched by com.tradebot.backend LaunchAgent.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
VENV="$BACKEND/.service-venv"
SITE_PACKAGES="$VENV/lib/python3.11/site-packages"

export PYTHONPATH="$BACKEND:$SITE_PACKAGES"
export PYTHONUNBUFFERED=1

cd "$BACKEND"
exec /usr/local/bin/python3.11 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
