"""Peak-Preis je Paper-Position fuer MFE/Early-Scratch.

Revision ID: 0005_paper_peak_price
Revises: 0004_paper_risk_amount
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_paper_peak_price"
down_revision: str | None = "0004_paper_risk_amount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("peak_price", sa.Numeric(24, 8), nullable=True),
    )
    op.execute(
        """
        UPDATE paper_positions
        SET peak_price = entry_price
        WHERE status = 'open' AND peak_price IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("paper_positions", "peak_price")
