#!/usr/bin/env bash
# SPY DayTrader - Setup & Run Script
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=============================="
echo "  SPY DayTrader Setup"
echo "=============================="

# --- Backend Setup ---
echo ""
echo "[1/4] Setting up Python backend..."
cd "$PROJECT_DIR/backend"

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  Created virtual environment"
fi

source venv/bin/activate
pip install -q -r requirements.txt
echo "  Python dependencies installed"

# --- Frontend Setup ---
echo ""
echo "[2/4] Setting up React frontend..."
cd "$PROJECT_DIR/frontend"

if [ ! -d "node_modules" ]; then
    npm install
    echo "  Node dependencies installed"
else
    echo "  Node modules already installed"
fi

# --- Environment Check ---
echo ""
echo "[3/4] Checking configuration..."
cd "$PROJECT_DIR/backend"

if [ ! -f ".env" ]; then
    echo "  WARNING: No .env file found! Copy .env.example and configure."
else
    echo "  .env file found"
fi

# --- Start Instructions ---
echo ""
echo "[4/4] Ready to run!"
echo ""
echo "To start the backend:"
echo "  cd $PROJECT_DIR/backend"
echo "  source venv/bin/activate"
echo "  uvicorn app.main:app --reload --port 8000"
echo ""
echo "To start the frontend (in another terminal):"
echo "  cd $PROJECT_DIR/frontend"
echo "  npm run dev"
echo ""
echo "Dashboard will be at: http://localhost:5173"
echo "API docs at: http://localhost:8000/docs"
echo ""
echo "=============================="
echo "  Configuration Checklist:"
echo "  [ ] Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET in backend/.env"
echo "  [ ] Run Schwab OAuth flow to generate token file"
echo "  [ ] Run a backtest first: POST /api/backtest/run"
echo "  [ ] Paper trade for 2-4 weeks before going live"
echo "=============================="
