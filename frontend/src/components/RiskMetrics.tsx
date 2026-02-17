import type { RiskMetrics } from '../types';

interface Props {
  risk: RiskMetrics | null;
}

export default function RiskMetricsCard({ risk }: Props) {
  if (!risk) return <div className="bg-gray-900 rounded-xl p-4 border border-gray-800 animate-pulse h-40" />;

  const ddPct = risk.current_drawdown_pct;
  const ddLimit = risk.max_drawdown_limit;
  const ddFill = Math.min((ddPct / ddLimit) * 100, 100);
  const ddColor = ddPct > ddLimit * 0.75 ? 'bg-red-500' : ddPct > ddLimit * 0.5 ? 'bg-yellow-500' : 'bg-green-500';

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h2 className="text-lg font-semibold mb-3">Risk Metrics</h2>

      {/* Drawdown gauge */}
      <div className="mb-3">
        <div className="flex justify-between text-xs text-gray-400 mb-1">
          <span>Drawdown</span>
          <span>{ddPct.toFixed(2)}% / {ddLimit}%</span>
        </div>
        <div className="w-full bg-gray-800 rounded-full h-3">
          <div className={`${ddColor} h-3 rounded-full transition-all`} style={{ width: `${ddFill}%` }} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <p className="text-gray-400 text-xs">Daily Loss</p>
          <p className={risk.daily_loss < 0 ? 'text-red-400' : ''}>
            ${risk.daily_loss.toFixed(2)} / -${risk.daily_loss_limit.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="text-gray-400 text-xs">Trades Today</p>
          <p>{risk.trades_today} / {risk.max_trades_per_day}</p>
        </div>
        <div>
          <p className="text-gray-400 text-xs">Consec. Losses</p>
          <p className={risk.consecutive_losses >= 3 ? 'text-red-400' : ''}>{risk.consecutive_losses}</p>
        </div>
        <div>
          <p className="text-gray-400 text-xs">Status</p>
          {risk.circuit_breaker_active ? (
            <p className="text-red-400 font-bold">CIRCUIT BREAKER</p>
          ) : risk.cooldown_active ? (
            <p className="text-yellow-400">Cooling Off</p>
          ) : (
            <p className="text-green-400">Active</p>
          )}
        </div>
      </div>
    </div>
  );
}
