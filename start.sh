#!/usr/bin/env bash
# SPY DayTrader - Universal Startup Script
# Works on macOS, Linux, and WSL (Windows Subsystem for Linux)
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║         SPY DayTrader - Paper Trading         ║"
echo "  ║      Institutional-Grade Day Trading Bot      ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}"

cleanup() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Check Python ─────────────────────────────────────────────────────────
echo -e "${CYAN}[1/5] Checking Python...${NC}"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_VERSION=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        PY_MAJOR=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        PY_MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 10 ]; then
            PYTHON="$cmd"
            echo -e "  ${GREEN}Found $cmd ($PY_VERSION)${NC}"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "  ${RED}Python 3.10+ is required. Install from https://python.org${NC}"
    exit 1
fi

# ── Check Node.js ────────────────────────────────────────────────────────
echo -e "${CYAN}[2/5] Checking Node.js...${NC}"

# Source nvm if available (common on macOS/Linux)
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
# Also check fnm
if command -v fnm &>/dev/null; then
    eval "$(fnm env --use-on-cd)" 2>/dev/null
fi
# Check for locally installed node
for nd in "$HOME/.local/node-"*/bin; do
    [ -x "$nd/node" ] && export PATH="$nd:$PATH" && break
done

if command -v node &>/dev/null; then
    NODE_VERSION=$(node -v)
    echo -e "  ${GREEN}Found node ($NODE_VERSION)${NC}"
elif [ -x "$FRONTEND_DIR/node_modules/.bin/vite" ]; then
    echo -e "  ${YELLOW}Node not in PATH but vite found locally${NC}"
else
    echo -e "  ${RED}Node.js is required. Install from https://nodejs.org${NC}"
    exit 1
fi

# ── Backend Setup ────────────────────────────────────────────────────────
echo -e "${CYAN}[3/5] Setting up backend...${NC}"
cd "$BACKEND_DIR"

# Create venv if missing
if [ ! -d "venv" ]; then
    echo -e "  Creating Python virtual environment..."
    $PYTHON -m venv venv
fi

# Activate venv
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
fi

# Install/upgrade deps
echo -e "  Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Create .env if missing
if [ ! -f ".env" ]; then
    echo -e "  ${YELLOW}Creating default .env (paper trading mode)${NC}"
    cat > .env << 'ENVFILE'
# SPY DayTrader Configuration (Paper Trading)
# No broker credentials needed for paper trading!

TRADING_MODE=paper
INITIAL_CAPITAL=25000.0
MAX_RISK_PER_TRADE=0.015
DAILY_LOSS_LIMIT=0.02
MAX_DRAWDOWN=0.16
MAX_POSITION_PCT=0.30
MAX_TRADES_PER_DAY=10
COOLDOWN_AFTER_CONSECUTIVE_LOSSES=3
COOLDOWN_MINUTES=15

DATABASE_URL=sqlite+aiosqlite:///./spy_daytrader.db
API_HOST=0.0.0.0
API_PORT=8000
ENVFILE
fi

echo -e "  ${GREEN}Backend ready${NC}"

# ── Frontend Setup ───────────────────────────────────────────────────────
echo -e "${CYAN}[4/5] Setting up frontend...${NC}"
cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
    echo -e "  Installing frontend dependencies..."
    if command -v npm &>/dev/null; then
        npm install --silent
    else
        echo -e "  ${RED}npm not found - install Node.js from https://nodejs.org${NC}"
        exit 1
    fi
else
    echo -e "  ${GREEN}Dependencies already installed${NC}"
fi
echo -e "  ${GREEN}Frontend ready${NC}"

# ── Launch ───────────────────────────────────────────────────────────────
echo -e "${CYAN}[5/5] Launching SPY DayTrader...${NC}"
echo ""

# Start backend
cd "$BACKEND_DIR"
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
fi

echo -e "  ${GREEN}Starting backend on http://localhost:8000${NC}"
$PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info &
BACKEND_PID=$!

# Wait for backend to be ready
echo -e "  Waiting for backend..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/api/health >/dev/null 2>&1; then
        echo -e "  ${GREEN}Backend is up${NC}"
        break
    fi
    sleep 1
done

# Start frontend
cd "$FRONTEND_DIR"
echo -e "  ${GREEN}Starting frontend on http://localhost:5173${NC}"
if command -v npx &>/dev/null; then
    npx vite --host &
else
    ./node_modules/.bin/vite --host &
fi
FRONTEND_PID=$!

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  SPY DayTrader is running!                               ║${NC}"
echo -e "${GREEN}║                                                          ║${NC}"
echo -e "${GREEN}║  Dashboard:  http://localhost:5173                        ║${NC}"
echo -e "${GREEN}║  API:        http://localhost:8000/docs                   ║${NC}"
echo -e "${GREEN}║  Mode:       PAPER TRADING (no real money)               ║${NC}"
echo -e "${GREEN}║                                                          ║${NC}"
echo -e "${GREEN}║  12 Strategies (auto-backtested every 4h):               ║${NC}"
echo -e "${GREEN}║    Trending:   ORB, EMA Crossover, MTF Momentum,         ║${NC}"
echo -e "${GREEN}║                Micro Pullback, Momentum Scalper          ║${NC}"
echo -e "${GREEN}║    Range:      VWAP Reversion, Volume Flow,              ║${NC}"
echo -e "${GREEN}║                RSI Divergence, BB Squeeze, Dbl Bot/Top   ║${NC}"
echo -e "${GREEN}║    Volatile:   MACD Reversal, Gap Fill                   ║${NC}"
echo -e "${GREEN}║                                                          ║${NC}"
echo -e "${GREEN}║  Press Ctrl+C to stop                                    ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

wait
