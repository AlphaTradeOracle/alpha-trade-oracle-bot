"""Persist signal.no_trade_reason for scanner audits.

Revision ID: 0007_signal_no_trade_reason
Revises: 0006_market_context
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_signal_no_trade_reason"
down_revision: str | None = "0006_market_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("no_trade_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("signals", "no_trade_reason")
