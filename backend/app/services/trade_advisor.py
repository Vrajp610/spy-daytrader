"""Adversarial pre-trade consultant — pressure-tests every proposed trade.

Inspired by Claude_Prophet's "Consultant Agent" pattern: before any position is
opened, an LLM is asked to find specific reasons the trade could fail right now.
This catches situations where multiple technical signals agree but the macro
environment makes the trade structurally unsound.

Verdicts
--------
PROCEED  — no critical flaws; execute normally
REDUCE   — valid trade but specific risk warrants halving position size
BLOCK    — critical flaw identified; skip this trade

The call is made with Claude Haiku (cheap, fast ~1-2s).
A result is cached by trade fingerprint for 60 seconds to avoid duplicate API
calls when the same setup persists across multiple 5-second loop ticks.

Falls back to PROCEED immediately if:
  - ANTHROPIC_API_KEY is not set
  - The API call fails for any reason
  - The response cannot be parsed

This ensures the trading loop is never blocked by a network issue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time as _time
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.services.strategies.base import TradeSignal

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# ── Cache ─────────────────────────────────────────────────────────────────────
_verdict_cache: dict[str, tuple[dict, float]] = {}   # fingerprint → (verdict, expire_ts)
_CACHE_TTL_SECS = 60
_advisor_available: bool = True                       # set False after auth failures


# ── Prompt template ───────────────────────────────────────────────────────────
_PROMPT = """\
You are an adversarial trading consultant reviewing a proposed SPY options trade.
Your job: find SPECIFIC reasons it could fail RIGHT NOW, given the market context.
Be skeptical but fair. Most trades should PROCEED or REDUCE — only BLOCK for \
genuinely critical, specific flaws.

PROPOSED TRADE
--------------
Strategy      : {strategy}
Direction     : {direction}
Confidence    : {confidence:.0%}
Options type  : {options_pref}
Target delta  : {target_delta}
DTE target    : {dte}

MARKET CONTEXT
--------------
Regime        : {regime}
VIX           : {vix:.1f}
News risk     : {news_risk}
Upcoming events (7d): {events}
Today P&L     : ${daily_pnl:+.0f}
Portfolio delta: {portfolio_delta:+.3f}

Trade memory  : {memory_verdict} (similar past WR={memory_wr})

Respond with ONLY valid JSON, no markdown:
{{
  "verdict": "PROCEED",
  "risk_factors": [],
  "reasoning": "one sentence"
}}

verdict must be exactly "PROCEED", "REDUCE", or "BLOCK".
risk_factors is a list of short strings (max 3 items).
BLOCK only if the trade has a clear, specific, critical flaw — not just generic risk.
REDUCE if 1-2 real concerns exist but don't outweigh the signal.
"""


# ── Public API ────────────────────────────────────────────────────────────────

async def assess_trade(
    signal: "TradeSignal",
    regime_str: str,
    vix: float,
    news_risk: str,
    upcoming_events: list[dict],
    daily_pnl: float,
    portfolio_delta: float,
    memory_result: dict | None = None,
) -> dict:
    """
    Pressure-test a proposed trade using Claude Haiku.

    Returns a verdict dict:
      {
        "verdict": "PROCEED" | "REDUCE" | "BLOCK",
        "risk_factors": [...],
        "reasoning": "...",
      }

    Always returns a dict (never raises); falls back to PROCEED on any error.
    """
    global _advisor_available

    # ── Cache lookup ──────────────────────────────────────────────────────────
    fp = _fingerprint(signal, regime_str, news_risk)
    cached = _verdict_cache.get(fp)
    if cached:
        verdict_dict, expire_ts = cached
        if _time.monotonic() < expire_ts:
            logger.debug(f"TradeAdvisor: cache hit ({fp[:8]}…) → {verdict_dict['verdict']}")
            return verdict_dict

    if not _advisor_available:
        return _proceed("API unavailable")

    # ── Build prompt ──────────────────────────────────────────────────────────
    events_str = (
        ", ".join(e["title"] for e in upcoming_events[:3])
        if upcoming_events else "none scheduled"
    )
    mem = memory_result or {}
    memory_verdict = mem.get("verdict", "INSUFFICIENT_DATA")
    memory_wr = (
        f"{mem['win_rate']:.0%} (n={mem['similar_count']})"
        if mem.get("win_rate") is not None
        else "N/A"
    )

    prompt = _PROMPT.format(
        strategy=signal.strategy,
        direction=signal.direction.value,
        confidence=signal.confidence,
        options_pref=signal.metadata.get("options_preference", "standard"),
        target_delta=signal.metadata.get("target_delta", 0.20),
        dte=signal.metadata.get("preferred_dte", "default"),
        regime=regime_str,
        vix=vix,
        news_risk=news_risk,
        events=events_str,
        daily_pnl=daily_pnl,
        portfolio_delta=portfolio_delta,
        memory_verdict=memory_verdict,
        memory_wr=memory_wr,
    )

    # ── LLM call (in executor so we don't block the async loop) ──────────────
    import asyncio
    loop = asyncio.get_running_loop()

    def _call() -> str:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    try:
        text = await loop.run_in_executor(None, _call)
        result = _parse(text)
    except Exception as exc:
        err = str(exc)
        if "api_key" in err.lower() or "authentication" in err.lower():
            logger.warning(
                "TradeAdvisor: ANTHROPIC_API_KEY not set — "
                "adversarial checks disabled for this session"
            )
            _advisor_available = False
        else:
            logger.debug(f"TradeAdvisor: LLM call failed ({exc}) — defaulting PROCEED")
        return _proceed(f"API error: {exc}")

    # ── Log non-trivial verdicts ──────────────────────────────────────────────
    verdict = result.get("verdict", "PROCEED")
    if verdict != "PROCEED":
        factors = "; ".join(result.get("risk_factors", []))
        logger.info(
            f"TradeAdvisor: {verdict} for {signal.strategy} "
            f"({signal.direction.value}) | {factors}"
        )

    # ── Cache and return ──────────────────────────────────────────────────────
    _verdict_cache[fp] = (result, _time.monotonic() + _CACHE_TTL_SECS)
    # Prune old cache entries
    if len(_verdict_cache) > 200:
        now = _time.monotonic()
        expired = [k for k, (_, ts) in _verdict_cache.items() if ts < now]
        for k in expired:
            del _verdict_cache[k]

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fingerprint(signal: "TradeSignal", regime: str, news_risk: str) -> str:
    """Short hash that identifies a (strategy, direction, regime, news_risk) combo."""
    key = f"{signal.strategy}:{signal.direction.value}:{regime}:{news_risk}"
    return hashlib.md5(key.encode()).hexdigest()


def _parse(text: str) -> dict:
    """Extract the first JSON object from the LLM response."""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return _proceed("no JSON in response")
    try:
        result = json.loads(text[start:end])
        if result.get("verdict") not in ("PROCEED", "REDUCE", "BLOCK"):
            result["verdict"] = "PROCEED"
        return result
    except json.JSONDecodeError:
        return _proceed("JSON parse error")


def _proceed(reason: str = "") -> dict:
    return {
        "verdict": "PROCEED",
        "risk_factors": [],
        "reasoning": reason or "default pass-through",
    }
