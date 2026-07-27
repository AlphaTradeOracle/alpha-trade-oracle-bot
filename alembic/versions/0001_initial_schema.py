"""Initiales Schema des Alpha Trade Oracle Bot.

Erstellt alle 18 Tabellen samt Constraints und Indizes. Die Datei wurde aus den
SQLAlchemy-Modellen erzeugt (siehe docs/DATA_MODEL.md).

Revision ID: 0001_initial_schema
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('application_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('correlation_id', sa.String(length=64), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_application_events'))
    )
    op.create_index(op.f('ix_application_events_created_at'), 'application_events', ['created_at'], unique=False)
    op.create_index('ix_event_type_created', 'application_events', ['event_type', 'created_at'], unique=False)
    op.create_table('assets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('base_asset', sa.String(length=16), nullable=False),
    sa.Column('quote_asset', sa.String(length=16), nullable=False),
    sa.Column('exchange', sa.String(length=32), nullable=False),
    sa.Column('price_precision', sa.Integer(), nullable=False),
    sa.Column('quantity_precision', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assets'))
    )
    op.create_index(op.f('ix_assets_symbol'), 'assets', ['symbol'], unique=True)
    op.create_table('model_configs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('temperature', sa.Float(), nullable=False),
    sa.Column('max_tokens', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_model_configs')),
    sa.UniqueConstraint('name', name=op.f('uq_model_configs_name'))
    )
    op.create_table('scheduled_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_key', sa.String(length=128), nullable=False),
    sa.Column('job_type', sa.String(length=64), nullable=False),
    sa.Column('interval_seconds', sa.Integer(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=32), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('run_count', sa.Integer(), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scheduled_jobs')),
    sa.UniqueConstraint('job_key', name=op.f('uq_scheduled_jobs_job_key'))
    )
    op.create_table('strategies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strategies')),
    sa.UniqueConstraint('name', name=op.f('uq_strategies_name'))
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_ref', sa.String(length=64), nullable=True),
    sa.Column('display_name', sa.String(length=128), nullable=True),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('external_ref', name=op.f('uq_users_external_ref'))
    )
    op.create_table('indicator_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('candle_open_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('close_price', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('ema_9', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('ema_20', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('ema_50', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('ema_100', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('ema_200', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('sma_50', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('sma_200', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('rsi_14', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('macd', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('macd_signal', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('macd_histogram', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('stoch_rsi_k', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('stoch_rsi_d', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('roc_14', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('bb_upper', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('bb_middle', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('bb_lower', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('bb_width', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('atr_14', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('atr_percent', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('adx_14', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('plus_di', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('minus_di', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('obv', sa.Numeric(precision=28, scale=8), nullable=True),
    sa.Column('volume_ma_20', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('volume_ratio', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('supertrend', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('supertrend_direction', sa.Integer(), nullable=True),
    sa.Column('vwap', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('trend_direction', sa.String(length=16), nullable=True),
    sa.Column('trend_strength', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('structure_state', sa.String(length=32), nullable=True),
    sa.Column('nearest_support', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('nearest_resistance', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('extra_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_indicator_snapshots_asset_id_assets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_indicator_snapshots')),
    sa.UniqueConstraint('asset_id', 'timeframe', 'candle_open_time', name='uq_snapshot_asset_tf_candle')
    )
    op.create_index('ix_snapshot_lookup', 'indicator_snapshots', ['asset_id', 'timeframe', 'candle_open_time'], unique=False)
    op.create_table('market_candles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('open_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('close_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('high', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('low', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('close', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('volume', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('quote_volume', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('trade_count', sa.Integer(), nullable=True),
    sa.Column('is_closed', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_market_candles_asset_id_assets'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_market_candles')),
    sa.UniqueConstraint('asset_id', 'timeframe', 'open_time', name='uq_candle_asset_tf_time')
    )
    op.create_index('ix_candle_lookup', 'market_candles', ['asset_id', 'timeframe', 'open_time'], unique=False)
    op.create_table('strategy_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('trend_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('momentum_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('volume_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('volatility_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('market_structure_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('multi_timeframe_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('sentiment_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('risk_reward_weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('min_score', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('min_risk_reward_ratio', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('atr_multiplier', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('abs(trend_weight + momentum_weight + volume_weight + volatility_weight + market_structure_weight + multi_timeframe_weight + sentiment_weight + risk_reward_weight - 1.0) < 0.000001', name=op.f('ck_strategy_versions_weights_sum_to_one')),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], name=op.f('fk_strategy_versions_strategy_id_strategies'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strategy_versions')),
    sa.UniqueConstraint('strategy_id', 'version', name='uq_strategy_version')
    )
    op.create_table('telegram_chats',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('chat_type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('notifications_enabled', sa.Boolean(), nullable=False),
    sa.Column('min_score_override', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_telegram_chats_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_telegram_chats'))
    )
    op.create_index(op.f('ix_telegram_chats_chat_id'), 'telegram_chats', ['chat_id'], unique=True)
    op.create_table('backtest_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_version_id', sa.Integer(), nullable=True),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fee_percent', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('slippage_percent', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('initial_capital', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id'], name=op.f('fk_backtest_runs_strategy_version_id_strategy_versions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backtest_runs'))
    )
    op.create_table('signals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('strategy_version_id', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('direction', sa.String(length=16), nullable=False),
    sa.Column('analyzed_timeframes', sa.String(length=64), nullable=False),
    sa.Column('primary_timeframe', sa.String(length=8), nullable=False),
    sa.Column('market_phase', sa.String(length=32), nullable=False),
    sa.Column('score', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('confidence', sa.String(length=16), nullable=False),
    sa.Column('reference_price', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('entry_low', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('entry_high', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('stop_loss', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('take_profit_1', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('take_profit_2', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('take_profit_3', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('risk_reward_ratio', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('risk_percent', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('suggested_position_size', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('data_quality', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('invalidation_note', sa.Text(), nullable=True),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('counter_arguments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('indicators_used', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('llm_summary', sa.Text(), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('is_dispatched', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_signals_asset_id_assets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id'], name=op.f('fk_signals_strategy_version_id_strategy_versions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_signals'))
    )
    op.create_index('ix_signal_asset_created', 'signals', ['asset_id', 'created_at'], unique=False)
    op.create_index('ix_signal_direction_created', 'signals', ['direction', 'created_at'], unique=False)
    op.create_index('ix_signal_fingerprint', 'signals', ['fingerprint'], unique=False)
    op.create_table('watchlists',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telegram_chat_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('timeframes', sa.String(length=64), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_watchlists_asset_id_assets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['telegram_chat_id'], ['telegram_chats.id'], name=op.f('fk_watchlists_telegram_chat_id_telegram_chats'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watchlists')),
    sa.UniqueConstraint('telegram_chat_id', 'asset_id', name='uq_watchlist_chat_asset')
    )
    op.create_table('backtest_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('backtest_run_id', sa.Integer(), nullable=False),
    sa.Column('scope', sa.String(length=64), nullable=False),
    sa.Column('metric_name', sa.String(length=64), nullable=False),
    sa.Column('metric_value', sa.Numeric(precision=20, scale=8), nullable=True),
    sa.ForeignKeyConstraint(['backtest_run_id'], ['backtest_runs.id'], name=op.f('fk_backtest_metrics_backtest_run_id_backtest_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backtest_metrics')),
    sa.UniqueConstraint('backtest_run_id', 'scope', 'metric_name', name='uq_backtest_metric_scope_name')
    )
    op.create_table('backtest_trades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('backtest_run_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('direction', sa.String(length=16), nullable=False),
    sa.Column('entry_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('entry_price', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('exit_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('exit_price', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('exit_reason', sa.String(length=32), nullable=True),
    sa.Column('stop_loss', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('take_profit_1', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('take_profit_2', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('take_profit_3', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('quantity', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('gross_pnl', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('fees', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('net_pnl', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('pnl_percent', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('risk_reward_planned', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('holding_minutes', sa.Integer(), nullable=True),
    sa.Column('signal_score', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.ForeignKeyConstraint(['backtest_run_id'], ['backtest_runs.id'], name=op.f('fk_backtest_trades_backtest_run_id_backtest_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backtest_trades'))
    )
    op.create_index('ix_backtest_trade_run', 'backtest_trades', ['backtest_run_id', 'entry_at'], unique=False)
    op.create_table('llm_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('signal_id', sa.Integer(), nullable=True),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('completion_tokens', sa.Integer(), nullable=True),
    sa.Column('total_tokens', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('validation_error', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default='now()', nullable=False),
    sa.ForeignKeyConstraint(['signal_id'], ['signals.id'], name=op.f('fk_llm_requests_signal_id_signals'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_requests'))
    )
    op.create_index(op.f('ix_llm_requests_created_at'), 'llm_requests', ['created_at'], unique=False)
    op.create_table('signal_deliveries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('signal_id', sa.Integer(), nullable=False),
    sa.Column('telegram_chat_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('suppression_reason', sa.String(length=64), nullable=True),
    sa.Column('message_id', sa.BigInteger(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default='now()', nullable=False),
    sa.ForeignKeyConstraint(['signal_id'], ['signals.id'], name=op.f('fk_signal_deliveries_signal_id_signals'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['telegram_chat_id'], ['telegram_chats.id'], name=op.f('fk_signal_deliveries_telegram_chat_id_telegram_chats'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_signal_deliveries')),
    sa.UniqueConstraint('signal_id', 'telegram_chat_id', name='uq_delivery_signal_chat')
    )
    op.create_table('signal_score_components',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('signal_id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('raw_score', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('weight', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('weighted_score', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['signal_id'], ['signals.id'], name=op.f('fk_signal_score_components_signal_id_signals'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_signal_score_components')),
    sa.UniqueConstraint('signal_id', 'category', name='uq_score_component_signal_category')
    )


def downgrade() -> None:
    op.drop_table('signal_score_components')
    op.drop_table('signal_deliveries')
    op.drop_index(op.f('ix_llm_requests_created_at'), table_name='llm_requests')
    op.drop_table('llm_requests')
    op.drop_index('ix_backtest_trade_run', table_name='backtest_trades')
    op.drop_table('backtest_trades')
    op.drop_table('backtest_metrics')
    op.drop_table('watchlists')
    op.drop_index('ix_signal_asset_created', table_name='signals')
    op.drop_index('ix_signal_direction_created', table_name='signals')
    op.drop_index('ix_signal_fingerprint', table_name='signals')
    op.drop_table('signals')
    op.drop_table('backtest_runs')
    op.drop_index(op.f('ix_telegram_chats_chat_id'), table_name='telegram_chats')
    op.drop_table('telegram_chats')
    op.drop_table('strategy_versions')
    op.drop_index('ix_candle_lookup', table_name='market_candles')
    op.drop_table('market_candles')
    op.drop_index('ix_snapshot_lookup', table_name='indicator_snapshots')
    op.drop_table('indicator_snapshots')
    op.drop_table('users')
    op.drop_table('strategies')
    op.drop_table('scheduled_jobs')
    op.drop_table('model_configs')
    op.drop_index(op.f('ix_assets_symbol'), table_name='assets')
    op.drop_table('assets')
    op.drop_index(op.f('ix_application_events_created_at'), table_name='application_events')
    op.drop_index('ix_event_type_created', table_name='application_events')
    op.drop_table('application_events')
