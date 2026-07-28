"""Universe-Felder fuer Market-Cap Top-N Scans.

Revision ID: 0002_universe_fields
Revises: 0001_initial_schema
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_universe_fields"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("coingecko_id", sa.String(length=64), nullable=True))
    op.add_column("assets", sa.Column("market_cap_rank", sa.Integer(), nullable=True))
    op.add_column("assets", sa.Column("market_cap_usd", sa.Numeric(28, 8), nullable=True))
    op.add_column(
        "assets",
        sa.Column("in_universe", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "assets", sa.Column("last_ranked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "assets", sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(op.f("ix_assets_market_cap_rank"), "assets", ["market_cap_rank"], unique=False)
    op.create_index(op.f("ix_assets_in_universe"), "assets", ["in_universe"], unique=False)
    op.create_index(
        "ix_assets_universe_scan",
        "assets",
        ["in_universe", "last_scanned_at", "market_cap_rank"],
        unique=False,
    )
    # server_default nur fuer Backfill; danach entfernen, damit ORM/Default greifen.
    op.alter_column("assets", "in_universe", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_assets_universe_scan", table_name="assets")
    op.drop_index(op.f("ix_assets_in_universe"), table_name="assets")
    op.drop_index(op.f("ix_assets_market_cap_rank"), table_name="assets")
    op.drop_column("assets", "last_scanned_at")
    op.drop_column("assets", "last_ranked_at")
    op.drop_column("assets", "in_universe")
    op.drop_column("assets", "market_cap_usd")
    op.drop_column("assets", "market_cap_rank")
    op.drop_column("assets", "coingecko_id")
