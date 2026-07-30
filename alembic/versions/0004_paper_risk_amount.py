"""Risikobetrag je Paper-Position (Basis fuer R-Auswertungen).

Revision ID: 0004_paper_risk_amount
Revises: 0003_paper_trading
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_paper_risk_amount"
down_revision: str | None = "0003_paper_trading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("risk_amount", sa.Numeric(24, 8), nullable=False, server_default="0"),
    )
    # Bestandspositionen: 1R aus Stueckzahl und urspruenglichem Stop-Abstand.
    op.execute(
        """
        UPDATE paper_positions
        SET risk_amount = ABS(entry_price - stop_loss) * initial_quantity
        WHERE risk_amount = 0
        """
    )


def downgrade() -> None:
    op.drop_column("paper_positions", "risk_amount")
