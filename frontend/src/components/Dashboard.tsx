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
    <div className="min-h-screen bg-terminal-950 bg-surface-noise text-terminal-100">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-terminal-900/90 backdrop-blur-md border-b border-terminal-600/30 shadow-inset-top px-5 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-display font-bold tracking-tight">
            <span className="text-accent">///</span>SPY DayTrader
          </h1>
          <span className="badge badge-paper">v1.0</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${connected ? 'bg-profit animate-pulse-slow' : 'bg-loss'}`} />
            <span className="text-muted text-xs">{connected ? 'Connected' : 'Offline'}</span>
          </div>
          {lastMessage?.type === 'price_update' && (
            <span className="font-mono text-sm text-accent bg-terminal-800/60 px-2.5 py-0.5 rounded-md">
              SPY ${(lastMessage.data as { price?: number }).price?.toFixed(2) ?? '--'}
            </span>
          )}
        </div>
      </header>

      {/* Main Grid */}
      <main className="p-3 md:p-5 space-y-3">
        {/* Top row: Controls + Summary */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
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
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          <div className="lg:col-span-2">
            <BacktestPanel />
          </div>
          <div className="space-y-3">
            <TradingSettingsPanel />
            <StrategyConfigPanel />
          </div>
        </div>
      </main>
    </div>
  );
}
