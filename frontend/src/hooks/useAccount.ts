import { useState, useEffect, useCallback } from 'react';
import { getAccountInfo, getRiskMetrics } from '../services/api';
import type { AccountInfo, RiskMetrics } from '../types';

export function useAccount() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [risk, setRisk] = useState<RiskMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [a, r] = await Promise.all([getAccountInfo(), getRiskMetrics()]);
      setAccount(a);
      setRisk(r);
      setError(null);
    } catch {
      setError('Failed to fetch account data');
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { account, risk, refresh, error };
}
