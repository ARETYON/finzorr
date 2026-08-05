"""Direct market-data REST passthrough (manual testing / future widgets)."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.market_data.yfinance_provider import SymbolNotFoundError, provider
from app.models.user import User

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/quote/{symbol}")
async def get_quote(symbol: str, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return asdict(await provider.get_quote(symbol))
    except SymbolNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"symbol not found: {symbol}") from exc
