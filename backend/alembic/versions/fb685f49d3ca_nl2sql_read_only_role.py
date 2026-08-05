"""nl2sql read-only role

Creates `finzorr_nl2sql_ro`: SELECT on `fundamentals` ONLY, everything else
revoked, plus a DB-enforced statement_timeout — layer 5 of the NL2SQL defense.
Even a validator bypass physically cannot write or read other tables.

Revision ID: fb685f49d3ca
Revises: 902b36b1c146
Create Date: 2026-08-06

"""
from collections.abc import Sequence

from alembic import op

revision: str = "fb685f49d3ca"
down_revision: str | Sequence[str] | None = "902b36b1c146"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# NOTE: role password is fine to be non-secret here — the role can only ever
# SELECT from the public `fundamentals` table, and the DB is never exposed
# outside the Docker network / localhost.
_ROLE = "finzorr_nl2sql_ro"
_PASSWORD = "nl2sql_ro"  # noqa: S105


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_ROLE}') THEN
                CREATE ROLE {_ROLE} LOGIN PASSWORD '{_PASSWORD}';
            END IF;
        END $$;
        """
    )
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {_ROLE}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_ROLE}")
    op.execute(f"GRANT SELECT ON fundamentals TO {_ROLE}")
    op.execute(f"ALTER ROLE {_ROLE} SET statement_timeout = '5s'")


def downgrade() -> None:
    op.execute(f"DROP ROLE IF EXISTS {_ROLE}")
