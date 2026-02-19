import { useState, useCallback } from 'react';
import { runBacktest, getBacktestResults } from '../services/api';
import type { BacktestRequest, BacktestResult } from '../types';

export function useBacktest() {
  const [results, setResults] = useState<BacktestResult[]>([]);
  const [current, setCurrent] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (req: BacktestRequest) => {
    setLoading(true);
    setError(null);
    try {
      const result = await runBacktest(req);
      setCurrent(result);
      setResults(prev => [result, ...prev]);
      return result;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Backtest failed';
      setError(msg);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const data = await getBacktestResults();
      setResults(data);
      if (data.length > 0 && !current) {
        setCurrent(data[0]);
      }
    } catch {
      setError('Failed to load backtest history');
    }
  }, [current]);

  return { results, current, loading, error, run, loadHistory };
}
