"""ORM models. Import them all here so Alembic autogenerate sees every table."""

from app.models.chat_session import ChatSession
from app.models.document import Document
from app.models.feedback import Feedback
from app.models.fundamental import Fundamental
from app.models.message import Message
from app.models.oauth_token import OAuthToken
from app.models.persona import Persona
from app.models.price_alert import PriceAlert
from app.models.scheduled_task import ScheduledTask
from app.models.share_token import ShareToken
from app.models.user import User
from app.models.watchlist_item import WatchlistItem

__all__ = [
    "ChatSession",
    "Document",
    "Feedback",
    "Fundamental",
    "Message",
    "OAuthToken",
    "Persona",
    "PriceAlert",
    "ScheduledTask",
    "ShareToken",
    "User",
    "WatchlistItem",
]
