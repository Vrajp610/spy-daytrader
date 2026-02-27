"""TradingView webhook receiver.

Accepts POST /api/webhook/tradingview with a signed JSON payload from a Pine Script
alert and routes it directly into the options execution pipeline — bypassing internal
signal generation but still applying all risk gates (daily loss, circuit breaker,
existing-position guard, no-chain guard).

Pine Script alert message template:
{
  "secret":      "{{your-webhook-secret}}",
  "strategy":    "orb_scalp",
  "action":      "BUY",
  "price":       {{close}},
  "stop_loss":   {{close}} * 0.995,
  "take_profit": {{close}} * 1.010,
  "confidence":  0.78,
  "options_preference": "long_call",
  "preferred_dte": 1,
  "target_delta": 0.45
}
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


class TradingViewPayload(BaseModel):
    secret: str
    strategy: str
    action: str                         # BUY | SELL | CLOSE
    price: float
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.70
    options_preference: Optional[str] = None
    preferred_dte: Optional[int] = None
    target_delta: Optional[float] = None
    min_dte: Optional[int] = None


@router.post("/tradingview")
async def tradingview_webhook(payload: TradingViewPayload):
    """Receive a TradingView alert and forward it to the trading engine."""
    # 1. Verify secret (skip check when secret is not configured)
    if settings.tradingview_webhook_secret and payload.secret != settings.tradingview_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    # 2. Build optional options-preference metadata
    metadata: dict = {}
    if payload.options_preference is not None:
        metadata["options_preference"] = payload.options_preference
    if payload.preferred_dte is not None:
        metadata["preferred_dte"] = payload.preferred_dte
    if payload.target_delta is not None:
        metadata["target_delta"] = payload.target_delta
    if payload.min_dte is not None:
        metadata["min_dte"] = payload.min_dte

    # 3. Delegate to the trading engine
    from app.services.trading_engine import trading_engine
    result = await trading_engine.execute_webhook_signal(
        action=payload.action.upper(),
        strategy=payload.strategy,
        price=payload.price,
        stop_loss=payload.stop_loss,
        take_profit=payload.take_profit,
        confidence=payload.confidence,
        metadata=metadata,
    )
    return result
