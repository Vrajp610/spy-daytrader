"""Macro event calendar — fetches and caches high-impact USD economic events.

Data sources (tried in order):
  1. ForexFactory public calendar JSON (no auth, refreshed twice daily)
  2. Computed estimates for recurring events (FOMC, CPI, NFP) as a fallback
     when the network fetch fails.

Usage in the trading engine
----------------------------
  from app.services.event_calendar import macro_calendar

  await macro_calendar.ensure_fresh()

  # Block theta_decay if any event falls within its 3-day hold window
  if macro_calendar.is_blackout_window(today, window_days=3):
      ...

  # Block / reduce confidence for ALL strategies on the event day itself
  if macro_calendar.is_event_day(today):
      ...
"""

from __future__ import annotations

import json
import logging
import math
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ── High-impact keyword matching ──────────────────────────────────────────────
# Titles from ForexFactory that we treat as market-moving for SPY/options.
_HIGH_IMPACT_KEYWORDS = [
    "fomc", "federal funds rate", "interest rate decision", "fed rate",
    "cpi", "consumer price index", "core cpi",
    "non-farm payroll", "nonfarm payroll", "nfp",
    "unemployment rate", "jobless claims",
    "gdp", "gross domestic product",
    "ppi", "producer price index",
    "retail sales",
    "jackson hole",
    "treasury", "debt ceiling",
    "jobs report",
    "inflation",
    "core pce", "pce price index",
]

# ── ForexFactory calendar URLs ────────────────────────────────────────────────
_FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; spy-daytrader/1.0)",
    "Accept": "application/json",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_high_impact_usd(item: dict) -> bool:
    """Return True if this ForexFactory entry is a high-impact USD event."""
    if item.get("country", "").upper() != "USD":
        return False
    if item.get("impact", "").lower() != "high":
        return False
    title = item.get("title", "").lower()
    return any(kw in title for kw in _HIGH_IMPACT_KEYWORDS)


def _parse_ff_date(date_str: str) -> Optional[date]:
    """Parse ISO-8601 date string from ForexFactory into a date object."""
    if not date_str:
        return None
    try:
        # e.g. "2026-02-25T14:00:00-05:00" or "2026-02-25T00:00:00"
        return datetime.fromisoformat(date_str[:19]).date()
    except ValueError:
        try:
            return date.fromisoformat(date_str[:10])
        except ValueError:
            return None


# ── Fallback: computed event schedule ────────────────────────────────────────

def _first_friday(year: int, month: int) -> date:
    """Return the first Friday of the given month (NFP release day)."""
    d = date(year, month, 1)
    # weekday(): Mon=0, Fri=4
    offset = (4 - d.weekday()) % 7
    return d + timedelta(days=offset)


def _cpi_release_date(year: int, month: int) -> date:
    """Approximate CPI release: ~12th of the following month (mid-month, weekday)."""
    # CPI for month M is released in month M+1 around the 10th–15th
    if month == 12:
        rel_year, rel_month = year + 1, 1
    else:
        rel_year, rel_month = year, month + 1
    d = date(rel_year, rel_month, 12)
    # Shift to next weekday if on weekend
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _fomc_meeting_dates(year: int) -> list[date]:
    """
    Return approximate FOMC meeting dates (second day = rate decision day).
    FOMC meets ~8 times/year; exact dates are published by the Fed at
    https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

    The dates below are the *known* 2025-2026 schedule and a rough
    heuristic for other years (last Wed of Jan/Mar/May/Jul/Sep/Oct/Dec).
    """
    known: dict[int, list[tuple[int, int]]] = {
        2025: [(1,29),(3,19),(5,7),(6,18),(7,30),(9,17),(10,29),(12,17)],
        2026: [(1,28),(3,18),(5,6),(6,17),(7,29),(9,16),(10,28),(12,16)],
    }
    if year in known:
        return [date(year, m, d) for m, d in known[year]]
    # Heuristic for unknown years: last Wed of each scheduled month
    months = [1, 3, 5, 6, 7, 9, 10, 12]
    result = []
    for month in months:
        # Find last Wednesday of the month
        last_day = monthrange(year, month)[1]
        d = date(year, month, last_day)
        while d.weekday() != 2:  # Wednesday = 2
            d -= timedelta(days=1)
        result.append(d)
    return result


def _compute_fallback_events(
    start: date, end: date,
) -> list[dict]:
    """Build a list of estimated high-impact events for the date range."""
    events: list[dict] = []
    year = start.year
    while year <= end.year:
        # FOMC dates
        for d in _fomc_meeting_dates(year):
            if start <= d <= end:
                events.append({"date": d.isoformat(), "title": "FOMC Rate Decision", "source": "estimate"})

        # NFP (first Friday of each month = jobs report)
        for month in range(1, 13):
            d = _first_friday(year, month)
            if start <= d <= end:
                events.append({"date": d.isoformat(), "title": "Non-Farm Payrolls", "source": "estimate"})

        # CPI (monthly, ~12th of following month)
        for month in range(1, 13):
            d = _cpi_release_date(year, month)
            if start <= d <= end:
                events.append({"date": d.isoformat(), "title": "CPI", "source": "estimate"})

        year += 1
    return events


# ── Main class ────────────────────────────────────────────────────────────────

class MacroEventCalendar:
    """
    Fetches, caches, and serves high-impact USD macro event dates.

    Blackout logic
    --------------
    is_event_day(d)       → True when a high-impact event is scheduled today.
    is_blackout_window(d, window_days=N)
                          → True when any event falls in [d, d+N-1].
                             Use N=3 for theta_decay (covers the full hold period).
                             Use N=1 for same-day check.
    """

    _CACHE_TTL_HOURS = 12
    _CACHE_FILE = Path("./data_cache/event_calendar.json")

    def __init__(self) -> None:
        self._events: list[dict] = []             # {"date": "YYYY-MM-DD", "title": ..., "source": ...}
        self._event_dates: set[date] = set()      # fast O(1) lookup
        self._last_refresh: Optional[datetime] = None

    # ── Public interface ──────────────────────────────────────────────────────

    def is_event_day(self, d: date) -> bool:
        """True if a high-impact macro event is scheduled on d."""
        return d in self._event_dates

    def is_blackout_window(self, start: date, window_days: int = 1) -> bool:
        """True if any event falls within [start, start + window_days - 1]."""
        for offset in range(window_days):
            if (start + timedelta(days=offset)) in self._event_dates:
                return True
        return False

    def get_events_for_date(self, d: date) -> list[dict]:
        """Return events scheduled for d."""
        return [e for e in self._events if e.get("date") == d.isoformat()]

    def upcoming_events(self, days_ahead: int = 7) -> list[dict]:
        """Return all events in the next `days_ahead` calendar days."""
        today = datetime.now(ET).date()
        end   = today + timedelta(days=days_ahead)
        return [
            e for e in self._events
            if today <= date.fromisoformat(e["date"]) <= end
        ]

    async def ensure_fresh(self) -> None:
        """Refresh if data is older than TTL or not loaded yet."""
        if self._last_refresh is None:
            self._load_cache()             # try disk first
            if not self._events:
                await self.refresh()       # then network
        else:
            age_h = (datetime.now() - self._last_refresh).total_seconds() / 3600
            if age_h >= self._CACHE_TTL_HOURS:
                await self.refresh()

    async def refresh(self) -> None:
        """Fetch events from ForexFactory; fall back to computed schedule."""
        fetched = await self._fetch_forex_factory()
        if fetched:
            self._events = fetched
            logger.info(
                f"EventCalendar: {len(fetched)} high-impact USD events loaded "
                f"from ForexFactory"
            )
        else:
            logger.warning(
                "EventCalendar: ForexFactory fetch failed — using computed fallback schedule"
            )
            today = datetime.now(ET).date()
            self._events = _compute_fallback_events(today, today + timedelta(days=60))
            logger.info(
                f"EventCalendar: {len(self._events)} estimated events loaded (fallback)"
            )

        self._rebuild_index()
        self._last_refresh = datetime.now()
        self._save_cache()

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_forex_factory(self) -> list[dict]:
        """Fetch this week + next week from ForexFactory JSON feeds."""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._fetch_ff_sync)

    def _fetch_ff_sync(self) -> list[dict]:
        """Blocking ForexFactory fetch (run in thread pool)."""
        events: list[dict] = []
        for url in _FF_URLS:
            try:
                resp = requests.get(url, timeout=8, headers=_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                for item in data:
                    if _is_high_impact_usd(item):
                        d = _parse_ff_date(item.get("date", ""))
                        if d:
                            events.append({
                                "date":   d.isoformat(),
                                "title":  item.get("title", "Unknown"),
                                "source": "forexfactory",
                            })
                logger.debug(f"EventCalendar: fetched {url} — {len(data)} items")
            except Exception as exc:
                logger.warning(f"EventCalendar: fetch failed for {url}: {exc}")
        return events

    def _rebuild_index(self) -> None:
        self._event_dates = {
            date.fromisoformat(e["date"])
            for e in self._events
            if e.get("date")
        }

    def _save_cache(self) -> None:
        try:
            self._CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self._CACHE_FILE, "w") as f:
                json.dump(
                    {
                        "refreshed_at": datetime.now().isoformat(),
                        "events": self._events,
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.warning(f"EventCalendar: cache save failed: {exc}")

    def _load_cache(self) -> None:
        try:
            if not self._CACHE_FILE.exists():
                return
            age_h = (
                datetime.now().timestamp() - self._CACHE_FILE.stat().st_mtime
            ) / 3600
            if age_h > self._CACHE_TTL_HOURS * 2:
                logger.info("EventCalendar: disk cache too old, skipping")
                return
            with open(self._CACHE_FILE) as f:
                data = json.load(f)
            self._events = data.get("events", [])
            self._rebuild_index()
            self._last_refresh = datetime.fromisoformat(
                data.get("refreshed_at", datetime.now().isoformat())
            )
            logger.info(
                f"EventCalendar: loaded {len(self._events)} events from disk cache"
            )
        except Exception as exc:
            logger.warning(f"EventCalendar: disk cache load failed: {exc}")


# ── Singleton ─────────────────────────────────────────────────────────────────
macro_calendar = MacroEventCalendar()
