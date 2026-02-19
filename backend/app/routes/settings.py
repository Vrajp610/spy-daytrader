"""Settings routes: strategy configuration and trading settings."""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models import StrategyConfig, TradingConfig
from app.config import settings
from app.schemas import (
    StrategyConfigOut, StrategyConfigUpdate,
    TradingSettingsOut, TradingSettingsUpdate,
)
from app.services.trading_engine import trading_engine

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/strategies", response_model=list[StrategyConfigOut])
async def get_strategy_configs(db: AsyncSession = Depends(get_db)):
    stmt = select(StrategyConfig)
    result = await db.execute(stmt)
    configs = result.scalars().all()

    # Build map of DB configs by name
    db_map = {c.name: c for c in configs}

    # Merge: use DB config if exists, otherwise default from engine
    merged = []
    for name, strategy in trading_engine.strategies.items():
        if name in db_map:
            merged.append(StrategyConfigOut.model_validate(db_map[name]))
        else:
            merged.append(StrategyConfigOut(
                id=0,
                name=name,
                enabled=name in trading_engine.enabled_strategies,
                params=strategy.params,
            ))
    return merged


@router.put("/strategies/{name}", response_model=StrategyConfigOut)
async def update_strategy_config(
    name: str, update: StrategyConfigUpdate, db: AsyncSession = Depends(get_db)
):
    if name not in trading_engine.strategies:
        raise HTTPException(404, f"Strategy '{name}' not found")

    # Upsert in DB
    stmt = select(StrategyConfig).where(StrategyConfig.name == name)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if config is None:
        config = StrategyConfig(
            name=name,
            enabled=True,
            params=trading_engine.strategies[name].params,
        )
        db.add(config)

    if update.enabled is not None:
        config.enabled = update.enabled
        if update.enabled:
            trading_engine.enabled_strategies.add(name)
        else:
            trading_engine.enabled_strategies.discard(name)

    if update.params is not None:
        config.params = update.params
        trading_engine.strategies[name].params = update.params

    await db.commit()
    await db.refresh(config)
    return StrategyConfigOut.model_validate(config)


@router.get("/trading", response_model=TradingSettingsOut)
async def get_trading_settings():
    """Get current trading configuration from database."""
    return TradingSettingsOut(
        initial_capital=settings.initial_capital,
        max_risk_per_trade=settings.max_risk_per_trade,
        daily_loss_limit=settings.daily_loss_limit,
        max_drawdown=settings.max_drawdown,
        max_position_pct=settings.max_position_pct,
        max_trades_per_day=settings.max_trades_per_day,
        cooldown_after_consecutive_losses=settings.cooldown_after_consecutive_losses,
        cooldown_minutes=settings.cooldown_minutes,
        min_signal_confidence=settings.min_signal_confidence,
        default_spread_width=settings.default_spread_width,
        preferred_dte_min=settings.preferred_dte_min,
        preferred_dte_max=settings.preferred_dte_max,
        target_delta_short=settings.target_delta_short,
        credit_profit_target_pct=settings.credit_profit_target_pct,
        max_contracts_per_trade=settings.max_contracts_per_trade,
    )


@router.put("/trading", response_model=TradingSettingsOut)
async def update_trading_settings(update: TradingSettingsUpdate):
    """Update trading configuration. Persists to database and takes effect immediately."""
    if update.initial_capital is not None:
        if update.initial_capital < 100:
            raise HTTPException(400, "Initial capital must be >= $100")
        settings.initial_capital = update.initial_capital
        if not trading_engine.running:
            trading_engine.paper_engine.capital = update.initial_capital
            trading_engine.paper_engine.initial_capital = update.initial_capital
            trading_engine.paper_engine.peak_capital = update.initial_capital

    if update.max_risk_per_trade is not None:
        if not 0.001 <= update.max_risk_per_trade <= 0.10:
            raise HTTPException(400, "Max risk per trade must be between 0.1% and 10%")
        settings.max_risk_per_trade = update.max_risk_per_trade
        trading_engine.risk_manager.max_risk_per_trade = update.max_risk_per_trade

    if update.daily_loss_limit is not None:
        if not 0.005 <= update.daily_loss_limit <= 0.20:
            raise HTTPException(400, "Daily loss limit must be between 0.5% and 20%")
        settings.daily_loss_limit = update.daily_loss_limit
        trading_engine.risk_manager.daily_loss_limit = update.daily_loss_limit

    if update.max_drawdown is not None:
        if not 0.02 <= update.max_drawdown <= 0.50:
            raise HTTPException(400, "Max drawdown must be between 2% and 50%")
        settings.max_drawdown = update.max_drawdown
        trading_engine.risk_manager.max_drawdown = update.max_drawdown

    if update.max_position_pct is not None:
        if not 0.05 <= update.max_position_pct <= 1.0:
            raise HTTPException(400, "Max position % must be between 5% and 100%")
        settings.max_position_pct = update.max_position_pct
        trading_engine.risk_manager.max_position_pct = update.max_position_pct

    if update.max_trades_per_day is not None:
        if not 1 <= update.max_trades_per_day <= 100:
            raise HTTPException(400, "Max trades per day must be between 1 and 100")
        settings.max_trades_per_day = update.max_trades_per_day
        trading_engine.risk_manager.max_trades_per_day = update.max_trades_per_day

    if update.cooldown_after_consecutive_losses is not None:
        if not 1 <= update.cooldown_after_consecutive_losses <= 20:
            raise HTTPException(400, "Cooldown after losses must be between 1 and 20")
        settings.cooldown_after_consecutive_losses = update.cooldown_after_consecutive_losses
        trading_engine.risk_manager.cooldown_after_losses = update.cooldown_after_consecutive_losses

    if update.cooldown_minutes is not None:
        if not 1 <= update.cooldown_minutes <= 240:
            raise HTTPException(400, "Cooldown minutes must be between 1 and 240")
        settings.cooldown_minutes = update.cooldown_minutes
        trading_engine.risk_manager.cooldown_minutes = update.cooldown_minutes

    if update.min_signal_confidence is not None:
        if not 0.0 <= update.min_signal_confidence <= 1.0:
            raise HTTPException(400, "Min signal confidence must be between 0% and 100%")
        settings.min_signal_confidence = update.min_signal_confidence

    # Options settings
    if update.default_spread_width is not None:
        if not 1.0 <= update.default_spread_width <= 20.0:
            raise HTTPException(400, "Spread width must be between $1 and $20")
        settings.default_spread_width = update.default_spread_width

    if update.preferred_dte_min is not None:
        if not 1 <= update.preferred_dte_min <= 30:
            raise HTTPException(400, "Min DTE must be between 1 and 30")
        settings.preferred_dte_min = update.preferred_dte_min

    if update.preferred_dte_max is not None:
        if not 3 <= update.preferred_dte_max <= 60:
            raise HTTPException(400, "Max DTE must be between 3 and 60")
        settings.preferred_dte_max = update.preferred_dte_max

    if update.target_delta_short is not None:
        if not 0.05 <= update.target_delta_short <= 0.50:
            raise HTTPException(400, "Target delta must be between 0.05 and 0.50")
        settings.target_delta_short = update.target_delta_short

    if update.credit_profit_target_pct is not None:
        if not 0.10 <= update.credit_profit_target_pct <= 1.0:
            raise HTTPException(400, "Credit profit target must be between 10% and 100%")
        settings.credit_profit_target_pct = update.credit_profit_target_pct

    if update.max_contracts_per_trade is not None:
        if not 1 <= update.max_contracts_per_trade <= 100:
            raise HTTPException(400, "Max contracts must be between 1 and 100")
        settings.max_contracts_per_trade = update.max_contracts_per_trade

    # Persist to database
    async with async_session() as db:
        stmt = select(TradingConfig).where(TradingConfig.id == 1)
        result = await db.execute(stmt)
        config = result.scalar_one_or_none()

        if config is None:
            config = TradingConfig(id=1)
            db.add(config)

        config.initial_capital = settings.initial_capital
        config.max_risk_per_trade = settings.max_risk_per_trade
        config.daily_loss_limit = settings.daily_loss_limit
        config.max_drawdown = settings.max_drawdown
        config.max_position_pct = settings.max_position_pct
        config.max_trades_per_day = settings.max_trades_per_day
        config.cooldown_after_consecutive_losses = settings.cooldown_after_consecutive_losses
        config.cooldown_minutes = settings.cooldown_minutes
        config.min_signal_confidence = settings.min_signal_confidence
        config.default_spread_width = settings.default_spread_width
        config.preferred_dte_min = settings.preferred_dte_min
        config.preferred_dte_max = settings.preferred_dte_max
        config.target_delta_short = settings.target_delta_short
        config.credit_profit_target_pct = settings.credit_profit_target_pct
        config.max_contracts_per_trade = settings.max_contracts_per_trade

        await db.commit()

    return await get_trading_settings()


async def load_trading_config_from_db():
    """Load persisted trading config from database on startup."""
    try:
        async with async_session() as db:
            stmt = select(TradingConfig).where(TradingConfig.id == 1)
            result = await db.execute(stmt)
            config = result.scalar_one_or_none()

            if config is None:
                return  # No saved config, use defaults

            settings.initial_capital = config.initial_capital
            settings.max_risk_per_trade = config.max_risk_per_trade
            settings.daily_loss_limit = config.daily_loss_limit
            settings.max_drawdown = config.max_drawdown
            settings.max_position_pct = config.max_position_pct
            settings.max_trades_per_day = config.max_trades_per_day
            settings.cooldown_after_consecutive_losses = config.cooldown_after_consecutive_losses
            settings.cooldown_minutes = config.cooldown_minutes
            if config.min_signal_confidence is not None:
                settings.min_signal_confidence = config.min_signal_confidence
            if config.default_spread_width is not None:
                settings.default_spread_width = config.default_spread_width
            if config.preferred_dte_min is not None:
                settings.preferred_dte_min = config.preferred_dte_min
            if config.preferred_dte_max is not None:
                settings.preferred_dte_max = config.preferred_dte_max
            if config.target_delta_short is not None:
                settings.target_delta_short = config.target_delta_short
            if config.credit_profit_target_pct is not None:
                settings.credit_profit_target_pct = config.credit_profit_target_pct
            if config.max_contracts_per_trade is not None:
                settings.max_contracts_per_trade = config.max_contracts_per_trade

            import logging
            logging.getLogger(__name__).info("Loaded trading config from database")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not load trading config from DB: {e}")
