import { useState, useEffect, useCallback } from 'react';
import { getAccountInfo, getRiskMetrics } from '../services/api';
import type { AccountInfo, RiskMetrics } from '../types';

export function useAccount() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [risk, setRisk] = useState<RiskMetrics | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [a, r] = await Promise.all([getAccountInfo(), getRiskMetrics()]);
      setAccount(a);
      setRisk(r);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { account, risk, refresh };
}
