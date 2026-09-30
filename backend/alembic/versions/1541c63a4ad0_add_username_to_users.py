"""add username to users

Revision ID: 1541c63a4ad0
Revises: 138eb3b67fe4
Create Date: 2026-09-30 09:58:24.975569

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1541c63a4ad0"
down_revision: str | Sequence[str] | None = "138eb3b67fe4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add users.username; backfill existing users from their email's local part."""
    op.add_column("users", sa.Column("username", sa.String(length=30), nullable=True))
    # Existing accounts (e.g. demo@example.com -> "demo") get a username derived from
    # the email; collisions get a numeric suffix so the unique index can be created.
    op.execute(
        """
        WITH base AS (
            SELECT id, created_at,
                   COALESCE(NULLIF(LEFT(REGEXP_REPLACE(SPLIT_PART(email, '@', 1),
                                                       '[^A-Za-z0-9_.-]', '', 'g'), 24), ''),
                            'user') AS name
            FROM users
        ), ranked AS (
            SELECT id, name,
                   ROW_NUMBER() OVER (PARTITION BY LOWER(name) ORDER BY created_at, id) AS rn
            FROM base
        )
        UPDATE users u
        -- Pad only names shorter than 3 chars: RPAD also *truncates* longer ones.
        SET username = CASE
                WHEN r.rn = 1 AND LENGTH(r.name) >= 3 THEN r.name
                WHEN LENGTH(r.name) < 3 THEN RPAD(r.name, 3, '0') || '_' || r.rn
                ELSE r.name || '_' || r.rn
            END
        FROM ranked r
        WHERE u.id = r.id
        """
    )
    op.alter_column("users", "username", nullable=False)
    op.create_index("uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_column("users", "username")
