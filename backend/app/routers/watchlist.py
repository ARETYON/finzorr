"""Watchlist REST — idempotent add/remove, user-scoped."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.pagination import Page, page_params
from app.db.session import get_db
from app.models.user import User
from app.models.watchlist_item import WatchlistItem
from app.schemas.misc import WatchlistAddOut, WatchlistItemOut

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistAddIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    exchange: str = Field(default="NSE", pattern="^(NSE|BSE)$")


@router.get("", response_model=list[WatchlistItemOut])
async def list_watchlist(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Page = Depends(page_params),
) -> list[WatchlistItemOut]:
    result = await db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.user_id == user.id)
        .order_by(WatchlistItem.added_at)
        .limit(page.limit)
        .offset(page.offset)
    )
    return [
        WatchlistItemOut(symbol=w.symbol, exchange=w.exchange, added_at=w.added_at)
        for w in result.scalars()
    ]


@router.post("", response_model=WatchlistAddOut, status_code=status.HTTP_201_CREATED)
async def add_item(
    body: WatchlistAddIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistAddOut:
    stmt = (
        pg_insert(WatchlistItem)
        .values(user_id=user.id, symbol=body.symbol.upper(), exchange=body.exchange)
        .on_conflict_do_nothing(index_elements=["user_id", "symbol"])
    )
    await db.execute(stmt)
    await db.commit()
    return WatchlistAddOut(symbol=body.symbol.upper(), status="added")


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_item(
    symbol: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        delete(WatchlistItem).where(
            WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol.upper()
        )
    )
    await db.commit()
    if getattr(result, "rowcount", 0) == 0:  # 404 like every other delete
        raise HTTPException(status.HTTP_404_NOT_FOUND, "symbol not on watchlist")
