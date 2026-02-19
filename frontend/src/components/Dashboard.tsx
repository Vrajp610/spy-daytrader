import { useWebSocket } from '../hooks/useWebSocket';
import { useAccount } from '../hooks/useAccount';
import { useTrades } from '../hooks/useTrades';
import BotControls from './BotControls';
import PnLCard from './PnLCard';
import PositionCard from './PositionCard';
import RiskMetricsCard from './RiskMetrics';
import TradeHistory from './TradeHistory';
import BacktestPanel from './BacktestPanel';
import StrategyConfigPanel from './StrategyConfig';
import TradingSettingsPanel from './TradingSettings';
import StrategyLeaderboard from './StrategyLeaderboard';

export default function Dashboard() {
  const { lastMessage, connected } = useWebSocket();
  const { account, risk } = useAccount();
  const { trades, total } = useTrades();

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold">SPY DayTrader</h1>
          <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">v1.0</span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-gray-400">{connected ? 'Connected' : 'Disconnected'}</span>
          {lastMessage?.type === 'price_update' && (
            <span className="ml-3 font-mono">
              SPY ${(lastMessage.data as { price?: number }).price?.toFixed(2) ?? '--'}
            </span>
          )}
        </div>
      </header>

      {/* Main Grid */}
      <main className="p-4 md:p-6 space-y-4">
        {/* Top row: Controls + Summary */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <BotControls />
          <PnLCard account={account} />
          <PositionCard />
          <RiskMetricsCard risk={risk} />
        </div>

        {/* Middle: Trade History */}
        <TradeHistory trades={trades} total={total} />

        {/* Strategy Leaderboard */}
        <StrategyLeaderboard />

        {/* Bottom: Backtesting + Strategy Config */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2">
            <BacktestPanel />
          </div>
          <div className="space-y-4">
            <TradingSettingsPanel />
            <StrategyConfigPanel />
          </div>
        </div>
      </main>
    </div>
  );
}
