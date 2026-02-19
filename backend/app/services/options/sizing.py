"""Options position sizing for defined-risk trades."""

from __future__ import annotations
import logging

from app.config import settings
from app.services.options.models import OptionsOrder

logger = logging.getLogger(__name__)


def calculate_contracts(
    order: OptionsOrder,
    capital: float,
    risk_fraction: float,
    open_risk: float = 0.0,
) -> int:
    """Calculate number of contracts based on defined risk.

    Args:
        order: The options order with max_loss pre-calculated.
        capital: Current account capital.
        risk_fraction: Fraction of capital to risk (e.g., 0.015 = 1.5%).
        open_risk: Total risk of currently open positions.

    Returns:
        Number of contracts (may be 0 if rejected).
    """
    if order.max_loss <= 0 or capital <= 0:
        return 0

    # Per-trade risk
    max_risk_per_contract = order.max_loss / order.contracts if order.contracts > 0 else order.max_loss
    risk_amount = capital * risk_fraction
    contracts = int(risk_amount / max_risk_per_contract)
    contracts = max(1, min(contracts, settings.max_contracts_per_trade))

    # Portfolio risk cap: total open risk must not exceed 16% of capital
    total_risk = open_risk + (max_risk_per_contract * contracts)
    portfolio_max = capital * settings.max_drawdown  # 0.16 by default

    if total_risk > portfolio_max:
        # Reduce contracts to fit within portfolio cap
        available_risk = portfolio_max - open_risk
        if available_risk <= 0:
            logger.warning(
                f"Portfolio risk cap reached: open_risk=${open_risk:.0f} >= "
                f"max=${portfolio_max:.0f}. Skipping trade."
            )
            return 0
        contracts = max(1, int(available_risk / max_risk_per_contract))

    return contracts
