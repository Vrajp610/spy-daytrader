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
import PortfolioAnalytics from './PortfolioAnalytics';

type ActiveTab = 'dashboard' | 'backtest' | 'analytics' | 'settings';

export default function Dashboard() {
  const { lastMessage, connected } = useWebSocket();
  const { account, risk } = useAccount();
  const { trades, total } = useTrades();
  const [activeTab, setActiveTab] = useState<ActiveTab>('dashboard');
  const [settingsOpen, setSettingsOpen] = useState<'trading' | 'strategies' | null>(null);

  return (
    <div className="min-h-screen bg-terminal-950 text-terminal-100 flex flex-col">

      {/* ── Header ── */}
      <header className="sticky top-0 z-50 bg-terminal-900 border-b border-terminal-600/60 flex items-stretch h-12">

        {/* Logo box */}
        <div className="flex items-center justify-center w-10 h-full bg-profit shrink-0">
          <span className="text-terminal-950 text-xs font-bold">S</span>
        </div>

        {/* Wordmark */}
        <div className="flex items-center px-4 border-r border-terminal-600/40">
          <span className="font-display text-sm font-bold tracking-widest text-terminal-100 whitespace-nowrap">
            SPY DAYTRADER
          </span>
        </div>

        {/* Nav tabs */}
        <nav className="hidden sm:flex items-stretch">
          {(['dashboard', 'backtest', 'analytics', 'settings'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`relative px-5 h-full text-xxs font-bold uppercase tracking-widest transition-colors border-r border-terminal-600/20 ${
                activeTab === tab
                  ? 'text-profit'
                  : 'text-muted hover:text-terminal-200'
              }`}
            >
              {tab}
              {activeTab === tab && (
                <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-profit" />
              )}
            </button>
          ))}
        </nav>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Right side: status + price */}
        <div className="flex items-center gap-3 px-4">
          {/* WS status */}
          <div className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 shrink-0 ${connected ? 'bg-profit animate-pulse-slow' : 'bg-loss'}`} />
            <span className="text-xxs text-muted uppercase tracking-terminal hidden md:inline">
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>

          {/* SPY price */}
          {lastMessage?.type === 'price_update' && (
            <span className="font-mono text-xs text-profit bg-profit/10 border border-profit/20 px-2.5 py-1 whitespace-nowrap">
              ${(lastMessage.data as { price?: number }).price?.toFixed(2) ?? '--'}
              <span className="hidden sm:inline text-muted ml-1">SPY</span>
            </span>
          )}

          {/* Mobile settings toggle */}
          <div className="flex sm:hidden gap-1">
            <button
              onClick={() => setSettingsOpen(settingsOpen === 'trading' ? null : 'trading')}
              className={`px-2 py-1 text-xxs font-bold uppercase tracking-terminal transition-colors ${
                settingsOpen === 'trading' ? 'text-profit bg-profit/10 border border-profit/30' : 'text-muted border border-terminal-600/40'
              }`}
            >
              ⚙
            </button>
            <button
              onClick={() => setSettingsOpen(settingsOpen === 'strategies' ? null : 'strategies')}
              className={`px-2 py-1 text-xxs font-bold uppercase tracking-terminal transition-colors ${
                settingsOpen === 'strategies' ? 'text-profit bg-profit/10 border border-profit/30' : 'text-muted border border-terminal-600/40'
              }`}
            >
              ☰
            </button>
          </div>
        </div>
      </header>

      {/* ── Mobile slide-out panel (settings/strategies) ── */}
      {settingsOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/60 animate-fade-in"
            onClick={() => setSettingsOpen(null)}
          />
          <div className="fixed top-12 right-0 z-50 w-full sm:max-w-md h-[calc(100vh-48px)] overflow-y-auto bg-terminal-900 border-l border-terminal-600/40 animate-fade-in">
            <div className="p-4 border-b border-terminal-600/30 flex items-center justify-between">
              <span className="text-xxs font-bold uppercase tracking-widest text-terminal-300">
                {settingsOpen === 'trading' ? 'Trading Settings' : 'Strategy Config'}
              </span>
              <button
                onClick={() => setSettingsOpen(null)}
                className="text-muted hover:text-terminal-100 text-lg leading-none px-1"
              >
                &times;
              </button>
            </div>
            <div className="p-4">
              {settingsOpen === 'trading' && <TradingSettingsPanel />}
              {settingsOpen === 'strategies' && <StrategyConfigPanel />}
            </div>
          </div>
        </>
      )}

      {/* ── Main Content ── */}
      <main className="flex-1 p-3 md:p-4 space-y-3">

        {/* DASHBOARD tab */}
        {activeTab === 'dashboard' && (
          <>
            {/* Top row: Controls + Summary cards */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <BotControls />
              <PnLCard account={account} />
              <PositionCard />
              <RiskMetricsCard risk={risk} />
            </div>

            {/* Alerts */}
            <AlertsFeed lastMessage={lastMessage} />

            {/* Trade History */}
            <TradeHistory trades={trades} total={total} />

            {/* Strategy Leaderboard */}
            <StrategyLeaderboard />
          </>
        )}

        {/* BACKTEST tab */}
        {activeTab === 'backtest' && <BacktestPanel />}

        {/* ANALYTICS tab */}
        {activeTab === 'analytics' && <PortfolioAnalytics />}

        {/* SETTINGS tab */}
        {activeTab === 'settings' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="card p-4">
              <div className="card-header">
                <h2 className="card-title">Trading Settings</h2>
              </div>
              <TradingSettingsPanel />
            </div>
            <div className="card p-4">
              <div className="card-header">
                <h2 className="card-title">Strategy Config</h2>
              </div>
              <StrategyConfigPanel />
            </div>
          </div>
        )}
      </main>

      {/* ── Footer status bar ── */}
      <footer className="h-6 bg-terminal-900 border-t border-terminal-600/40 flex items-center px-4 gap-4">
        <span className="text-xxs text-muted uppercase tracking-terminal">
          SPY DayTrader v1.0
        </span>
        <span className="w-px h-3 bg-terminal-600/40" />
        <span className={`text-xxs uppercase tracking-terminal font-bold ${connected ? 'text-profit' : 'text-loss'}`}>
          {connected ? '● CONNECTED' : '○ DISCONNECTED'}
        </span>
      </footer>
    </div>
  );
}
