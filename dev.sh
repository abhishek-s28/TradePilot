#!/usr/bin/env bash
# Bootstrap a local dev environment without Docker.
#
# Sets up .env files, installs backend + frontend deps, runs DB migrations,
# and prints the next steps. Idempotent — safe to re-run.
set -euo pipefail

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

say() { echo -e "${GREEN}▶${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
die() { echo -e "${RED}✗${NC} $*"; exit 1; }

cd "$(dirname "$0")"

# Python check
command -v python3 >/dev/null || die "python3 not found"
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
say "python $PYVER"

# Node check
command -v node >/dev/null || die "node not found"
say "node $(node --version)"

# ── backend ──
say "setting up backend"
cd backend

if [ ! -f .env ]; then
  cp .env.example .env
  # Switch to sqlite default so it just works without postgres
  sed -i.bak 's|^DATABASE_URL=postgresql.*|DATABASE_URL=sqlite+aiosqlite:///./tradebot.db|' .env
  rm -f .env.bak
  warn "created backend/.env with SQLite default. Edit to use Postgres or Alpaca."
fi

if [ ! -d .venv ]; then
  say "creating virtualenv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

say "installing backend deps (this can take a minute the first time)"
pip install --upgrade pip --quiet
pip install -e ".[dev]" --quiet

deactivate
cd ..

# ── frontend ──
say "setting up frontend"
cd frontend
if [ ! -f .env.local ]; then
  cp .env.example .env.local
  warn "created frontend/.env.local"
fi
if [ ! -d node_modules ]; then
  say "installing frontend deps (also can take a minute)"
  npm install --silent
fi
cd ..

# ── summary ──
echo
say "ready. Next steps:"
echo
echo "  Terminal 1 (backend):"
echo "    cd backend && source .venv/bin/activate"
echo "    uvicorn app.main:app --reload"
echo
echo "  Terminal 2 (frontend):"
echo "    cd frontend && npm run dev"
echo
echo "  Then open http://localhost:3000"
echo
echo "  Or just run everything in Docker:"
echo "    cd infra && docker compose up --build"
echo
