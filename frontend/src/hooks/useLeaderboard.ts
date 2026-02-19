import { useState, useEffect, useCallback } from 'react';
import { getLeaderboardRankings, getLeaderboardComparison } from '../services/api';
import type { StrategyRanking, LeaderboardProgress, StrategyComparison } from '../types';

export function useLeaderboard() {
  const [rankings, setRankings] = useState<StrategyRanking[]>([]);
  const [progress, setProgress] = useState<LeaderboardProgress>({
    status: 'idle',
    current_test: '',
    completed: 0,
    total: 0,
    errors: 0,
    last_run: null,
  });
  const [comparisons, setComparisons] = useState<StrategyComparison[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getLeaderboardRankings();
      setRankings(data.rankings);
      setProgress(data.progress);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  const loadComparisons = useCallback(async () => {
    try {
      setComparisons(await getLeaderboardComparison());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refresh();
    loadComparisons();

    // Poll every 30 seconds
    const interval = setInterval(() => {
      refresh();
    }, 30000);

    return () => clearInterval(interval);
  }, [refresh, loadComparisons]);

  return { rankings, progress, comparisons, loading, refresh, loadComparisons };
}
