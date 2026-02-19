import type { AccountInfo } from '../types';

interface Props {
  account: AccountInfo | null;
}

export default function PnLCard({ account }: Props) {
  if (!account) return <div className="card skeleton h-40" />;

  const dailyPositive = account.daily_pnl >= 0;
  const totalPositive = account.total_pnl >= 0;

  return (
    <div className={`card p-4 ${dailyPositive ? 'accent-left-green' : 'accent-left-red'}`}>
      <h2 className="card-title mb-3">P&L Summary</h2>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="label">Daily P&L</p>
          <p className={`text-2xl font-mono font-bold ${dailyPositive ? 'text-profit text-glow-green' : 'text-loss text-glow-red'}`}>
            ${account.daily_pnl.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="label">Total P&L</p>
          <p className={`text-2xl font-mono font-bold ${totalPositive ? 'text-profit' : 'text-loss'}`}>
            ${account.total_pnl.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="label">Equity</p>
          <p className="data-value text-lg">${account.equity.toLocaleString()}</p>
        </div>
        <div>
          <p className="label">Win Rate</p>
          <p className="data-value text-lg">{(account.win_rate * 100).toFixed(1)}%</p>
        </div>
        <div>
          <p className="label">Trades</p>
          <p className="data-value text-lg">{account.total_trades}</p>
        </div>
        <div>
          <p className="label">Drawdown</p>
          <p className={`data-value text-lg ${
            account.drawdown_pct > 10 ? 'text-loss' : account.drawdown_pct > 5 ? 'text-caution' : 'text-terminal-100'
          }`}>
            {account.drawdown_pct.toFixed(2)}%
          </p>
        </div>
      </div>
    </div>
  );
}
