import type { RiskMetrics } from '../types';

interface Props {
  risk: RiskMetrics | null;
}

export default function RiskMetricsCard({ risk }: Props) {
  if (!risk) return <div className="card skeleton h-40" />;

  const ddPct = risk.current_drawdown_pct;
  const ddLimit = risk.max_drawdown_limit;
  const ddFill = Math.min((ddPct / ddLimit) * 100, 100);
  const ddColor = ddPct > ddLimit * 0.75
    ? 'bg-gradient-to-r from-loss-dim to-loss'
    : ddPct > ddLimit * 0.5
      ? 'bg-gradient-to-r from-caution-dim to-caution'
      : 'bg-gradient-to-r from-profit-dim to-profit';

  return (
    <div className="card p-4 accent-left-blue">
      <h2 className="card-title mb-3">Risk Metrics</h2>

      {/* Drawdown gauge */}
      <div className="mb-3">
        <div className="flex justify-between mb-1">
          <span className="label">Drawdown</span>
          <span className="text-xxs font-mono text-muted">{ddPct.toFixed(2)}% / {ddLimit}%</span>
        </div>
        <div className="progress-track">
          <div className={`progress-fill ${ddColor}`} style={{ width: `${ddFill}%` }} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <p className="label">Daily Loss</p>
          <p className={`data-value ${risk.daily_loss < 0 ? 'text-loss' : ''}`}>
            ${risk.daily_loss.toFixed(2)} / -${risk.daily_loss_limit.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="label">Trades Today</p>
          <p className="data-value">{risk.trades_today} / {risk.max_trades_per_day}</p>
        </div>
        <div>
          <p className="label">Consec. Losses</p>
          <p className={`data-value ${risk.consecutive_losses >= 3 ? 'text-loss' : ''}`}>{risk.consecutive_losses}</p>
        </div>
        <div>
          <p className="label">Status</p>
          {risk.circuit_breaker_active ? (
            <span className="badge badge-live font-bold">CIRCUIT BREAKER</span>
          ) : risk.cooldown_active ? (
            <span className="badge border-caution/40 bg-caution/10 text-caution">Cooling Off</span>
          ) : (
            <span className="badge badge-active">Active</span>
          )}
        </div>
      </div>
    </div>
  );
}
