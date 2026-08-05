"""Watchlist REST — idempotent add/remove, user-scoped."""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.watchlist_item import WatchlistItem

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistAddIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    exchange: str = Field(default="NSE", pattern="^(NSE|BSE)$")


@router.get("")
async def list_watchlist(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict[str, str]]:
    result = await db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.user_id == user.id)
        .order_by(WatchlistItem.added_at)
    )
    return [
        {"symbol": w.symbol, "exchange": w.exchange, "added_at": w.added_at.isoformat()}
        for w in result.scalars()
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_item(
    body: WatchlistAddIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    stmt = (
        pg_insert(WatchlistItem)
        .values(user_id=user.id, symbol=body.symbol.upper(), exchange=body.exchange)
        .on_conflict_do_nothing(index_elements=["user_id", "symbol"])
    )
    await db.execute(stmt)
    await db.commit()
    return {"symbol": body.symbol.upper(), "status": "added"}


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_item(
    symbol: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.execute(
        delete(WatchlistItem).where(
            WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol.upper()
        )
    )
    await db.commit()
