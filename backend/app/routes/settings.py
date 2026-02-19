"""Settings routes: strategy configuration."""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import StrategyConfig
from app.schemas import StrategyConfigOut, StrategyConfigUpdate
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
