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
    """Calculate number of contracts based on defined risk and available capital.

    Args:
        order: The options order with max_loss pre-calculated.
        capital: Current available capital (already has open collateral deducted).
        risk_fraction: Fraction of capital to risk (e.g., 0.015 = 1.5%).
        open_risk: Total risk of currently open positions.

    Returns:
        Number of contracts (may be 0 if rejected).
    """
    if order.max_loss <= 0 or capital <= 0:
        return 0

    # Per-contract risk and collateral
    max_loss_per_contract = order.max_loss / order.contracts if order.contracts > 0 else order.max_loss

    if order.is_credit:
        # Credit spread collateral = max_loss per contract
        collateral_per_contract = max_loss_per_contract
    else:
        # Debit position cost = premium per contract
        collateral_per_contract = abs(order.net_premium) * 100

    if collateral_per_contract <= 0:
        return 0

    # Size by risk fraction
    risk_amount = capital * risk_fraction
    contracts_by_risk = int(risk_amount / max_loss_per_contract)

    # Size by available capital (can't commit more collateral than we have)
    contracts_by_capital = int(capital / collateral_per_contract)

    # Take the smaller of risk-based and capital-based sizing
    contracts = min(contracts_by_risk, contracts_by_capital)
    contracts = max(1, min(contracts, settings.max_contracts_per_trade))

    # Verify we can actually afford this many contracts
    total_collateral = collateral_per_contract * contracts
    if total_collateral > capital:
        contracts = max(1, int(capital / collateral_per_contract))
        if collateral_per_contract > capital:
            logger.warning(
                f"Cannot afford even 1 contract: collateral ${collateral_per_contract:.0f} "
                f"> capital ${capital:.0f}"
            )
            return 0

    # Portfolio risk cap: total open risk must not exceed max_drawdown % of capital
    total_risk = open_risk + (max_loss_per_contract * contracts)
    # Use total equity (capital + collateral) for portfolio risk cap
    total_equity = capital + open_risk  # approximate total equity
    portfolio_max = total_equity * settings.max_drawdown

    if total_risk > portfolio_max:
        available_risk = portfolio_max - open_risk
        if available_risk <= 0:
            logger.warning(
                f"Portfolio risk cap reached: open_risk=${open_risk:.0f} >= "
                f"max=${portfolio_max:.0f}. Skipping trade."
            )
            return 0
        contracts = max(1, int(available_risk / max_loss_per_contract))

    # Final capital check after all adjustments
    final_collateral = collateral_per_contract * contracts
    if final_collateral > capital:
        return 0

    return contracts
