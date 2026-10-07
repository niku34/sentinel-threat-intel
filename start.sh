#!/bin/bash

echo "Starting Sentinel..."

# Embedding server (loads all-MiniLM-L6-v2 once — stays warm between Veriff requests)
cd ~/sentinel && agents/.venv/bin/python agents/veriff/embedding_server.py > /tmp/embedding-server.log 2>&1 &
EMBED_PID=$!
echo "Embedding server started (PID $EMBED_PID) — model loading ~10s, log: /tmp/embedding-server.log"

# API server
cd ~/sentinel/api
nohup .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload > /tmp/sentinel-api.log 2>&1 &
API_PID=$!
echo "API server started (PID $API_PID) → http://localhost:8000"

# Frontend
cd ~/sentinel/dashboard
nohup npm run dev > /tmp/sentinel-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "Frontend started (PID $FRONTEND_PID) → http://localhost:3000"

echo ""
echo "Both servers running. Logs:"
echo "  API:      tail -f /tmp/sentinel-api.log"
echo "  Frontend: tail -f /tmp/sentinel-frontend.log"
echo ""
echo "To stop everything: kill $API_PID $FRONTEND_PID"
