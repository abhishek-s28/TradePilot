# Tradebot Makefile.
# Common dev commands. Run from repo root.

.PHONY: help install backend frontend test lint migrate up down logs reset clean service-install service-uninstall service-status

help:
	@echo "Tradebot dev commands:"
	@echo "  make install      install backend + frontend deps"
	@echo "  make backend      run the FastAPI backend (uvicorn --reload)"
	@echo "  make frontend     run the Next.js dev server"
	@echo "  make test         run backend tests"
	@echo "  make lint         run ruff + tsc"
	@echo "  make migrate      run Alembic migrations"
	@echo "  make up           docker compose up (full stack)"
	@echo "  make down         docker compose down"
	@echo "  make logs         tail docker compose logs"
	@echo "  make reset        wipe paper account via API (backend must be running)"
	@echo "  make service-install   install macOS background backend service"
	@echo "  make service-status    show macOS backend service status"
	@echo "  make service-uninstall remove macOS background backend service"
	@echo "  make clean        remove __pycache__, .pytest_cache, node_modules"

install:
	cd backend && python -m pip install -e ".[dev]"
	cd frontend && npm install

backend:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && pytest -v

lint:
	cd backend && ruff check app tests
	cd frontend && npm run typecheck

migrate:
	cd backend && alembic upgrade head

up:
	cd infra && docker compose up --build

down:
	cd infra && docker compose down

logs:
	cd infra && docker compose logs -f --tail=200

reset:
	curl -X POST http://localhost:8000/paper/reset

service-install:
	./scripts/install_macos_launch_agent.sh

service-status:
	./scripts/status_macos_launch_agent.sh

service-uninstall:
	./scripts/uninstall_macos_launch_agent.sh

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	rm -rf frontend/node_modules frontend/.next
