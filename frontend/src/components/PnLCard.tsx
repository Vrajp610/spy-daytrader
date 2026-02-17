import type { AccountInfo } from '../types';

interface Props {
  account: AccountInfo | null;
}

export default function PnLCard({ account }: Props) {
  if (!account) return <div className="bg-gray-900 rounded-xl p-4 border border-gray-800 animate-pulse h-40" />;

  const dailyColor = account.daily_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const totalColor = account.total_pnl >= 0 ? 'text-green-400' : 'text-red-400';

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h2 className="text-lg font-semibold mb-3">P&L Summary</h2>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="text-xs text-gray-400 uppercase">Daily P&L</p>
          <p className={`text-2xl font-bold ${dailyColor}`}>
            ${account.daily_pnl.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase">Total P&L</p>
          <p className={`text-2xl font-bold ${totalColor}`}>
            ${account.total_pnl.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase">Equity</p>
          <p className="text-lg font-medium">${account.equity.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase">Win Rate</p>
          <p className="text-lg font-medium">{(account.win_rate * 100).toFixed(1)}%</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase">Trades</p>
          <p className="text-lg font-medium">{account.total_trades}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase">Drawdown</p>
          <p className={`text-lg font-medium ${account.drawdown_pct > 10 ? 'text-red-400' : 'text-gray-100'}`}>
            {account.drawdown_pct.toFixed(2)}%
          </p>
        </div>
      </div>
    </div>
  );
}
