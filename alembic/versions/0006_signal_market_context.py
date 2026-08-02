"""Add market_context + coin_score to signals."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.database.base import JSON_COLUMN, SCORE

revision = "0006_signal_market_context"
down_revision = "0005_paper_peak_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("market_context", JSON_COLUMN, nullable=True))
    op.add_column("signals", sa.Column("coin_score", SCORE, nullable=True))


def downgrade() -> None:
    op.drop_column("signals", "coin_score")
    op.drop_column("signals", "market_context")
