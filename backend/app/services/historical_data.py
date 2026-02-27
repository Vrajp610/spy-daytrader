"""Historical data manager: fetch daily OHLCV bars from Yahoo Finance with local CSV caching."""

from __future__ import annotations
import time
import logging
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)


class LocalDataCache:
    """Simple CSV cache keyed by symbol/interval/start/end."""

    def __init__(self, cache_dir: str = "./data_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age_hours = 7 * 24  # daily bars: 7-day TTL (no need to refresh multi-year cache daily)

    def _key(self, symbol: str, interval: str, start: str, end: str) -> Path:
        fname = f"{symbol}_{interval}_{start}_{end}.csv"
        return self.cache_dir / fname

    def get(self, symbol: str, interval: str, start: str, end: str) -> pd.DataFrame | None:
        path = self._key(symbol, interval, start, end)
        if path.exists():
            age_hours = (time.time() - path.stat().st_mtime) / 3600
            if age_hours <= self.max_age_hours:
                try:
                    df = pd.read_csv(path, index_col=0, parse_dates=True)
                    logger.info("Cache hit: %s (%d rows)", path.name, len(df))
                    return df
                except Exception as exc:
                    logger.warning("Cache read error %s: %s", path.name, exc)
            else:
                logger.info("Cache stale (%.1fh), will try fuzzy fallback %s", age_hours, path.name)

        # Fuzzy fallback: find any cached file with same symbol/interval/start whose
        # end date is within 14 calendar days of the requested end.  This avoids a
        # live yfinance download when only a few recent bars are missing (e.g. after
        # a weekend) and the bulk of the historical data is already on disk.
        try:
            from datetime import datetime as _dt
            req_end = _dt.strptime(end, "%Y-%m-%d")
            prefix = f"{symbol}_{interval}_{start}_"
            candidates = list(self.cache_dir.glob(f"{prefix}*.csv"))
            for cpath in sorted(candidates, reverse=True):  # newest end-date first
                cend_str = cpath.stem.replace(prefix, "")
                try:
                    cend = _dt.strptime(cend_str, "%Y-%m-%d")
                except ValueError:
                    continue
                delta_days = abs((req_end - cend).days)
                if delta_days <= 14:
                    try:
                        df = pd.read_csv(cpath, index_col=0, parse_dates=True)
                        logger.info(
                            "Cache fuzzy hit: %s (end Δ=%dd, %d rows)",
                            cpath.name, delta_days, len(df),
                        )
                        # Re-save under the requested key so next call is exact
                        self.save(symbol, interval, start, end, df)
                        return df
                    except Exception as exc:
                        logger.warning("Cache fuzzy read error %s: %s", cpath.name, exc)
        except Exception as exc:
            logger.debug("Cache fuzzy search error: %s", exc)

        return None

    def save(self, symbol: str, interval: str, start: str, end: str, df: pd.DataFrame) -> None:
        path = self._key(symbol, interval, start, end)
        try:
            df.to_csv(path)
            logger.info("Cached %d rows → %s", len(df), path.name)
        except Exception as exc:
            logger.warning("Cache write error: %s", exc)

    def list_files(self) -> list[dict]:
        files = []
        for f in sorted(self.cache_dir.glob("*.csv")):
            stat = f.stat()
            files.append({
                "name": f.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "age_hours": round((time.time() - stat.st_mtime) / 3600, 1),
            })
        return files


def _fetch_yahoo_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily OHLCV via yfinance (handles auth/rate-limiting automatically)."""
    raw = yf.download(
        symbol,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if raw.empty:
        raise ValueError(f"No data returned for {symbol} {start}→{end}")

    # yfinance 1.x returns MultiIndex columns like (Price, Ticker) — flatten them
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]

    df = raw[["open", "high", "low", "close", "volume"]].copy()
    df["adj_close"] = df["close"]  # auto_adjust=True already applies split/dividend adjustments
    df.index.name = "date"

    # Normalize index to date (drop time/tz)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.to_datetime(df.index).normalize()

    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["volume"] > 0]
    df = df.sort_index()
    return df


def _add_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators needed by daily strategy generators."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # EMAs
    df["ema9"]  = close.ewm(span=9,   adjust=False).mean()
    df["ema21"] = close.ewm(span=21,  adjust=False).mean()
    df["ema50"] = close.ewm(span=50,  adjust=False).mean()
    df["ema200"]= close.ewm(span=200, adjust=False).mean()
    df["sma20"] = close.rolling(20).mean()

    # RSI 14
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - 100 / (1 + rs)

    # ATR 14
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    # ADX 14
    up   = high.diff()
    down = -low.diff()
    pdm  = up.where((up > down) & (up > 0), 0.0)
    ndm  = down.where((down > up) & (down > 0), 0.0)
    atr14 = df["atr14"]
    pdi  = 100 * pdm.rolling(14).mean() / atr14.replace(0, np.nan)
    ndi  = 100 * ndm.rolling(14).mean() / atr14.replace(0, np.nan)
    dx   = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan))
    df["adx14"] = dx.rolling(14).mean()

    # MACD (12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd_line"]   = ema12 - ema26
    df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd_line"] - df["macd_signal"]

    # Bollinger Bands (20, 2σ)
    df["bb_mid"]   = df["sma20"]
    df["bb_std"]   = close.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, np.nan)

    # Volume ratio (vs 20-day avg)
    df["vol_sma20"] = volume.rolling(20).mean()
    df["vol_ratio"] = volume / df["vol_sma20"].replace(0, np.nan)

    # Gap %: (open - prev_close) / prev_close
    df["gap_pct"] = (df["open"] - close.shift()) / close.shift().replace(0, np.nan) * 100

    # Rate of change
    df["roc5"]  = (close / close.shift(5)  - 1) * 100
    df["roc20"] = (close / close.shift(20) - 1) * 100

    return df


class HistoricalDataManager:
    """Fetch and cache daily OHLCV bars with indicators for backtesting."""

    def __init__(self, cache_dir: str = "./data_cache"):
        self._cache = LocalDataCache(cache_dir)

    def fetch_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Return daily OHLCV + indicators DataFrame."""
        if use_cache:
            cached = self._cache.get(symbol, "1d", start, end)
            if cached is not None:
                return cached

        logger.info("Fetching daily bars %s %s → %s", symbol, start, end)
        df = _fetch_yahoo_daily(symbol, start, end)
        df = _add_daily_indicators(df)

        if use_cache:
            self._cache.save(symbol, "1d", start, end, df)

        return df

    def list_cache_files(self) -> list[dict]:
        return self._cache.list_files()
