# Single-Strategy Backtest Dropdown + Reduced API Polling

## Overview

This change makes two improvements to the SPY DayTrader frontend:

1. **BacktestPanel** — Replaced the multi-select strategy tile buttons with a single-select dropdown, so you run one strategy at a time.
2. **API Polling** — Reduced polling frequencies across all components and added exponential backoff to WebSocket reconnection logic.

---

## 1. BacktestPanel: Single-Select Dropdown

### What changed

**File:** `frontend/src/components/BacktestPanel.tsx`

#### Before

- State held an array of selected strategies: `useState<string[]>([...all 12...])`
- A `toggleStrategy` function toggled strategies in/out of the array
- The UI rendered 12 clickable tile buttons (blue = selected, gray = deselected)
- On submit, the full array was sent: `strategies: strategies`

#### After

- State holds a single strategy string: `useState('vwap_reversion')`
- No toggle function needed
- The UI renders a `<select>` dropdown with all 12 strategies as `<option>` elements
- On submit, it wraps in an array: `strategies: [strategy]`

#### Why

Running all 12 strategies simultaneously in a backtest is slow and produces noisy, hard-to-interpret results. A single-select dropdown:
- Forces focused, one-at-a-time strategy evaluation
- Makes the UI cleaner and less cluttered
- Still sends a `strategies` array to the backend (single-element), so no backend API changes are needed

#### How to use

1. Open the dashboard
2. In the **Backtesting** panel, use the **Strategy** dropdown to pick one strategy
3. Optionally toggle the **Regime Filter** checkbox
4. Set date range and capital, then click **Run Backtest**

### Backend default strategies list

**File:** `backend/app/schemas.py`

The `BacktestRequest` schema's default `strategies` list was updated from 5 to all 12:

```python
strategies: list[str] = [
    "vwap_reversion", "orb", "ema_crossover", "volume_flow", "mtf_momentum",
    "rsi_divergence", "bb_squeeze", "macd_reversal", "momentum_scalper",
    "gap_fill", "micro_pullback", "double_bottom_top",
]
```

This is cosmetic — the frontend now always sends exactly one strategy. The default only matters if someone hits the API directly without specifying strategies.

---

## 2. Reduced API Polling Intervals

### What changed

| File | Component | Old Interval | New Interval |
|---|---|---|---|
| `frontend/src/components/BotControls.tsx` | Bot status polling | 3 seconds | 10 seconds |
| `frontend/src/components/PositionCard.tsx` | Position polling | 3 seconds | 10 seconds |
| `frontend/src/hooks/useAccount.ts` | Account + risk metrics | 5 seconds | 15 seconds |
| `frontend/src/hooks/useTrades.ts` | Trade history | 10 seconds | 30 seconds |

### Why

The previous intervals were aggressive and generated unnecessary network traffic. Bot status and positions don't change multiple times per second — 10-second intervals are sufficient. Account info and trade history change even less frequently.

### How it works

Each component/hook uses `setInterval` inside a `useEffect`:

```typescript
useEffect(() => {
  refresh();
  const interval = setInterval(refresh, 10000); // was 3000
  return () => clearInterval(interval);
}, []);
```

The pattern is identical across all four files — only the millisecond value changed.

---

## 3. WebSocket Exponential Backoff

### What changed

**File:** `frontend/src/hooks/useWebSocket.ts`

#### Before

On WebSocket close, it reconnected after a fixed 3-second delay every time:

```typescript
ws.onclose = () => {
  setConnected(false);
  reconnectTimer.current = setTimeout(connect, 3000);
};
```

#### After

Uses exponential backoff with a 60-second cap:

```typescript
const reconnectAttempt = useRef(0);

// In connect():
ws.onopen = () => {
  setConnected(true);
  reconnectAttempt.current = 0;  // Reset on successful connection
  // ...
};

ws.onclose = () => {
  setConnected(false);
  const delay = Math.min(3000 * Math.pow(2, reconnectAttempt.current), 60000);
  reconnectAttempt.current += 1;
  reconnectTimer.current = setTimeout(connect, delay);
};
```

#### Reconnect schedule

| Attempt | Delay |
|---|---|
| 0 | 3 seconds |
| 1 | 6 seconds |
| 2 | 12 seconds |
| 3 | 24 seconds |
| 4 | 48 seconds |
| 5+ | 60 seconds (cap) |

#### Why

If the server is down, hammering it with reconnect attempts every 3 seconds wastes resources on both client and server. Exponential backoff:
- Gives the server breathing room to recover
- Reduces browser network noise
- Resets immediately on successful reconnection, so normal operation resumes instantly

---

## All Files Changed

| # | File | Change |
|---|---|---|
| 1 | `frontend/src/components/BacktestPanel.tsx` | Multi-select tiles → single-select dropdown |
| 2 | `frontend/src/components/BotControls.tsx` | Polling 3s → 10s |
| 3 | `frontend/src/components/PositionCard.tsx` | Polling 3s → 10s |
| 4 | `frontend/src/hooks/useAccount.ts` | Polling 5s → 15s |
| 5 | `frontend/src/hooks/useTrades.ts` | Polling 10s → 30s |
| 6 | `frontend/src/hooks/useWebSocket.ts` | Exponential backoff on reconnect |
| 7 | `backend/app/schemas.py` | Default strategies list updated to all 12 |

---

## Verification Steps

1. **Start the server:** `./start.sh`
2. **Open the dashboard** in your browser
3. **Backtest dropdown:** Confirm the Strategy dropdown shows all 12 strategies. Select one, run a backtest, verify results appear.
4. **Reduced polling:** Open browser DevTools → Network tab. Confirm API calls are spaced at 10s/15s/30s intervals instead of 3s/5s/10s.
5. **WebSocket backoff:** Stop the backend server while the dashboard is open. In the browser console, observe reconnect attempts increasing in delay (3s, 6s, 12s...). Restart the server — the WebSocket should reconnect and the attempt counter resets.

---

## Available Strategies

| Strategy | Key |
|---|---|
| VWAP Reversion | `vwap_reversion` |
| Opening Range Breakout | `orb` |
| EMA Crossover | `ema_crossover` |
| Volume Flow | `volume_flow` |
| Multi-Timeframe Momentum | `mtf_momentum` |
| RSI Divergence | `rsi_divergence` |
| Bollinger Band Squeeze | `bb_squeeze` |
| MACD Reversal | `macd_reversal` |
| Momentum Scalper | `momentum_scalper` |
| Gap Fill | `gap_fill` |
| Micro Pullback | `micro_pullback` |
| Double Bottom/Top | `double_bottom_top` |
