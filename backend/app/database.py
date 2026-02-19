"""SQLAlchemy async engine and session factory."""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def _migrate_missing_columns(conn):
    """Add columns that exist in the ORM model but are missing from the DB."""
    # Map of table -> list of (column_name, column_type, default)
    migrations = {
        "trades": [
            ("confidence", "REAL", None),
            ("slippage", "REAL", None),
            ("commission", "REAL", "0.0"),
            ("vix_at_entry", "REAL", None),
            ("bars_held", "INTEGER", None),
            ("mae", "REAL", None),
            ("mfe", "REAL", None),
            ("mae_pct", "REAL", None),
            ("mfe_pct", "REAL", None),
            # Options columns
            ("option_strategy_type", "TEXT", None),
            ("contract_symbol", "TEXT", None),
            ("legs_json", "TEXT", None),
            ("strike", "REAL", None),
            ("expiration_date", "TEXT", None),
            ("option_type", "TEXT", None),
            ("net_premium", "REAL", None),
            ("max_loss", "REAL", None),
            ("max_profit", "REAL", None),
            ("entry_delta", "REAL", None),
            ("entry_theta", "REAL", None),
            ("entry_iv", "REAL", None),
            ("underlying_entry", "REAL", None),
            ("underlying_exit", "REAL", None),
            ("contracts", "INTEGER", None),
        ],
        "trading_config": [
            ("min_signal_confidence", "REAL", "0.6"),
            # Options config
            ("default_spread_width", "REAL", "3.0"),
            ("preferred_dte_min", "INTEGER", "5"),
            ("preferred_dte_max", "INTEGER", "14"),
            ("target_delta_short", "REAL", "0.20"),
            ("credit_profit_target_pct", "REAL", "0.50"),
            ("max_contracts_per_trade", "INTEGER", "10"),
        ],
    }

    for table, columns in migrations.items():
        # Get existing columns
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}

        for col_name, col_type, default in columns:
            if col_name not in existing:
                default_clause = f" DEFAULT {default}" if default is not None else ""
                stmt = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}{default_clause}"
                await conn.execute(text(stmt))
                logger.info(f"Migration: added {table}.{col_name} ({col_type})")


async def init_db():
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_missing_columns(conn)
