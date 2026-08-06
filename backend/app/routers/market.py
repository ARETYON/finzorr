"""Direct market-data REST passthrough (manual testing / future widgets)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.market_data.yfinance_provider import SymbolNotFoundError, provider
from app.models.user import User
from app.schemas.misc import QuoteOut

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/quote/{symbol}", response_model=QuoteOut)
async def get_quote(symbol: str, _user: User = Depends(get_current_user)) -> QuoteOut:
    try:
        return QuoteOut(**asdict(await provider.get_quote(symbol)))
    except SymbolNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"symbol not found: {symbol}") from exc
