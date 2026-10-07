#!/bin/bash
set -e

echo "=== Sentinel Deployment Script ==="
echo "Tamil Broadcast AI Security Platform"
echo "======================================"

# Check requirements
echo "[1/7] Checking requirements..."
command -v python3 >/dev/null || { echo "ERROR: Python 3 required"; exit 1; }
command -v node >/dev/null || { echo "ERROR: Node.js required"; exit 1; }
command -v psql >/dev/null || { echo "ERROR: PostgreSQL required"; exit 1; }
command -v ollama >/dev/null || { echo "ERROR: Ollama required. Install from ollama.ai"; exit 1; }
echo "  ✓ All requirements found"

# Check .env exists
echo "[2/7] Checking configuration..."
if [ ! -f api/.env ]; then
  echo "ERROR: api/.env not found. Copy env.example to api/.env and fill in values."
  exit 1
fi
echo "  ✓ Configuration found"

# Database setup
echo "[3/7] Setting up database..."
if psql -U postgres -c "SELECT 1 FROM pg_database WHERE datname='sentinel'" | grep -q 1; then
  echo "  ✓ Database already exists"
else
  echo "  Creating database..."
  sudo -u postgres psql -f database/db_setup.sql
  echo "  ✓ Database created"
fi

# API venv
echo "[4/7] Setting up API environment..."
if [ ! -d api/.venv ]; then
  python3 -m venv api/.venv
fi
api/.venv/bin/pip install -r api/requirements.txt -q
echo "  ✓ API environment ready"

# Agents venv
echo "[5/7] Setting up agents environment..."
if [ ! -d agents/.venv ]; then
  python3 -m venv agents/.venv
fi
agents/.venv/bin/pip install -r agents/requirements.txt -q
echo "  ✓ Agents environment ready"

# Dashboard
echo "[6/7] Building dashboard..."
cd dashboard && npm install -q && npm run build -q && cd ..
echo "  ✓ Dashboard built"

# Start
echo "[7/7] Starting Sentinel..."
cd api && nohup .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /tmp/sentinel-api.log 2>&1 &
echo "  API started → http://localhost:8000"
cd ../dashboard && nohup npm run start > /tmp/sentinel-dashboard.log 2>&1 &
echo "  Dashboard started → http://localhost:3000"

echo ""
echo "======================================"
echo "Sentinel is running!"
echo "Dashboard: http://localhost:3000"
echo "API:       http://localhost:8000"
echo "Logs:      tail -f /tmp/sentinel-api.log"
echo "======================================"
