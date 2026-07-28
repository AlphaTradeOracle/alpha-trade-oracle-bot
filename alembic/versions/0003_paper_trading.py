"""Paper-Trading Migration.

Revision ID: 0003_paper_trading
Revises: 0002_universe_fields
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_paper_trading"
down_revision: str | None = "0002_universe_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("initial_balance", sa.Numeric(24, 8), nullable=False),
        sa.Column("cash_balance", sa.Numeric(24, 8), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("margin_per_trade", sa.Numeric(24, 8), nullable=False),
        sa.Column("leverage", sa.Numeric(8, 2), nullable=False, server_default="5"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name="uq_paper_accounts_name"),
    )

    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signal_id",
            sa.Integer(),
            sa.ForeignKey("signals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("timeframe", sa.String(length=8), nullable=False, server_default="1h"),
        sa.Column("entry_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("stop_loss", sa.Numeric(24, 8), nullable=False),
        sa.Column("current_stop", sa.Numeric(24, 8), nullable=False),
        sa.Column("take_profit_1", sa.Numeric(24, 8), nullable=False),
        sa.Column("take_profit_2", sa.Numeric(24, 8), nullable=False),
        sa.Column("take_profit_3", sa.Numeric(24, 8), nullable=False),
        sa.Column("initial_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("margin_used", sa.Numeric(24, 8), nullable=False),
        sa.Column("notional", sa.Numeric(24, 8), nullable=False),
        sa.Column("leverage", sa.Numeric(8, 2), nullable=False),
        sa.Column("tp1_filled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tp2_filled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tp3_filled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("realized_pnl", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("fees", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("signal_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("exit_reason", sa.String(length=32), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_paper_positions_status", "paper_positions", ["status"])
    op.create_index(
        "ix_paper_positions_symbol_status", "paper_positions", ["symbol", "status"]
    )

    op.create_table(
        "paper_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("paper_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("fee", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("pnl", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_paper_fills_position", "paper_fills", ["position_id", "filled_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_paper_fills_position", table_name="paper_fills")
    op.drop_table("paper_fills")
    op.drop_index("ix_paper_positions_symbol_status", table_name="paper_positions")
    op.drop_index("ix_paper_positions_status", table_name="paper_positions")
    op.drop_table("paper_positions")
    op.drop_table("paper_accounts")
