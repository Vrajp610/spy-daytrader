"""Black-Scholes options pricing and Greeks calculator."""

from __future__ import annotations
import math
from typing import Optional

from app.services.options.models import OptionType


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def black_scholes_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: OptionType,
) -> float:
    """Compute Black-Scholes option price.

    Args:
        S: Underlying price
        K: Strike price
        T: Time to expiration in years
        r: Risk-free rate (annualized)
        sigma: Implied volatility (annualized)
        option_type: CALL or PUT
    """
    if T <= 0:
        # At expiration — intrinsic value only
        if option_type == OptionType.CALL:
            return max(0.0, S - K)
        return max(0.0, K - S)

    d1_val = _d1(S, K, T, r, sigma)
    d2_val = _d2(S, K, T, r, sigma)

    if option_type == OptionType.CALL:
        return S * _norm_cdf(d1_val) - K * math.exp(-r * T) * _norm_cdf(d2_val)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2_val) - S * _norm_cdf(-d1_val)


def delta(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: OptionType,
) -> float:
    """Option delta."""
    if T <= 0 or sigma <= 0:
        if option_type == OptionType.CALL:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0

    d1_val = _d1(S, K, T, r, sigma)
    if option_type == OptionType.CALL:
        return _norm_cdf(d1_val)
    return _norm_cdf(d1_val) - 1.0


def gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Option gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1_val = _d1(S, K, T, r, sigma)
    return _norm_pdf(d1_val) / (S * sigma * math.sqrt(T))


def theta(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: OptionType,
) -> float:
    """Option theta (per day)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1_val = _d1(S, K, T, r, sigma)
    d2_val = _d2(S, K, T, r, sigma)

    common = -(S * _norm_pdf(d1_val) * sigma) / (2.0 * math.sqrt(T))

    if option_type == OptionType.CALL:
        annual_theta = common - r * K * math.exp(-r * T) * _norm_cdf(d2_val)
    else:
        annual_theta = common + r * K * math.exp(-r * T) * _norm_cdf(-d2_val)

    return annual_theta / 365.0  # per calendar day


def vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Option vega (per 1% change in IV)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1_val = _d1(S, K, T, r, sigma)
    return S * _norm_pdf(d1_val) * math.sqrt(T) / 100.0


def estimate_premium_change(
    delta_val: float, gamma_val: float, theta_val: float,
    dS: float, dt_days: float,
) -> float:
    """Estimate premium change using Greeks approximation.

    delta*dS + 0.5*gamma*dS^2 + theta*dt
    """
    return delta_val * dS + 0.5 * gamma_val * dS * dS + theta_val * dt_days


def iv_from_atr(atr: float, price: float, bar_minutes: int = 1) -> float:
    """Estimate annualized IV from ATR.

    Args:
        atr: Average True Range value
        price: Current underlying price
        bar_minutes: Timeframe of the ATR in minutes (1 for 1-min bars, 1440 for daily)

    Uses the relationship: daily_vol ≈ ATR / (price * 1.4)
    For intraday ATR, first scales to daily using sqrt(minutes_per_day / bar_minutes).
    Then annualizes: IV = daily_vol * sqrt(252)
    """
    if price <= 0 or atr <= 0:
        return 0.20  # default 20%

    # Scale intraday ATR to daily ATR
    # A trading day has 390 minutes (6.5 hours)
    import math as _math
    if bar_minutes < 1440:
        bars_per_day = 390 / max(1, bar_minutes)
        daily_atr = atr * _math.sqrt(bars_per_day)
    else:
        daily_atr = atr

    daily_vol = daily_atr / (price * 1.4)
    iv = daily_vol * math.sqrt(252)

    # Clamp to reasonable range: 8% - 120%
    return max(0.08, min(iv, 1.20))
