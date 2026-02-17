import { useState, useEffect, useCallback } from 'react';
import { getTrades } from '../services/api';
import type { Trade } from '../types';

export function useTrades() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const data = await getTrades(100);
      setTrades(data.trades);
      setTotal(data.total);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { trades, total, refresh };
}
