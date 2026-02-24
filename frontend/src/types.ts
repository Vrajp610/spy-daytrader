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
  total_pnl: number;
  vix?: number;
  vix_term_ratio?: number;
  vix_regime?: string;
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
  original_quantity?: number;
  effective_stop?: number;
  scales_completed?: number[];
  // Options fields
  option_strategy_type?: string;
  option_strategy_abbrev?: string;
  contracts?: number;
  net_premium?: number;
  max_loss?: number;
  max_profit?: number;
  net_delta?: number;
  net_theta?: number;
  legs?: OptionLeg[];
  underlying_price?: number;
  expiration_date?: string;
  display?: string;
}

export interface OptionLeg {
  contract_symbol: string;
  option_type: string;
  strike: number;
  expiration: string;
  action: string;
  quantity: number;
  premium: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  iv: number;
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
  is_partial?: boolean;
  confidence?: number;
  slippage?: number;
  commission?: number;
  mae?: number;
  mfe?: number;
  mae_pct?: number;
  mfe_pct?: number;
  bars_held?: number;
  // Options fields
  option_strategy_type?: string;
  contract_symbol?: string;
  legs_json?: string;
  strike?: number;
  expiration_date?: string;
  option_type?: string;
  net_premium?: number;
  max_loss?: number;
  max_profit?: number;
  entry_delta?: number;
  entry_theta?: number;
  entry_iv?: number;
  underlying_entry?: number;
  underlying_exit?: number;
  contracts?: number;
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

// ── Trading Settings ────────────────────────────────────────────

export interface TradingSettings {
  initial_capital: number;
  max_risk_per_trade: number;
  daily_loss_limit: number;
  max_drawdown: number;
  max_position_pct: number;
  max_trades_per_day: number;
  cooldown_after_consecutive_losses: number;
  cooldown_minutes: number;
  min_signal_confidence: number;
  // Options settings
  default_spread_width: number;
  preferred_dte_min: number;
  preferred_dte_max: number;
  target_delta_short: number;
  credit_profit_target_pct: number;
  max_contracts_per_trade: number;
}

// ── WebSocket Message ────────────────────────────────────────────

export interface WSMessage {
  type: 'price_update' | 'trade_update' | 'status_update' | 'error' | 'pong';
  data: Record<string, unknown>;
}

// ── Leaderboard ──────────────────────────────────────────────────

export interface StrategyRanking {
  strategy_name: string;
  // Short-term metrics
  avg_sharpe_ratio: number;
  avg_profit_factor: number;
  avg_win_rate: number;
  avg_return_pct: number;
  avg_max_drawdown_pct: number;
  st_composite_score: number;
  total_backtest_trades: number;
  backtest_count: number;
  computed_at: string | null;
  // Long-term metrics (null until first LT run)
  lt_cagr_pct: number | null;
  lt_sharpe: number | null;
  lt_sortino: number | null;
  lt_calmar: number | null;
  lt_max_drawdown_pct: number | null;
  lt_win_rate: number | null;
  lt_profit_factor: number | null;
  lt_total_trades: number | null;
  lt_years_tested: number | null;
  lt_composite_score: number | null;
  lt_computed_at: string | null;
  // Blended score (55% ST + 45% LT when LT available)
  composite_score: number;
}

export interface LeaderboardProgress {
  status: string;
  current_test: string;
  completed: number;
  total: number;
  errors: number;
  last_run: string | null;
}

export interface LtProgress {
  status: string;
  current_test: string;
  completed: number;
  total: number;
  errors: number;
  last_run: string | null;
  start_date: string;
  end_date: string;
}

export interface LeaderboardResponse {
  rankings: StrategyRanking[];
  progress: LeaderboardProgress;
  lt_progress: LtProgress;
}

// ── Long-Term Backtest ────────────────────────────────────────────

export interface LongTermBacktestRequest {
  start_date: string;
  end_date: string;
  initial_capital?: number;
  strategies?: string[];
  max_risk_per_trade?: number;
}

export interface YearlyReturn {
  year: number;
  return_pct: number;
  trades: number;
  end_equity: number;
}

export interface LongTermBacktestResult {
  cagr_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_drawdown_pct: number;
  total_return_pct: number;
  win_rate: number;
  total_trades: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  final_capital: number;
  years_tested: number;
  equity_curve: { date: string; equity: number }[];
  yearly_returns: YearlyReturn[];
  trades: Record<string, unknown>[];
}

// ── Strategy Live Performance ─────────────────────────────────────────

export interface StrategyLiveStats {
  strategy_name: string;
  live_trades: number;
  live_wins: number;
  live_losses: number;
  live_pnl_total: number;
  live_win_rate: number;
  live_avg_win: number;
  live_avg_loss: number;
  live_profit_factor: number;
  consecutive_live_losses: number;
  auto_disabled: boolean;
  disabled_reason: string | null;
  disabled_at: string | null;
  last_trade_at: string | null;
}

export interface StrategyComparison {
  strategy: string;
  date_range: string;
  start_date: string;
  end_date: string;
  total_trades: number;
  win_rate: number;
  total_return_pct: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  profit_factor: number;
}
