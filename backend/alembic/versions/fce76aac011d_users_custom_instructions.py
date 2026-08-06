"""users.custom_instructions

Revision ID: fce76aac011d
Revises: fb685f49d3ca
Create Date: 2026-08-06 08:45:23.946754

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fce76aac011d'
down_revision: Union[str, Sequence[str], None] = 'fb685f49d3ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    NOTE: autogenerate originally emitted drop_table for LangGraph's runtime
    checkpoint tables here (they are created by checkpointer.setup(), not our
    models). Those drops broke `upgrade head` on a clean database and destroyed
    live checkpoints; they were removed, and alembic/env.py now filters those
    tables out of autogenerate entirely.
    """
    op.add_column('users', sa.Column('custom_instructions', sa.String(length=2000), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'custom_instructions')
