# Local Setup

## Prerequisites
- macOS
- Node.js and npm installed
- Python 3.11+ installed
- Git installed
- Docker is optional; not required when using local Python/Node development

## Current status
- Docker is not installed / unavailable on this machine
- Python 3.9 is present but the project requires Python 3.11+
- Homebrew is available and can install Python 3.11

## Local development commands
1. Install Python 3.11 if needed:
   ```bash
   brew install python@3.11
   ```

2. Backend setup:
   ```bash
   cd backend
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

3. Frontend setup:
   ```bash
   cd ../frontend
   npm install
   ```

4. Run the backend:
   ```bash
   cd backend
   source .venv/bin/activate
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

5. Run the frontend:
   ```bash
   cd frontend
   npm run dev
   ```

6. Open in browser:
   - Frontend: `http://localhost:3000`
   - Backend docs: `http://localhost:8000/docs`
   - Health: `http://localhost:8000/health`

## Tests
- Run backend tests:
  ```bash
  cd backend
  pytest -v
  ```

## Docker alternative
If Docker Desktop is installed later, use:
```bash
cd infra
docker compose up --build
```
