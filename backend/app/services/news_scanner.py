"""News sentiment scanner — fetches SPY/macro headlines and scores daily risk.

Primary path: Claude Haiku analyses headlines and returns structured JSON:
  {risk_level, sector_sentiment, key_catalysts, reasoning}

Fallback path: keyword scoring when the API is unavailable or returns an error.

Risk levels
-----------
LOW    — no concerning signals; all strategies run normally
MEDIUM — elevated uncertainty; 8% confidence penalty applied to all candidates
HIGH   — major macro shock detected; all new entries blocked

Cache: ./data_cache/news_scanner.json, refreshed every 4 hours.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]

# ── Keyword fallback ───────────────────────────────────────────────────────────
# Used when the LLM call fails. Counts matching headlines.

_NEGATIVE_KEYWORDS: list[str] = [
    "crash", "selloff", "sell-off", "plunge", "plummets", "tumble", "slump",
    "recession", "stagflation", "bear market", "market correction",
    "rate hike", "rate increase", "tightening", "hawkish",
    "tariff", "trade war", "trade dispute", "sanctions", "embargo",
    "debt ceiling", "default", "credit downgrade",
    "geopolitical", "military", "escalation",
    "inflation surges", "inflation spikes",
    "mass layoffs", "large layoffs",
    "bank failure", "banking crisis", "contagion",
    "earnings miss", "profit warning", "guidance cut",
]

_HIGH_IMPACT_KEYWORDS: list[str] = [
    "federal reserve", "fed chair", "powell", "fomc", "rate decision",
    "interest rate", "monetary policy",
    "nonfarm payroll", "jobs report", "unemployment rate",
    "consumer price", "cpi", "inflation data", "pce",
    "gross domestic product", "gdp",
    "debt ceiling", "treasury",
]

_CACHE_FILE = Path("./data_cache/news_scanner.json")
_CACHE_TTL_HOURS = 4

# ── LLM prompt ────────────────────────────────────────────────────────────────
_LLM_PROMPT_TEMPLATE = """\
You are a macro risk analyst for SPY options day trading.

Given these recent market headlines, assess today's trading risk level and \
sector sentiment. Be direct and concise.

HEADLINES:
{headlines}

Respond with ONLY valid JSON, no markdown, no explanation outside the JSON:
{{
  "risk_level": "LOW",
  "sector_sentiment": {{
    "broad_market": "neutral",
    "tech": "neutral",
    "financials": "neutral",
    "rates": "neutral",
    "energy": "neutral"
  }},
  "key_catalysts": [],
  "reasoning": "one sentence"
}}

Risk level guide:
- HIGH: Major shock event (Fed surprise, banking crisis, geopolitical escalation, \
recession confirmation, systemic stress)
- MEDIUM: Elevated uncertainty (scheduled high-impact release today, significant \
tariff/trade news, conflicting macro signals, moderate fear language)
- LOW: Normal market conditions, no dominant negative catalysts

Sector sentiment values must be exactly: "bullish", "neutral", or "bearish".
"""


# ── Main class ────────────────────────────────────────────────────────────────

class NewsScanner:
    """
    Fetches recent SPY/VIX/GSPC headlines via yfinance and scores them using
    Claude Haiku.  Falls back to keyword scoring if the LLM is unavailable.
    """

    def __init__(self) -> None:
        self._daily_risk: RiskLevel = "LOW"
        self._sector_sentiment: dict[str, str] = {}
        self._key_catalysts: list[str] = []
        self._risk_reasoning: str = ""
        self._risk_reasons: list[str] = []          # backward-compat headline list
        self._last_date: date | None = None
        self._last_fetch: datetime | None = None
        self._llm_available: bool = True            # cleared on repeated failures

    # ── Public interface ──────────────────────────────────────────────────────

    def get_daily_risk(self) -> RiskLevel:
        """Return today's scored risk level (LOW / MEDIUM / HIGH)."""
        return self._daily_risk

    def get_sector_sentiment(self) -> dict[str, str]:
        """Return per-sector sentiment dict (broad_market/tech/financials/rates/energy)."""
        return self._sector_sentiment

    def get_key_catalysts(self) -> list[str]:
        """Return list of key catalyst strings identified by the LLM."""
        return self._key_catalysts

    def get_risk_reasoning(self) -> str:
        """Return the LLM's one-sentence reasoning for the risk level."""
        return self._risk_reasoning

    def get_risk_reasons(self) -> list[str]:
        """Return matching headline snippets (fallback path or keyword matches)."""
        return self._risk_reasons

    async def ensure_fresh(self) -> None:
        """Refresh if the cache is stale, a new day, or never loaded."""
        if self._last_fetch is None:
            self._load_cache()

        today = datetime.now(ET).date()
        needs_refresh = (
            self._last_fetch is None
            or self._last_date != today
            or (datetime.now() - self._last_fetch).total_seconds() / 3600 >= _CACHE_TTL_HOURS
        )
        if needs_refresh:
            await self.refresh()

    async def refresh(self) -> None:
        """Fetch latest headlines, score with LLM (or keyword fallback), persist cache."""
        import asyncio
        loop = asyncio.get_running_loop()
        headlines = await loop.run_in_executor(None, self._fetch_headlines)

        if not headlines:
            logger.warning("NewsScanner: no headlines fetched — keeping previous risk level")
            return

        # ── LLM path ──────────────────────────────────────────────────────────
        llm_result = None
        if self._llm_available:
            llm_result = await loop.run_in_executor(
                None, self._score_with_llm, headlines
            )

        if llm_result:
            self._daily_risk = llm_result.get("risk_level", "LOW")
            self._sector_sentiment = llm_result.get("sector_sentiment", {})
            self._key_catalysts = llm_result.get("key_catalysts", [])
            self._risk_reasoning = llm_result.get("reasoning", "")
            self._risk_reasons = self._key_catalysts[:]
            logger.info(
                f"NewsScanner[LLM]: risk={self._daily_risk} | "
                f"broad={self._sector_sentiment.get('broad_market','?')} | "
                f"{self._risk_reasoning[:80]}"
            )
        else:
            # ── Keyword fallback ───────────────────────────────────────────────
            self._score_with_keywords(headlines)
            logger.info(
                f"NewsScanner[keywords]: risk={self._daily_risk} "
                f"from {len(headlines)} headlines"
            )

        self._last_date = datetime.now(ET).date()
        self._last_fetch = datetime.now()
        self._save_cache()

    # ── LLM scoring ──────────────────────────────────────────────────────────

    def _score_with_llm(self, headlines: list[str]) -> dict | None:
        """Blocking: call Claude Haiku to score headlines. Returns parsed dict or None."""
        try:
            import anthropic
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

            headlines_text = "\n".join(f"- {h}" for h in headlines[:35])
            prompt = _LLM_PROMPT_TEMPLATE.format(headlines=headlines_text)

            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()

            # Extract JSON even if the model added surrounding text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < 0 or end <= start:
                logger.debug(f"NewsScanner LLM: no JSON in response: {text[:100]}")
                return None

            result = json.loads(text[start:end])
            # Validate risk_level
            if result.get("risk_level") not in ("LOW", "MEDIUM", "HIGH"):
                result["risk_level"] = "LOW"
            return result

        except Exception as exc:
            err = str(exc)
            if "api_key" in err.lower() or "authentication" in err.lower():
                logger.warning(
                    "NewsScanner: ANTHROPIC_API_KEY not set or invalid — "
                    "falling back to keyword scoring permanently this session"
                )
                self._llm_available = False
            else:
                logger.warning(f"NewsScanner: LLM scoring failed: {exc}")
            return None

    # ── Keyword fallback ──────────────────────────────────────────────────────

    def _score_with_keywords(self, headlines: list[str]) -> None:
        """Score headlines using keyword lists (no API required)."""
        negative_count = 0
        high_impact_count = 0
        reasons: list[str] = []

        for headline in headlines:
            h_lower = headline.lower()
            matched_neg = False
            matched_hi = False

            for kw in _NEGATIVE_KEYWORDS:
                if kw in h_lower and not matched_neg:
                    negative_count += 1
                    reasons.append(f"[-] {headline[:70]}")
                    matched_neg = True

            for kw in _HIGH_IMPACT_KEYWORDS:
                if kw in h_lower and not matched_hi:
                    high_impact_count += 1
                    if not matched_neg:
                        reasons.append(f"[!] {headline[:70]}")
                    matched_hi = True

            if negative_count >= 4 and high_impact_count >= 2:
                break

        if high_impact_count >= 2 or negative_count >= 4:
            self._daily_risk = "HIGH"
        elif high_impact_count >= 1 or negative_count >= 2:
            self._daily_risk = "MEDIUM"
        else:
            self._daily_risk = "LOW"

        self._risk_reasons = reasons[:6]
        self._sector_sentiment = {}
        self._key_catalysts = []
        self._risk_reasoning = f"keyword: neg={negative_count}, hi_impact={high_impact_count}"

    # ── Headline fetch ────────────────────────────────────────────────────────

    def _fetch_headlines(self) -> list[str]:
        """Blocking: fetch recent news titles for SPY, ^VIX, ^GSPC."""
        headlines: list[str] = []
        try:
            import yfinance as yf

            for ticker_sym in ("SPY", "^VIX", "^GSPC"):
                try:
                    t = yf.Ticker(ticker_sym)
                    items = t.news or []
                    for item in items[:15]:
                        title = (
                            item.get("title")
                            or (item.get("content") or {}).get("title", "")
                        )
                        if title and title not in headlines:
                            headlines.append(title)
                except Exception as exc:
                    logger.debug(f"NewsScanner: {ticker_sym} fetch error: {exc}")

            logger.debug(f"NewsScanner: fetched {len(headlines)} unique headlines")
        except Exception as exc:
            logger.warning(f"NewsScanner: headline fetch failed: {exc}")
        return headlines

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _save_cache(self) -> None:
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_CACHE_FILE, "w") as f:
                json.dump(
                    {
                        "date": self._last_date.isoformat() if self._last_date else None,
                        "fetched_at": self._last_fetch.isoformat() if self._last_fetch else None,
                        "risk": self._daily_risk,
                        "sector_sentiment": self._sector_sentiment,
                        "key_catalysts": self._key_catalysts,
                        "reasoning": self._risk_reasoning,
                        "reasons": self._risk_reasons,
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.warning(f"NewsScanner: cache save failed: {exc}")

    def _load_cache(self) -> None:
        try:
            if not _CACHE_FILE.exists():
                return
            with open(_CACHE_FILE) as f:
                data = json.load(f)

            cached_date_str = data.get("date")
            if not cached_date_str:
                return
            cached_date = date.fromisoformat(cached_date_str)
            if cached_date != datetime.now(ET).date():
                return  # stale — different day

            self._daily_risk = data.get("risk", "LOW")
            self._sector_sentiment = data.get("sector_sentiment", {})
            self._key_catalysts = data.get("key_catalysts", [])
            self._risk_reasoning = data.get("reasoning", "")
            self._risk_reasons = data.get("reasons", [])
            self._last_date = cached_date
            fa = data.get("fetched_at")
            self._last_fetch = datetime.fromisoformat(fa) if fa else None
            logger.info(f"NewsScanner: loaded from cache — risk={self._daily_risk}")
        except Exception as exc:
            logger.warning(f"NewsScanner: cache load failed: {exc}")


# ── Singleton ─────────────────────────────────────────────────────────────────
news_scanner = NewsScanner()
