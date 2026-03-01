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

        # Fuzzy fallback 1: same-start files with end date within 14 days of requested.
        # (handles the case where only a few recent bars are missing)
        try:
            from datetime import datetime as _dt
            req_start = _dt.strptime(start, "%Y-%m-%d")
            req_end   = _dt.strptime(end,   "%Y-%m-%d")
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
                        self.save(symbol, interval, start, end, df)
                        return df
                    except Exception as exc:
                        logger.warning("Cache fuzzy read error %s: %s", cpath.name, exc)
        except Exception as exc:
            logger.debug("Cache fuzzy search error: %s", exc)

        # Fuzzy fallback 2: sub-range slice — find any cached file for this symbol/interval
        # whose range fully contains [start, end], then slice and return.
        # This allows a 2010→2026 cache to serve a 2010→2019 request.
        try:
            from datetime import datetime as _dt
            req_start = _dt.strptime(start, "%Y-%m-%d")
            req_end   = _dt.strptime(end,   "%Y-%m-%d")
            pat = f"{symbol}_{interval}_*_*.csv"
            all_candidates = list(self.cache_dir.glob(pat))
            for cpath in sorted(all_candidates, reverse=True):
                parts = cpath.stem.split("_")
                # filename: SYMBOL_1d_START_END → parts[-2] = start, parts[-1] = end
                if len(parts) < 4:
                    continue
                try:
                    cstart = _dt.strptime(parts[-2], "%Y-%m-%d")
                    cend   = _dt.strptime(parts[-1], "%Y-%m-%d")
                except ValueError:
                    continue
                # Check if cached range fully contains the requested range
                if cstart <= req_start and cend >= req_end:
                    try:
                        df = pd.read_csv(cpath, index_col=0, parse_dates=True)
                        # Slice to the requested date range
                        df.index = pd.to_datetime(df.index)
                        sliced = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
                        if len(sliced) >= 30:
                            logger.info(
                                "Cache sub-range hit: %s → sliced to %s/%s (%d rows)",
                                cpath.name, start, end, len(sliced),
                            )
                            self.save(symbol, interval, start, end, sliced)
                            return sliced
                    except Exception as exc:
                        logger.warning("Cache sub-range read error %s: %s", cpath.name, exc)
        except Exception as exc:
            logger.debug("Cache sub-range search error: %s", exc)

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


def _fetch_yahoo_v8_raw(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    Fetch daily OHLCV directly via Yahoo Finance v8 API (no yfinance dependency).
    Returns a DataFrame with date index and OHLCV columns (adj_close used as close).
    """
    import requests
    from datetime import datetime as _dt

    start_ts = int(_dt.strptime(start, "%Y-%m-%d").timestamp())
    end_ts   = int(_dt.strptime(end,   "%Y-%m-%d").timestamp()) + 86400  # include last day

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval": "1d", "period1": start_ts, "period2": end_ts, "events": "history"}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; spy-daytrader/1.0)"}

    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()

    result_list = data.get("chart", {}).get("result")
    if not result_list:
        raise ValueError(f"Yahoo v8 API returned no result for {symbol} {start}→{end}")

    result   = result_list[0]
    timestamps = result.get("timestamp", [])
    if not timestamps:
        raise ValueError(f"No timestamps in Yahoo v8 result for {symbol} {start}→{end}")

    quote    = result["indicators"]["quote"][0]
    raw_close = quote["close"]
    adjclose  = result["indicators"].get("adjclose", [{}])[0].get("adjclose", raw_close)

    idx = pd.to_datetime(timestamps, unit="s").normalize()

    # Yahoo v8 returns split-adjusted-only open/high/low but dividend+split-adjusted close.
    # To keep all OHLCV on the same adjusted scale, compute the per-bar ratio and apply it.
    raw_close_s = pd.Series(raw_close, index=idx, dtype=float)
    adj_close_s = pd.Series(adjclose,  index=idx, dtype=float)
    # Ratio: adjclose / raw_close (e.g. ~0.68 for 2003 data with 20+ years of dividends)
    adj_ratio   = (adj_close_s / raw_close_s.replace(0.0, np.nan)).fillna(1.0)

    df = pd.DataFrame({
        "open":   pd.Series(quote["open"],   index=idx, dtype=float) * adj_ratio,
        "high":   pd.Series(quote["high"],   index=idx, dtype=float) * adj_ratio,
        "low":    pd.Series(quote["low"],    index=idx, dtype=float) * adj_ratio,
        "close":  adj_close_s,   # already fully adjusted
        "volume": pd.Series(quote["volume"], index=idx, dtype=float),
    })
    df.index.name = "date"
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["volume"] > 0]
    df = df.sort_index()
    df["adj_close"] = df["close"]
    return df


def _fetch_yahoo_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    Fetch daily OHLCV for symbol between start and end.

    Strategy:
    1. Try the raw Yahoo v8 API (works without yfinance auth).
    2. Fall back to yfinance.download() if the raw call fails.
    """
    # Primary: direct Yahoo Finance v8 API
    try:
        df = _fetch_yahoo_v8_raw(symbol, start, end)
        if not df.empty:
            logger.info("Fetched %d rows via Yahoo v8 raw API (%s %s→%s)", len(df), symbol, start, end)
            return df
    except Exception as exc:
        logger.warning("Yahoo v8 raw API failed for %s %s→%s: %s — falling back to yfinance", symbol, start, end, exc)

    # Fallback: yfinance
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
