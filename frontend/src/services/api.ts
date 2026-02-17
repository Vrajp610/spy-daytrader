import axios from 'axios';
import type {
  BotStatus, AccountInfo, RiskMetrics, BacktestRequest,
  BacktestResult, StrategyConfig, Trade,
} from '../types';

const api = axios.create({ baseURL: '/api' });

// Trading
export const getStatus = () => api.get<BotStatus>('/trading/status').then(r => r.data);
export const startBot = () => api.post('/trading/start').then(r => r.data);
export const stopBot = () => api.post('/trading/stop').then(r => r.data);
export const setMode = (mode: string, confirmation?: string) =>
  api.post('/trading/mode', { mode, confirmation }).then(r => r.data);
export const getTrades = (limit = 50) =>
  api.get<{ trades: Trade[]; total: number }>('/trading/trades', { params: { limit } }).then(r => r.data);
export const getPosition = () => api.get('/trading/position').then(r => r.data);

// Account
export const getAccountInfo = () => api.get<AccountInfo>('/account/info').then(r => r.data);
export const getRiskMetrics = () => api.get<RiskMetrics>('/account/risk').then(r => r.data);
export const getDailyPerformance = () => api.get('/account/performance').then(r => r.data);

// Backtest
export const runBacktest = (req: BacktestRequest) =>
  api.post<BacktestResult>('/backtest/run', req).then(r => r.data);
export const getBacktestResults = (limit = 20) =>
  api.get<BacktestResult[]>('/backtest/results', { params: { limit } }).then(r => r.data);

// Settings
export const getStrategyConfigs = () =>
  api.get<StrategyConfig[]>('/settings/strategies').then(r => r.data);
export const updateStrategyConfig = (name: string, update: Partial<StrategyConfig>) =>
  api.put<StrategyConfig>(`/settings/strategies/${name}`, update).then(r => r.data);

// Health
export const getHealth = () => api.get('/health').then(r => r.data);
