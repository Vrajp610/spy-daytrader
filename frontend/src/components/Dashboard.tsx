import { useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import { useAccount } from '../hooks/useAccount';
import { useTrades } from '../hooks/useTrades';
import BotControls from './BotControls';
import PnLCard from './PnLCard';
import PositionCard from './PositionCard';
import RiskMetricsCard from './RiskMetrics';
import AlertsFeed from './AlertsFeed';
import TradeHistory from './TradeHistory';
import BacktestPanel from './BacktestPanel';
import StrategyConfigPanel from './StrategyConfig';
import TradingSettingsPanel from './TradingSettings';
import StrategyLeaderboard from './StrategyLeaderboard';

type SlidePanel = 'settings' | 'strategies' | null;

export default function Dashboard() {
  const { lastMessage, connected } = useWebSocket();
  const { account, risk } = useAccount();
  const { trades, total } = useTrades();
  const [openPanel, setOpenPanel] = useState<SlidePanel>(null);

  const togglePanel = (panel: SlidePanel) => {
    setOpenPanel(prev => prev === panel ? null : panel);
  };

  return (
    <div className="min-h-screen bg-terminal-950 bg-surface-noise text-terminal-100">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-terminal-900/90 backdrop-blur-md border-b border-terminal-600/30 shadow-inset-top px-3 sm:px-5 py-2.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <h1 className="text-base sm:text-lg font-display font-bold tracking-tight whitespace-nowrap">
            <span className="text-accent">///</span><span className="hidden xs:inline">SPY </span>DayTrader
          </h1>
          <span className="badge badge-paper hidden sm:inline-flex">v1.0</span>
        </div>
        <div className="flex items-center gap-1.5 sm:gap-3 text-sm">
          <button
            onClick={() => togglePanel('settings')}
            className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-all ${
              openPanel === 'settings'
                ? 'bg-accent text-white'
                : 'bg-terminal-700/50 hover:bg-terminal-600/50 text-terminal-200'
            }`}
          >
            ⚙ <span className="hidden sm:inline">Settings</span>
          </button>
          <button
            onClick={() => togglePanel('strategies')}
            className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-all ${
              openPanel === 'strategies'
                ? 'bg-accent text-white'
                : 'bg-terminal-700/50 hover:bg-terminal-600/50 text-terminal-200'
            }`}
          >
            ☰ <span className="hidden sm:inline">Strategies</span>
          </button>
          <div className="w-px h-5 bg-terminal-600/30 hidden sm:block" />
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full shrink-0 ${connected ? 'bg-profit animate-pulse-slow' : 'bg-loss'}`} />
            <span className="text-muted text-xs hidden md:inline">{connected ? 'Live' : 'Offline'}</span>
          </div>
          {lastMessage?.type === 'price_update' && (
            <span className="font-mono text-xs sm:text-sm text-accent bg-terminal-800/60 px-2 sm:px-2.5 py-0.5 rounded-md whitespace-nowrap">
              ${(lastMessage.data as { price?: number }).price?.toFixed(2) ?? '--'}
              <span className="hidden sm:inline"> SPY</span>
            </span>
          )}
        </div>
      </header>

      {/* Slide-out panel overlay */}
      {openPanel && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm animate-fade-in"
            onClick={() => setOpenPanel(null)}
          />
          <div className="fixed top-[49px] right-0 z-50 w-full sm:max-w-md h-[calc(100vh-49px)] overflow-y-auto bg-terminal-900 border-l border-terminal-600/30 shadow-2xl animate-fade-in">
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-terminal-200 uppercase tracking-wider">
                  {openPanel === 'settings' ? 'Trading Settings' : 'Strategy Config'}
                </h2>
                <button
                  onClick={() => setOpenPanel(null)}
                  className="text-muted hover:text-terminal-100 text-lg leading-none px-1"
                >
                  &times;
                </button>
              </div>
              {openPanel === 'settings' && <TradingSettingsPanel />}
              {openPanel === 'strategies' && <StrategyConfigPanel />}
            </div>
          </div>
        </>
      )}

      {/* Main Grid */}
      <main className="p-3 md:p-5 space-y-3">
        {/* Top row: Controls + Summary */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <BotControls />
          <PnLCard account={account} />
          <PositionCard />
          <RiskMetricsCard risk={risk} />
        </div>

        {/* Alerts Feed */}
        <AlertsFeed lastMessage={lastMessage} />

        {/* Middle: Trade History */}
        <TradeHistory trades={trades} total={total} />

        {/* Strategy Leaderboard */}
        <StrategyLeaderboard />

        {/* Bottom: Backtesting */}
        <BacktestPanel />
      </main>
    </div>
  );
}
