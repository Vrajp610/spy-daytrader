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
    blended_score: float = 0.0,
    confidence: float = 0.0,
    vix_daily_move_pct: float = 1.25,
) -> int:
    """Calculate number of contracts based on defined risk and available capital.

    Args:
        order: The options order with max_loss pre-calculated.
        capital: Current available capital (already has open collateral deducted).
        risk_fraction: Fraction of capital to risk (e.g., 0.015 = 1.5%).
        open_risk: Total risk of currently open positions.
        blended_score: Strategy's blended composite score (-20..100+). Higher = better model.
        confidence: Signal confidence (0-1). High confidence + good model lifts the ceiling.
        vix_daily_move_pct: VIX/16 = 1-sigma expected daily move %. Used to scale position
            size inversely with expected volatility (VIX=20 → 1.25% = baseline 1.0×;
            VIX=32 → 2.0% → 0.625×; VIX=12 → 0.75% → 1.5× capped).

    Returns:
        Number of contracts (may be 0 if rejected).
    """
    if order.max_loss <= 0 or capital <= 0:
        return 0

    # ── Model quality scalar ─────────────────────────────────────────────────
    # As the model proves itself (composite score rises), we risk slightly more.
    # Score=0  → 1.0×, Score=50 → 1.5×, Score=−20 → 0.8× (reduce when struggling)
    # Hard cap at 1.5× so we never more than 50% oversize vs the baseline.
    if blended_score >= 0:
        model_quality_scalar = min(1.5, 1.0 + blended_score / 100.0)
    else:
        model_quality_scalar = max(0.8, 1.0 + blended_score / 100.0)

    # ── Contract ceiling ─────────────────────────────────────────────────────
    # Lift from 5 → 10 only when: confidence is very high AND model is validated.
    # blended_score ≥ 15 means the strategy has meaningful backtest + live evidence.
    high_confidence = (
        confidence >= settings.high_confidence_threshold
        and blended_score >= 15
    )
    contract_ceiling = (
        settings.max_contracts_high_confidence if high_confidence
        else settings.max_contracts_per_trade
    )
    if high_confidence:
        logger.debug(
            f"High-confidence sizing: ceiling={contract_ceiling} ct "
            f"(conf={confidence:.0%}, score={blended_score:.1f})"
        )

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

    # ── VIX/16 scalar ────────────────────────────────────────────────────────
    # Larger expected daily move → each contract carries more intraday P&L swing.
    # Scale contracts inversely so dollar risk stays roughly constant regardless of VIX.
    # Reference: VIX=20 → 1.25% → scalar=1.0× (neutral baseline)
    # VIX=12 → 0.75% → scalar=1.5× (low vol, options cheap, smaller moves → more contracts ok)
    # VIX=32 → 2.0%  → scalar=0.625× (high vol, big moves → reduce exposure)
    # VIX=40 → 2.5%  → scalar=0.5× (floor, extreme fear)
    _baseline_move = 1.25  # normalised to VIX=20
    vix_scalar = min(1.5, max(0.5, _baseline_move / max(0.25, vix_daily_move_pct)))
    if vix_scalar != 1.0:
        logger.debug(
            f"VIX/16 scalar={vix_scalar:.2f} "
            f"(vix_move={vix_daily_move_pct:.2f}%)"
        )

    # Size by risk fraction — scaled up when model quality is high and down when VIX is elevated
    risk_amount = capital * risk_fraction * model_quality_scalar * vix_scalar
    contracts_by_risk = int(risk_amount / max_loss_per_contract)

    # Size by available capital (can't commit more collateral than we have)
    contracts_by_capital = int(capital / collateral_per_contract)

    # Take the smaller of risk-based and capital-based sizing
    contracts = min(contracts_by_risk, contracts_by_capital)
    contracts = max(1, min(contracts, contract_ceiling))

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
