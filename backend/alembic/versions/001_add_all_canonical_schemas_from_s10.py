"""add_all_canonical_schemas_from_s10

Revision ID: 001_s10
Revises: None
Create Date: 2025-03-01

Creates all §10 canonical tables and converts time-series tables to TimescaleDB hypertables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_s10"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hypertables (partitioned by timestamp); created as regular tables then converted.
HYPERTABLES = [
    "vault_metrics",
    "vault_concentration",
    "risk_score_history",
    "price_history",
    "transaction_lots",
    "position_snapshots",
    "api_usage_log",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")

    # ----- Regular tables (FK order) -----
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("approved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("rejected", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "capital_gains_tax_rate",
            sa.Numeric(4, 1),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cost_basis_default",
            sa.String(4),
            server_default=sa.text("'fifo'::character varying"),
            nullable=False,
        ),
        sa.Column(
            "data_freshness_pref",
            sa.String(15),
            server_default=sa.text("'balanced'::character varying"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "vaults",
        sa.Column("vault_id", sa.String(100), primary_key=True),
        sa.Column("chain", sa.String(20), primary_key=True),
        sa.Column("protocol", sa.String(30), nullable=False),
        sa.Column("vault_name", sa.Text(), nullable=True),
        sa.Column("contract_address", sa.String(100), nullable=True),
        sa.Column("asset_symbol", sa.String(20), nullable=True),
        sa.Column("asset_address", sa.String(100), nullable=True),
        sa.Column("vault_type", sa.String(20), nullable=True),
        sa.Column("deployment_date", sa.Date(), nullable=True),
        sa.Column(
            "is_tracked",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("curator", sa.String(100), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "wallets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address", sa.String(100), nullable=False),
        sa.Column("chain", sa.String(20), nullable=False),
        sa.Column("label", sa.String(50), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "sync_status",
            sa.String(20),
            server_default=sa.text("'pending'::character varying"),
            nullable=False,
        ),
        sa.Column("sync_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_block", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_wallets_user_address_chain",
        "wallets",
        ["user_id", "address", "chain"],
        unique=True,
    )

    op.create_table(
        "positions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chain", sa.String(20), nullable=False),
        sa.Column("protocol", sa.String(30), nullable=False),
        sa.Column("vault_or_market_id", sa.String(100), nullable=False),
        sa.Column("position_type", sa.String(10), nullable=False),
        sa.Column("asset_symbol", sa.String(20), nullable=True),
        sa.Column("asset_address", sa.String(100), nullable=True),
        sa.Column("current_shares_or_amount", sa.Numeric(30, 12), nullable=True),
        sa.Column("cost_basis_fifo_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("cost_basis_wac_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "total_yield_earned_usd",
            sa.Numeric(18, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_borrow_cost_usd",
            sa.Numeric(18, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("unrealised_pnl_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("unrealised_pnl_native", sa.Numeric(24, 8), nullable=True),
        sa.Column(
            "realised_pnl_fifo_usd",
            sa.Numeric(18, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "realised_pnl_wac_usd",
            sa.Numeric(18, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("health_factor", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "status",
            sa.String(10),
            server_default=sa.text("'active'::character varying"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reconstruction_status",
            sa.String(10),
            server_default=sa.text("'complete'::character varying"),
            nullable=False,
        ),
        sa.Column(
            "adapter_type",
            sa.String(15),
            server_default=sa.text("'adapter'::character varying"),
            nullable=False,
        ),
        sa.Column("manual_protocol_name", sa.Text(), nullable=True),
        sa.Column("pendle_position_type", sa.String(5), nullable=True),
        sa.Column("pendle_maturity_date", sa.Date(), nullable=True),
        sa.Column("pendle_implied_apy_at_entry", sa.Numeric(6, 3), nullable=True),
        sa.Column("pendle_yt_claimed_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("pendle_yt_pending_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("pendle_pt_maturity_value_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "recommendations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_vault_id", sa.String(100), nullable=True),
        sa.Column("target_chain", sa.String(20), nullable=True),
        sa.Column("trigger_type", sa.String(40), nullable=False),
        sa.Column("urgency", sa.String(10), nullable=False),
        sa.Column(
            "status",
            sa.String(15),
            server_default=sa.text("'active'::character varying"),
            nullable=False,
        ),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apy_source_at_creation", sa.Numeric(8, 4), nullable=True),
        sa.Column("apy_target_at_creation", sa.Numeric(8, 4), nullable=True),
        sa.Column("risk_source_at_creation", sa.Numeric(5, 1), nullable=True),
        sa.Column("risk_target_at_creation", sa.Numeric(5, 1), nullable=True),
        sa.Column("net_benefit_usd_30d", sa.Numeric(12, 2), nullable=True),
        sa.Column("gas_cost_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("bridge_cost_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("rationale_text", sa.Text(), nullable=True),
    )

    op.create_table(
        "recommendation_outcomes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "new_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "marked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actual_tx_hash", sa.Text(), nullable=True),
        sa.Column("user_note", sa.Text(), nullable=True),
    )

    op.create_table(
        "cost_service_config",
        sa.Column("service_key", sa.String(100), primary_key=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "cost_throttle_status",
        sa.Column("service_key", sa.String(100), primary_key=True),
        sa.Column("throttled_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ----- Hypertable tables (created as regular, then converted) -----
    op.create_table(
        "vault_metrics",
        sa.Column("vault_id", sa.String(100), primary_key=True),
        sa.Column("chain", sa.String(20), primary_key=True),
        sa.Column("protocol", sa.String(30), nullable=False),
        sa.Column("vault_name", sa.Text(), nullable=True),
        sa.Column("asset_symbol", sa.String(20), nullable=True),
        sa.Column("asset_address", sa.String(100), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("apy_gross", sa.Numeric(8, 4), nullable=True),
        sa.Column("apy_base", sa.Numeric(8, 4), nullable=True),
        sa.Column("apy_reward", sa.Numeric(8, 4), nullable=True),
        sa.Column("performance_fee_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("mgmt_fee_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("net_apy", sa.Numeric(8, 4), nullable=True),
        sa.Column("tvl_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("tvl_native", sa.Numeric(24, 8), nullable=True),
        sa.Column("utilisation_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("supply_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("borrow_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("redemption_type", sa.String(20), nullable=True),
        sa.Column("redemption_days_est", sa.Integer(), nullable=True),
        sa.Column("maturity_date", sa.Date(), nullable=True),
    )

    op.create_table(
        "vault_concentration",
        sa.Column("vault_id", sa.String(100), primary_key=True),
        sa.Column("chain", sa.String(20), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("top_n", sa.Integer(), nullable=True),
        sa.Column("top_n_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("top_holders", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_holders", sa.Integer(), nullable=True),
    )

    op.create_table(
        "risk_score_history",
        sa.Column("vault_id", sa.String(100), primary_key=True),
        sa.Column("chain", sa.String(20), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("previous_score", sa.Numeric(5, 1), nullable=True),
        sa.Column("new_score", sa.Numeric(5, 1), nullable=True),
        sa.Column("grade_before", sa.String(1), nullable=True),
        sa.Column("grade_after", sa.String(1), nullable=True),
        sa.Column("changed_layers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
    )

    op.create_table(
        "price_history",
        sa.Column("asset_address", sa.String(100), primary_key=True),
        sa.Column("chain", sa.String(20), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("price_usd", sa.Numeric(24, 8), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
    )

    op.create_table(
        "transaction_lots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("wallet_address", sa.String(100), nullable=False),
        sa.Column("chain", sa.String(20), nullable=False),
        sa.Column("protocol", sa.String(30), nullable=False),
        sa.Column("vault_or_market_id", sa.String(100), nullable=False),
        sa.Column("action", sa.String(15), nullable=False),
        sa.Column("asset_symbol", sa.String(20), nullable=True),
        sa.Column("asset_address", sa.String(100), nullable=True),
        sa.Column("amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("amount_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("price_per_unit_usd", sa.Numeric(24, 8), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tx_hash", sa.String(100), nullable=True),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column(
            "lot_status",
            sa.String(20),
            server_default=sa.text("'open'::character varying"),
            nullable=False,
        ),
        sa.Column("remaining_amount", sa.Numeric(30, 12), nullable=True),
        sa.Column(
            "source",
            sa.String(15),
            server_default=sa.text("'auto'::character varying"),
            nullable=False,
        ),
        sa.Column("original_price_usd", sa.Numeric(24, 8), nullable=True),
        sa.Column("user_price_usd", sa.Numeric(24, 8), nullable=True),
        sa.Column(
            "price_overridden",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("pendle_position_type", sa.String(5), nullable=True),
        sa.Column("pendle_implied_apy_at_entry", sa.Numeric(6, 3), nullable=True),
        sa.Column("pendle_maturity_date", sa.Date(), nullable=True),
        sa.Column("pendle_accounting_asset", sa.String(50), nullable=True),
    )

    op.create_table(
        "position_snapshots",
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("value_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("cost_basis_fifo_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("cost_basis_wac_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("cumulative_yield_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("cumulative_borrow_cost_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("net_pnl_fifo_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("net_pnl_wac_usd", sa.Numeric(18, 2), nullable=True),
    )

    op.create_table(
        "api_usage_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 4), nullable=True),
    )

    # Convert time-series tables to hypertables (partition by timestamp)
    for table in HYPERTABLES:
        op.execute(
            f"SELECT create_hypertable("
            f"'{table}', 'timestamp', "
            f"chunk_time_interval => INTERVAL '1 day', "
            f"if_not_exists => TRUE);"
        )


def downgrade() -> None:
    # Drop tables in reverse FK order (DROP TABLE works on hypertables)
    op.drop_table("api_usage_log")
    op.drop_table("position_snapshots")
    op.drop_table("transaction_lots")
    op.drop_table("price_history")
    op.drop_table("risk_score_history")
    op.drop_table("vault_concentration")
    op.drop_table("vault_metrics")
    op.drop_table("cost_throttle_status")
    op.drop_table("cost_service_config")
    op.drop_table("recommendation_outcomes")
    op.drop_table("recommendations")
    op.drop_table("positions")
    op.drop_index("uq_wallets_user_address_chain", table_name="wallets")
    op.drop_table("wallets")
    op.drop_table("vaults")
    op.drop_table("profiles")

    op.execute("DROP EXTENSION IF EXISTS timescaledb CASCADE;")
