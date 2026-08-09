"""messages.ls_run_id — LangSmith root-run id for the feedback loop.

Revision ID: a1f2e3d4c5b6
Revises: 32796543a937
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1f2e3d4c5b6"
down_revision: str | Sequence[str] | None = "32796543a937"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("ls_run_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "ls_run_id")
