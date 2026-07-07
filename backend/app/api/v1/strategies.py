"""Strategy registry endpoints (read-only in Phase 1)."""
from __future__ import annotations

from fastapi import APIRouter

# Importing the strategy modules triggers their @registry.register decorators.
from app.strategies import advanced as _advanced  # noqa: F401
from app.strategies import macd_crossover as _macd  # noqa: F401
from app.strategies import mean_reversion as _mr  # noqa: F401
from app.strategies import momentum_breakout as _mb  # noqa: F401
from app.strategies import opening_range_breakout as _orb  # noqa: F401
from app.strategies import vwap_deviation as _vwap  # noqa: F401
from app.strategies.base import registry

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("")
async def list_strategies() -> list[dict]:
    out = []
    for name, cls in registry.all().items():
        out.append({
            "name": name,
            "description": cls.description,
            "timeframe": cls.timeframe,
            "lookback_bars": cls.lookback_bars,
            "default_params": cls.default_params,
        })
    return out
