"""Market context JSON on signals and paper positions.

Revision ID: 0006_market_context
Revises: 0005_paper_peak_price
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_market_context"
down_revision: str | None = "0005_paper_peak_price"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
    op.add_column("signals", sa.Column("market_context", json_type, nullable=True))
    op.add_column("paper_positions", sa.Column("market_context", json_type, nullable=True))


def downgrade() -> None:
    op.drop_column("paper_positions", "market_context")
    op.drop_column("signals", "market_context")
