"""SQLAlchemy declarative base shared by every model."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root declarative base; models must import this, never redefine it."""
