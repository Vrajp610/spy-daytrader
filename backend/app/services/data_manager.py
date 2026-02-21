"""Data fetching and technical indicator computation."""

from __future__ import annotations
import ssl
import certifi
import json
import urllib.request
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import logging

# Fix SSL certificate verification on macOS
_ssl_context = ssl.create_default_context(cafile=certifi.where())

logger = logging.getLogger(__name__)

FALLBACK_CSV = Path(__file__).resolve().parent.parent.parent / "spy_sample.csv"

# Period string to seconds mapping for the Yahoo v8 API
_PERIOD_SECONDS = {
    "1d": 86400, "2d": 172800, "5d": 432000, "1mo": 2592000,
    "3mo": 7776000, "6mo": 15552000, "1y": 31536000,
}


class DataManager:
    """Fetches SPY data and computes technical indicators."""

    def __init__(self):
        # Cache for extended timeframe data (refreshed every 15 min)
        self._extended_cache: Optional[dict] = None
        self._extended_cache_time: Optional[datetime] = None
        self._extended_cache_ttl = 900  # 15 minutes

    @staticmethod
    def _fetch_yahoo_chart(
        symbol: str,
        period: str = "5d",
        interval: str = "1m",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch data directly from Yahoo Finance v8 chart API."""
        base = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

        if start and end:
            # Convert date strings to unix timestamps
            start_ts = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
            end_ts = int(datetime.strptime(end, "%Y-%m-%d").timestamp()) + 86400
            params = f"period1={start_ts}&period2={end_ts}&interval={interval}"
        else:
            params = f"range={period}&interval={interval}"

        url = f"{base}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        resp = urllib.request.urlopen(req, context=_ssl_context, timeout=15)
        data = json.loads(resp.read())

        result = data.get("chart", {}).get("result")
        if not result:
            return pd.DataFrame()

        chart = result[0]
        timestamps = chart.get("timestamp", [])
        quote = chart.get("indicators", {}).get("quote", [{}])[0]

        if not timestamps:
            return pd.DataFrame()

        df = pd.DataFrame({
            "open": quote.get("open", []),
            "high": quote.get("high", []),
            "low": quote.get("low", []),
            "close": quote.get("close", []),
            "volume": quote.get("volume", []),
        }, index=pd.to_datetime(timestamps, unit="s", utc=True))

        df.index.name = "Datetime"
        df = df.dropna(subset=["close"])
        return df

    @staticmethod
    def fetch_intraday(
        symbol: str = "SPY",
        period: str = "5d",
        interval: str = "1m",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        # Primary: direct Yahoo Finance API
        df = pd.DataFrame()
        try:
            df = DataManager._fetch_yahoo_chart(symbol, period, interval, start, end)
            if not df.empty:
                logger.info(f"Fetched {len(df)} bars from Yahoo Finance API")
        except Exception as e:
            logger.warning(f"Yahoo Finance API fetch failed: {e}")

        # Fallback: CSV
        if df.empty and FALLBACK_CSV.exists():
            logger.info(f"Using fallback CSV: {FALLBACK_CSV}")
            df = pd.read_csv(FALLBACK_CSV, index_col=0, parse_dates=True)
            if start and end:
                df = df[(df.index >= start) & (df.index <= end)]

        if df.empty:
            logger.warning(f"No data returned for {symbol}")
            return df

        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York")
        else:
            df.index = df.index.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward")
            df = df[df.index.notna()]

        df.columns = [c.lower() for c in df.columns]
        df = DataManager.validate_bars(df)

        # Stale data check
        if not df.empty and df.index.tz is not None:
            last_bar_time = df.index[-1]
            now = pd.Timestamp.now(tz="America/New_York")
            staleness = (now - last_bar_time).total_seconds()
            if staleness > 300:  # 5 minutes
                from datetime import time as dt_time
                market_open = dt_time(9, 30)
                market_close = dt_time(16, 0)
                current_time = now.time()
                if market_open <= current_time <= market_close:
                    logger.warning(f"Stale data: last bar is {staleness:.0f}s old ({last_bar_time})")

        return df

    @staticmethod
    def validate_bars(df: pd.DataFrame) -> pd.DataFrame:
        """Validate and clean OHLCV data."""
        if df.empty:
            return df

        original_len = len(df)

        # Remove rows with non-positive prices
        price_cols = ["open", "high", "low", "close"]
        existing_cols = [c for c in price_cols if c in df.columns]
        for col in existing_cols:
            df = df[df[col] > 0]

        # Fix high < low (swap)
        if "high" in df.columns and "low" in df.columns:
            mask = df["high"] < df["low"]
            if mask.any():
                df.loc[mask, ["high", "low"]] = df.loc[mask, ["low", "high"]].values

        # Remove duplicate timestamps
        df = df[~df.index.duplicated(keep="last")]

        # Sort by timestamp
        df = df.sort_index()

        dropped = original_len - len(df)
        if dropped > 0:
            logger.warning(f"Data validation: dropped {dropped}/{original_len} invalid bars")

        return df

    @staticmethod
    def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Add all technical indicators needed by strategies."""
        if df.empty:
            return df

        df = df.copy()

        # VWAP
        df["vwap"] = DataManager._compute_vwap(df)

        # RSI(14)
        df["rsi"] = DataManager._compute_rsi(df["close"], 14)

        # EMAs
        df["ema9"]  = df["close"].ewm(span=9,   adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=21,  adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50,  adjust=False).mean()
        df["ema200"]= df["close"].ewm(span=200, adjust=False).mean()

        # ATR(14)
        df["atr"] = DataManager._compute_atr(df, 14)

        # ADX(14) + directional indices (+DI, -DI)
        df["adx"], df["plus_di"], df["minus_di"] = DataManager._compute_adx_full(df, 14)

        # Williams %R (14)
        roll_high = df["high"].rolling(14).max()
        roll_low  = df["low"].rolling(14).min()
        df["wr14"] = -100 * (roll_high - df["close"]) / (roll_high - roll_low).replace(0, np.nan)

        # Keltner Channel (EMA21 ± 1.5×ATR) — used by keltner_breakout strategy
        df["kc_upper"] = df["ema21"] + 1.5 * df["atr"]
        df["kc_lower"] = df["ema21"] - 1.5 * df["atr"]

        # MACD
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        df["macd"] = ema12 - ema26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        # Bollinger Bands
        sma20 = df["close"].rolling(20).mean()
        std20 = df["close"].rolling(20).std()
        df["bb_upper"] = sma20 + 2 * std20
        df["bb_lower"] = sma20 - 2 * std20
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma20

        # Volume average (20-bar)
        df["vol_avg"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

        return df

    @staticmethod
    def _compute_vwap(df: pd.DataFrame) -> pd.Series:
        """Session VWAP, resetting each trading day."""
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        tp_vol = typical_price * df["volume"]

        dates = df.index.date
        vwap = pd.Series(index=df.index, dtype=float)
        for d in pd.unique(dates):
            mask = dates == d
            cum_tp_vol = tp_vol[mask].cumsum()
            cum_vol = df["volume"][mask].cumsum()
            vwap[mask] = cum_tp_vol / cum_vol.replace(0, np.nan)
        return vwap

    @staticmethod
    def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift(1)).abs()
        low_close = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _compute_adx_full(df: pd.DataFrame, period: int = 14) -> tuple:
        """Return (adx, plus_di, minus_di) as three pd.Series."""
        plus_dm  = df["high"].diff()
        minus_dm = -df["low"].diff()

        plus_dm  = plus_dm.where((plus_dm > minus_dm)  & (plus_dm > 0),  0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        atr = DataManager._compute_atr(df, period)

        plus_di  = 100 * (plus_dm.ewm(span=period,  adjust=False).mean() / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan))

        dx  = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.ewm(span=period, adjust=False).mean()
        return adx, plus_di, minus_di

    @staticmethod
    def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Legacy wrapper — returns ADX only."""
        adx, _, _ = DataManager._compute_adx_full(df, period)
        return adx

    @staticmethod
    def resample_to_5min(df: pd.DataFrame) -> pd.DataFrame:
        """Resample 1-min bars to 5-min bars."""
        return DataManager.resample_to_interval(df, "5min")

    @staticmethod
    def resample_to_interval(df: pd.DataFrame, interval: str) -> pd.DataFrame:
        """Resample 1-min bars to the given interval (e.g. '5min', '15min')."""
        resampled = df.resample(interval).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        return DataManager.add_indicators(resampled)

    def fetch_extended_data(self, symbol: str = "SPY") -> Optional[dict]:
        """Fetch 60-day hourly data and resample to 4hr, 1hr, 30min timeframes.

        Results are cached for 15 minutes since higher TFs don't need rapid refresh.
        """
        now = datetime.now()
        if (self._extended_cache is not None
                and self._extended_cache_time is not None
                and (now - self._extended_cache_time).total_seconds() < self._extended_cache_ttl):
            return self._extended_cache

        try:
            # Fetch 60 days of hourly data
            df_hourly = self._fetch_yahoo_chart(symbol, period="60d", interval="1h")
            if df_hourly.empty:
                logger.warning("Extended data fetch returned empty — trying 30d")
                df_hourly = self._fetch_yahoo_chart(symbol, period="30d", interval="1h")

            if df_hourly.empty:
                logger.warning("Extended hourly data unavailable")
                return self._extended_cache

            # Localize timezone
            if df_hourly.index.tz is not None:
                df_hourly.index = df_hourly.index.tz_convert("America/New_York")
            else:
                df_hourly.index = df_hourly.index.tz_localize(
                    "America/New_York", ambiguous="NaT", nonexistent="shift_forward"
                )
                df_hourly = df_hourly[df_hourly.index.notna()]

            df_hourly.columns = [c.lower() for c in df_hourly.columns]
            df_hourly = self.validate_bars(df_hourly)

            if df_hourly.empty:
                return self._extended_cache

            # Add indicators to the 1hr data
            df_1hr = self.add_indicators(df_hourly)

            # Resample to higher timeframes
            df_4hr = self.resample_to_interval(df_hourly, "4h")
            df_30min_raw = self._fetch_yahoo_chart(symbol, period="60d", interval="30m")
            if not df_30min_raw.empty:
                if df_30min_raw.index.tz is not None:
                    df_30min_raw.index = df_30min_raw.index.tz_convert("America/New_York")
                else:
                    df_30min_raw.index = df_30min_raw.index.tz_localize(
                        "America/New_York", ambiguous="NaT", nonexistent="shift_forward"
                    )
                    df_30min_raw = df_30min_raw[df_30min_raw.index.notna()]
                df_30min_raw.columns = [c.lower() for c in df_30min_raw.columns]
                df_30min_raw = self.validate_bars(df_30min_raw)
                df_30min = self.add_indicators(df_30min_raw)
            else:
                # Fallback: resample hourly to 30min is not ideal but provides data
                df_30min = df_1hr.copy()

            self._extended_cache = {
                "df_30min": df_30min,
                "df_1hr": df_1hr,
                "df_4hr": df_4hr,
            }
            self._extended_cache_time = now
            logger.info(
                f"Fetched extended TF data: 4hr={len(df_4hr)} bars, "
                f"1hr={len(df_1hr)} bars, 30min={len(df_30min)} bars"
            )
            return self._extended_cache

        except Exception as e:
            logger.error(f"Extended data fetch error: {e}")
            return self._extended_cache
