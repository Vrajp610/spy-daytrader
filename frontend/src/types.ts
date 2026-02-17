// ── API Response Types ─────────────────────────────────────────────

export interface BotStatus {
  running: boolean;
  mode: string;
  current_regime: string | null;
  open_position: Position | null;
  daily_pnl: number;
  daily_trades: number;
  consecutive_losses: number;
  cooldown_until: string | null;
  equity: number;
  peak_equity: number;
  drawdown_pct: number;
}

export interface Position {
  symbol: string;
  direction: string;
  quantity: number;
  entry_price: number;
  entry_time: string;
  stop_loss: number;
  take_profit: number;
  strategy: string;
  unrealized_pnl?: number;
}

export interface Trade {
  id?: number;
  symbol?: string;
  direction: string;
  strategy: string;
  regime?: string;
  quantity: number;
  entry_price: number;
  exit_price: number;
  entry_time: string;
  exit_time: string;
  pnl: number;
  pnl_pct?: number;
  exit_reason: string;
}

export interface AccountInfo {
  equity: number;
  cash: number;
  buying_power: number;
  peak_equity: number;
  drawdown_pct: number;
  daily_pnl: number;
  total_pnl: number;
  win_rate: number;
  total_trades: number;
}

export interface RiskMetrics {
  current_drawdown_pct: number;
  max_drawdown_limit: number;
  daily_loss: number;
  daily_loss_limit: number;
  trades_today: number;
  max_trades_per_day: number;
  consecutive_losses: number;
  cooldown_active: boolean;
  circuit_breaker_active: boolean;
}

export interface BacktestRequest {
  start_date: string;
  end_date: string;
  interval?: string;
  initial_capital?: number;
  strategies?: string[];
  use_regime_filter?: boolean;
}

export interface BacktestResult {
  id: number;
  created_at: string;
  start_date: string;
  end_date: string;
  strategies: string;
  initial_capital: number;
  total_return_pct: number | null;
  win_rate: number | null;
  total_trades: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
  profit_factor: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  equity_curve: { timestamp: string; equity: number }[] | null;
  trades_json: Trade[] | null;
}

export interface StrategyConfig {
  id: number;
  name: string;
  enabled: boolean;
  params: Record<string, unknown>;
}

export interface DailyPerformance {
  date: string;
  pnl: number;
  trades: number;
  wins: number;
  losses: number;
}

// ── WebSocket Message ────────────────────────────────────────────

export interface WSMessage {
  type: 'price_update' | 'trade_update' | 'status_update' | 'error' | 'pong';
  data: Record<string, unknown>;
}
