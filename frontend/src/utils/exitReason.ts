/**
 * Converts raw backend exit_reason codes into human-readable labels.
 *
 * Backend produces codes like:
 *   "eod", "take_profit_50%_max", "stop_loss_3.0x_credit",
 *   "trailing_stop_20%_from_best", "theta_time_stop_eod",
 *   "bot_stopped", "webhook_close"
 */
export function formatExitReason(raw: string | undefined | null): string {
  if (!raw) return '—';
  const r = raw.toLowerCase();

  if (r === 'eod' || r === 'end_of_day')        return 'End of day';
  if (r === 'theta_time_stop_eod')               return 'Theta decay — end of day';
  if (r === 'bot_stopped')                       return 'Bot stopped';
  if (r === 'webhook_close')                     return 'Manual close (webhook)';
  if (r === 'manual')                            return 'Manually closed';
  if (r === 'circuit_breaker')                   return 'Circuit breaker';

  if (r.startsWith('take_profit')) {
    const pct = raw.match(/([\d.]+)%/)?.[1];
    if (r.includes('max'))    return pct ? `Profit target (${pct}% of max)` : 'Profit target hit';
    if (r.includes('premium')) return pct ? `Profit target (${pct}% gain)` : 'Profit target hit';
    return 'Profit target hit';
  }

  if (r.startsWith('stop_loss')) {
    const mult = raw.match(/([\d.]+)x/)?.[1];
    if (r.includes('credit'))  return mult ? `Stop loss (${mult}× credit)` : 'Stop loss hit';
    if (r.includes('premium')) return mult ? `Stop loss (${mult}× premium)` : 'Stop loss hit';
    return 'Stop loss hit';
  }

  if (r.startsWith('trailing_stop')) {
    const pct = raw.match(/([\d.]+)%/)?.[1];
    return pct ? `Trailing stop (${pct}% reversal)` : 'Trailing stop hit';
  }

  // Fallback: replace underscores, capitalise first word
  return raw.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
}
