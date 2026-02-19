# SPY DayTrader

An automated SPY day trading platform with 12 institutional-grade strategies, regime-adaptive filtering, layered risk management, and a real-time React dashboard. Runs in paper trading mode by default — no broker credentials required.

## Architecture

```
Frontend (React + Vite + TailwindCSS)
   |
   |-- REST API (axios)
   |-- WebSocket (real-time price/trade updates)
   |
   v
Backend (FastAPI + SQLAlchemy + SQLite)
   |
   |-- Trading Engine (async loop, 30s ticks)
   |    |-- RegimeDetector -> selects strategies for current market
   |    |-- 12 Strategy instances -> generate entry/exit signals
   |    |-- RiskManager -> position sizing, gate checks, Kelly criterion
   |    |-- ExitManager -> scale-outs, trailing stops, breakeven mgmt
   |    |-- PaperEngine or SchwabClient -> order execution
   |
   |-- DataManager (Yahoo Finance -> pandas -> technical indicators)
   |-- AutoBacktester (background, every 4 hours)
   |-- SQLite (trades, backtests, rankings, configs)
   |
   v
Yahoo Finance v8 API (market data)
Charles Schwab API (live trading, optional)
```

## Quick Start

```bash
git clone https://github.com/Vrajp610/spy-daytrader.git
cd spy-daytrader
chmod +x start.sh
./start.sh
```

The startup script:
1. Checks for Python 3.10+ and Node.js
2. Creates a Python virtual environment and installs dependencies
3. Installs frontend dependencies via npm
4. Creates a default `.env` for paper trading
5. Launches backend on `http://localhost:8000` and frontend on `http://localhost:5173`

Open `http://localhost:5173` in your browser to access the dashboard.

## Project Structure

```
spy-daytrader/
├── start.sh                          # Universal startup script
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                   # FastAPI app, lifespan, WebSocket endpoint
│   │   ├── config.py                 # Settings from .env via pydantic-settings
│   │   ├── database.py               # Async SQLAlchemy + SQLite setup
│   │   ├── models.py                 # ORM models (6 tables)
│   │   ├── schemas.py                # Pydantic request/response schemas
│   │   ├── websocket.py              # WebSocket connection manager
│   │   ├── routes/
│   │   │   ├── trading.py            # /api/trading/* (start, stop, status, trades, position)
│   │   │   ├── account.py            # /api/account/* (info, risk, performance)
│   │   │   ├── backtest.py           # /api/backtest/* (run, results)
│   │   │   ├── settings.py           # /api/settings/* (strategy configs)
│   │   │   └── leaderboard.py        # /api/leaderboard/* (rankings, comparison)
│   │   └── services/
│   │       ├── trading_engine.py     # Core orchestrator (30s tick loop)
│   │       ├── paper_engine.py       # Simulated order execution
│   │       ├── schwab_client.py      # Charles Schwab API wrapper
│   │       ├── data_manager.py       # Yahoo Finance data + indicators
│   │       ├── risk_manager.py       # Position sizing, gate checks, Kelly
│   │       ├── exit_manager.py       # Scale-outs, trailing stops, breakeven
│   │       ├── backtester.py         # Bar-by-bar backtesting engine
│   │       ├── auto_backtester.py    # Scheduled background backtests
│   │       └── strategies/
│   │           ├── base.py           # BaseStrategy abstract class
│   │           ├── vwap_reversion.py
│   │           ├── orb.py
│   │           ├── ema_crossover.py
│   │           ├── volume_flow.py
│   │           ├── mtf_momentum.py
│   │           ├── rsi_divergence.py
│   │           ├── bb_squeeze.py
│   │           ├── macd_reversal.py
│   │           ├── momentum_scalper.py
│   │           ├── gap_fill.py
│   │           ├── micro_pullback.py
│   │           └── double_bottom_top.py
│
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js
    └── src/
        ├── App.tsx
        ├── types.ts                  # TypeScript interfaces
        ├── services/api.ts           # Axios REST client
        ├── hooks/
        │   ├── useWebSocket.ts       # WebSocket with exponential backoff
        │   ├── useAccount.ts         # Account + risk polling (15s)
        │   ├── useTrades.ts          # Trade history polling (30s)
        │   ├── useBacktest.ts        # Backtest runner + history
        │   └── useLeaderboard.ts     # Strategy rankings polling (30s)
        └── components/
            ├── Dashboard.tsx         # Main layout
            ├── BotControls.tsx       # Start/stop, mode toggle, regime display
            ├── PnLCard.tsx           # Daily P&L, equity, win rate
            ├── PositionCard.tsx      # Open position details + scale indicators
            ├── RiskMetrics.tsx       # Drawdown gauge, limits, cooldown status
            ├── TradeHistory.tsx      # Sortable/filterable trade table
            ├── Chart.tsx             # Equity curve (lightweight-charts)
            ├── BacktestPanel.tsx     # Single-strategy backtest runner
            ├── StrategyConfig.tsx    # Per-strategy enable/disable + params
            └── StrategyLeaderboard.tsx  # Auto-backtest rankings
```

## How It Works

### The Trading Loop

The trading engine runs an async loop that ticks every 30 seconds during market hours (9:30 AM - 4:00 PM ET). Each tick:

1. **Data fetch** (every 60s): Pulls 2 days of 1-minute SPY bars from Yahoo Finance. Computes VWAP, RSI(14), EMA(9/21), ATR(14), ADX(14), MACD(12/26/9), Bollinger Bands(20,2), and volume ratios. Resamples to 5-min and 15-min bars for multi-timeframe strategies.

2. **Regime detection**: Classifies the current market using the 5-min data:
   - **Trending** (up/down): ADX > 25 with clear EMA/VWAP alignment
   - **Range-bound**: ADX < 18, narrow Bollinger Bands
   - **Volatile**: High ATR relative to recent history

3. **Strategy filtering**: Only strategies suited to the current regime are evaluated (see regime map below).

4. **Entry check** (if no position): Each eligible strategy generates a signal. The first valid signal is taken — the system holds only one position at a time.

5. **Exit check** (if position open): The ExitManager evaluates scale-outs, adaptive trailing stops, breakeven stops, and strategy-specific exits.

6. **EOD flatten**: All positions are force-closed at 3:55 PM ET.

### Regime-Strategy Map

| Regime | Strategies |
|---|---|
| Trending (up/down) | ORB, EMA Crossover, MTF Momentum, Micro Pullback, Momentum Scalper |
| Range-bound | VWAP Reversion, Volume Flow, RSI Divergence, BB Squeeze, Double Bottom/Top |
| Volatile | VWAP Reversion, Volume Flow, MACD Reversal, Gap Fill |

## The 12 Strategies

### Trending Strategies

**Opening Range Breakout (ORB)**
Captures the 9:30-9:45 AM high/low range. Enters on a breakout beyond the range with 1.5x average volume confirmation. Only trades between 9:45-10:30 AM. Detects false breakouts if price closes back inside the range within 3 bars. Target: 2x range width. Stop: 50% retracement into range.

**EMA Crossover + RSI**
Operates on 5-minute bars. Enters when EMA(9) crosses EMA(21) with RSI in the 40-70 zone (longs) or 30-60 (shorts), confirmed by MACD histogram direction, ADX > 20, and VWAP alignment. Exits on reverse cross. Target: 2.0x ATR. Stop: 1.5x ATR.

**Multi-Timeframe Momentum**
Scores confluence across 1-min, 5-min, and 15-min timeframes (0-100 scale). 1-min contributes 20 points, 5-min 30 points, 15-min 50 points. Factors: EMA alignment, MACD direction, RSI zone, ADX trend strength. Enters when score >= 60 (long) or <= -60 (short). Target: 2.5x ATR.

**Micro Pullback**
Waits for strong trends (ADX > 30) then enters when price pulls back to touch EMA(9) and closes back above/below it. Target: 2.0x ATR. Stop: 1.0x ATR.

**Momentum Scalper**
Uses fast RSI(5) to detect oversold bounces (RSI was <= 25, bounces up 5+ points) or overbought drops (RSI was >= 75, drops 5+ points) in trending markets. Tight stop at 0.75x ATR. Target: 1.5x ATR. 30-minute time stop.

### Range-Bound Strategies

**VWAP Mean Reversion**
Enters when price deviates >= 0.3% from VWAP with RSI at extremes (<=30 for longs, >=70 for shorts) and 1.3x volume confirmation. Targets a reversion back to VWAP. Only trades after 10:00 AM. 45-minute time stop.

**Volume Profile + Order Flow**
Computes Volume Point of Control (VPOC), Value Area High/Low (where 70% of volume traded), and cumulative volume delta. Enters at value area boundaries with confirming delta. Detects institutional absorption (high volume + minimal price change). Target: VPOC or 2.0x ATR.

**RSI Divergence**
Scans a 10-bar window for divergences between price and RSI. Bullish: price makes a lower low but RSI makes a higher low (RSI <= 35). Bearish: price makes a higher high but RSI makes a lower high (RSI >= 65). Target: 1.5x ATR.

**Bollinger Band Squeeze**
Detects when BB width drops to the bottom 20th percentile of the last 50 bars (compression), then enters when width expands with 1.3x volume. Direction follows the breakout side. Target: 2.0x ATR.

**Double Bottom/Top**
Identifies two swing lows (or highs) within 0.2% of each other, at least 5 bars apart, within a 30-bar lookback. Enters when price breaks the neckline with 1.2x volume. Target: pattern height projected from neckline.

### Volatile Strategies

**MACD Reversal**
Enters when MACD histogram reaches an 85th percentile extreme, then reverses for 3 consecutive bars with 1.2x volume. Target: 1.5x ATR. Stop: 1.0x ATR.

**Gap Fill**
Detects opening gaps of 0.2%-1.5% from the prior day's close. Fades the gap (short gap-ups, long gap-downs) expecting a fill back to the prior close. Only trades 9:31-10:30 AM. Target: prior day's close.

## Exit Management

The ExitManager handles all exits centrally, overriding individual strategy stop/target logic:

### Scale-Out System
- **Scale 1**: At 1.0x ATR profit, close 50% of position. Move stop to breakeven.
- **Scale 2**: At 2.0x ATR profit, close 25% of position. Tighten trailing stop to 0.5x ATR.
- Only activates when original position is >= 4 shares.

### Adaptive Trailing Stop
| Profit Level | Trailing Distance |
|---|---|
| < 1 ATR | Strategy's default stop |
| 1-2 ATR | 0.75x ATR from high/low watermark |
| > 2 ATR | 0.50x ATR from high/low watermark |

### Other Exits
- **EOD flatten**: Mandatory close at 3:55 PM ET
- **Time stops**: Strategy-specific (30-60 minutes depending on strategy)
- **Reverse signals**: Strategy detects opposing conditions
- **False breakout**: ORB-specific — price re-enters the opening range

## Risk Management

| Rule | Default | Description |
|---|---|---|
| Max risk per trade | 1.5% of capital | Caps dollars at risk per trade |
| Daily loss limit | 2% of capital | Stops all trading for the day |
| Circuit breaker | 16% drawdown from peak | Halts all trading until manual reset |
| Max position size | 30% of capital | Single position cap |
| Max trades per day | 10 | Hard daily limit |
| Consecutive loss cooldown | 3 losses | 15-minute pause after 3 straight losses |
| EOD flat | 3:55 PM ET | All positions closed before market close |
| Slippage simulation | 1 basis point/side | Realistic fill modeling in paper mode |

### Adaptive Position Sizing

Uses quarter-Kelly criterion based on a rolling window of the last 50 trades:

```
Kelly fraction = win_rate - (1 - win_rate) / payoff_ratio
Position Kelly = Kelly / 4  (quarter-Kelly for safety)
Quantity = (capital * position_kelly) / stop_distance
```

Falls back to the configured `max_risk_per_trade` with insufficient trade history. Additionally scales down 25% per consecutive loss (minimum 25% of normal size).

## Auto-Backtester and Strategy Leaderboard

A background task runs automatically on startup and every 4 hours.

### Test Matrix
- **Individual**: Each of 12 strategies x 3 date ranges (1 day, 5 days, 30 days) = 36 tests
- **Combinations**: 9 curated multi-strategy combos x 3 date ranges = 27 tests
- **Total**: ~63 backtests per run

### Ranking Algorithm

Each strategy is scored on a composite scale:

| Factor | Weight | Normalization |
|---|---|---|
| Sharpe Ratio | 35% | -3 to 3 mapped to -100 to 100 |
| Profit Factor | 25% | 0 to 3 mapped to -50 to 100 |
| Win Rate | 20% | 0% to 100% mapped directly |
| Return % | 10% | -5% to 5% mapped to -100 to 100 |
| Low Drawdown | 10% | 100 - (drawdown * 10) |

Rankings are computed from individual strategy results averaged across all 3 date ranges. The leaderboard is visible on the dashboard and updates after each auto-backtest cycle.

## Configuration

All settings are controlled via `backend/.env`:

```env
# Trading mode
TRADING_MODE=paper              # paper or live

# Capital and risk
INITIAL_CAPITAL=25000.0
MAX_RISK_PER_TRADE=0.015        # 1.5% per trade
DAILY_LOSS_LIMIT=0.02           # 2% daily loss limit
MAX_DRAWDOWN=0.16               # 16% circuit breaker
MAX_POSITION_PCT=0.30           # 30% max position
MAX_TRADES_PER_DAY=10
COOLDOWN_AFTER_CONSECUTIVE_LOSSES=3
COOLDOWN_MINUTES=15

# Database
DATABASE_URL=sqlite+aiosqlite:///./spy_daytrader.db

# Server
API_HOST=0.0.0.0
API_PORT=8000

# Live trading (optional - leave blank for paper-only)
SCHWAB_APP_KEY=
SCHWAB_APP_SECRET=
```

Individual strategy parameters (ATR multipliers, RSI thresholds, time windows) can be adjusted at runtime through the dashboard's Strategy Config panel or via the API at `PUT /api/settings/strategies/{name}`.

## API Reference

### Trading (`/api/trading`)
| Method | Endpoint | Description |
|---|---|---|
| GET | `/status` | Bot status: running, mode, regime, position, daily P&L |
| POST | `/start` | Start the trading engine |
| POST | `/stop` | Stop engine, close any open position |
| POST | `/mode` | Switch paper/live (live requires confirmation string) |
| GET | `/trades` | Last N closed trades |
| GET | `/position` | Current open position with unrealized P&L |

### Account (`/api/account`)
| Method | Endpoint | Description |
|---|---|---|
| GET | `/info` | Equity, cash, drawdown, win rate, total P&L |
| GET | `/risk` | Risk gauges: drawdown, daily loss, trade count, cooldown |
| GET | `/performance` | Daily performance breakdown |

### Backtest (`/api/backtest`)
| Method | Endpoint | Description |
|---|---|---|
| POST | `/run` | Run a backtest with specified strategy, dates, capital |
| GET | `/results` | List recent backtest runs |
| GET | `/results/{id}` | Get specific backtest result |

### Settings (`/api/settings`)
| Method | Endpoint | Description |
|---|---|---|
| GET | `/strategies` | All 12 strategies with enabled state and params |
| PUT | `/strategies/{name}` | Toggle enable/disable or update params |

### Leaderboard (`/api/leaderboard`)
| Method | Endpoint | Description |
|---|---|---|
| GET | `/rankings` | Strategy rankings by composite score |
| GET | `/comparison` | Per-strategy, per-date-range breakdown |
| GET | `/progress` | Current auto-backtest progress |
| POST | `/trigger` | Manually trigger a backtest run |

### WebSocket (`/ws`)
Real-time bidirectional messages. Types: `price_update`, `trade_update`, `status_update`, `error`, `pong`.

### Health (`/api/health`)
Returns `{"status": "ok", "mode": "paper"}`.

Full interactive API docs available at `http://localhost:8000/docs` (Swagger UI).

## Dashboard

The dashboard at `http://localhost:5173` provides:

- **Bot Controls**: Start/stop, paper/live mode toggle, current regime display
- **P&L Card**: Daily and total P&L, equity, win rate, trade count
- **Position Card**: Live open position with direction, entry, stop (including effective stop after scale-outs), target, unrealized P&L, and scale-out indicators (S1/S2)
- **Risk Metrics**: Visual drawdown gauge (green/yellow/red), daily loss vs limit, trades today, consecutive losses, circuit breaker and cooldown status
- **Trade History**: Sortable, filterable table of closed trades with exit reasons
- **Strategy Leaderboard**: Auto-backtest rankings with progress bar and manual trigger
- **Backtest Panel**: Single-strategy dropdown, date range, capital, regime filter toggle, result stats (8 metrics), equity curve chart, run history
- **Strategy Config**: Per-strategy enable/disable toggles with expandable parameter editors

Polling intervals: bot status and position every 10s, account info every 15s, trade history and leaderboard every 30s. WebSocket reconnects with exponential backoff (3s base, 60s cap).

## Live Trading

To enable live trading with Charles Schwab:

1. Create a Schwab developer app at [developer.schwab.com](https://developer.schwab.com)
2. Install the schwab-py library: `pip install schwab-py`
3. Set credentials in `backend/.env`:
   ```env
   SCHWAB_APP_KEY=your_app_key
   SCHWAB_APP_SECRET=your_app_secret
   ```
4. Complete OAuth authentication (schwab-py will guide you through token generation)
5. Switch to live mode via the dashboard mode toggle — you'll be required to type "I understand the risks of live trading" as confirmation

**Warning**: Live trading involves real money and real risk. The paper trading mode exists so you can evaluate strategy performance before committing capital. Always review backtest results and live paper performance extensively before enabling live mode.

## Adjusting for Success

This platform is designed to be iteratively tuned. Key areas to adjust over time:

### Strategy Parameters
Each strategy exposes configurable parameters (ATR multipliers for stops/targets, RSI thresholds, volume ratios, time windows). Use the backtest panel to test parameter changes against recent market data before applying them live.

### Regime Detection Thresholds
The regime detector uses ADX, BB width, and ATR ratios to classify market conditions. These thresholds in `trading_engine.py` may need tuning as market character evolves.

### Risk Parameters
The default risk settings (1.5% per trade, 2% daily loss limit, 16% circuit breaker) are conservative. Adjust based on your capital size, risk tolerance, and observed strategy performance.

### Strategy Selection
Use the leaderboard to identify which strategies perform well in current market conditions. Disable underperformers via the Strategy Config panel. The auto-backtester re-evaluates every 4 hours.

### Data Source
The platform defaults to Yahoo Finance for market data. For lower latency in live trading, consider integrating the Schwab streaming API or a dedicated market data provider.

## Tech Stack

**Backend**: Python 3.10+, FastAPI, SQLAlchemy (async), SQLite (aiosqlite), pandas, aiohttp
**Frontend**: React 18, TypeScript, Vite 6, TailwindCSS 3, Axios, lightweight-charts
**Data**: Yahoo Finance v8 API
**Broker**: Charles Schwab API (optional, via schwab-py)

## License

This project is for educational and personal use. Trading involves significant risk of financial loss. This software is provided as-is with no guarantees of profitability.
