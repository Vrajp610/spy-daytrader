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
        df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

        # ATR(14)
        df["atr"] = DataManager._compute_atr(df, 14)

        # ADX(14)
        df["adx"] = DataManager._compute_adx(df, 14)

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
    def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        plus_dm = df["high"].diff()
        minus_dm = -df["low"].diff()

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        atr = DataManager._compute_atr(df, period)

        plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan))

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.ewm(span=period, adjust=False).mean()
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
