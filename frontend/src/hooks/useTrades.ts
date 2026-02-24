import { useState, useEffect, useCallback } from 'react';
import { getTrades } from '../services/api';
import type { Trade } from '../types';

export function useTrades() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getTrades();
      setTrades(data.trades);
      setTotal(data.total);
      setError(null);
    } catch {
      setError('Failed to fetch trade data');
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { trades, total, refresh, error };
}
