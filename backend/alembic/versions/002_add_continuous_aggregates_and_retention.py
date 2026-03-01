"""add_continuous_aggregates_and_retention

Revision ID: 002_cagg
Revises: 001_s10
Create Date: 2025-03-01

Adds TimescaleDB continuous aggregates for position_snapshots (hourly, 4h, daily)
with refresh policies, and retention policies per §14.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_cagg"
down_revision: Union[str, None] = "001_s10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- Continuous aggregates on position_snapshots (§14) -----
    # Hourly: 1W chart resolution
    op.execute("""
        CREATE MATERIALIZED VIEW position_snapshots_hourly
        WITH (timescaledb.continuous) AS
        SELECT
            position_id,
            user_id,
            time_bucket('1 hour', "timestamp") AS bucket,
            last(value_usd, "timestamp") AS value_usd,
            last(cost_basis_fifo_usd, "timestamp") AS cost_basis_fifo_usd,
            last(cost_basis_wac_usd, "timestamp") AS cost_basis_wac_usd,
            last(cumulative_yield_usd, "timestamp") AS cumulative_yield_usd,
            last(cumulative_borrow_cost_usd, "timestamp") AS cumulative_borrow_cost_usd,
            last(net_pnl_fifo_usd, "timestamp") AS net_pnl_fifo_usd,
            last(net_pnl_wac_usd, "timestamp") AS net_pnl_wac_usd
        FROM position_snapshots
        GROUP BY position_id, user_id, bucket
        WITH NO DATA;
    """)

    # 4-hour: 1M chart resolution
    op.execute("""
        CREATE MATERIALIZED VIEW position_snapshots_4h
        WITH (timescaledb.continuous) AS
        SELECT
            position_id,
            user_id,
            time_bucket('4 hours', "timestamp") AS bucket,
            last(value_usd, "timestamp") AS value_usd,
            last(cost_basis_fifo_usd, "timestamp") AS cost_basis_fifo_usd,
            last(cost_basis_wac_usd, "timestamp") AS cost_basis_wac_usd,
            last(cumulative_yield_usd, "timestamp") AS cumulative_yield_usd,
            last(cumulative_borrow_cost_usd, "timestamp") AS cumulative_borrow_cost_usd,
            last(net_pnl_fifo_usd, "timestamp") AS net_pnl_fifo_usd,
            last(net_pnl_wac_usd, "timestamp") AS net_pnl_wac_usd
        FROM position_snapshots
        GROUP BY position_id, user_id, bucket
        WITH NO DATA;
    """)

    # Daily: 3M / 1Y / All Time chart resolution
    op.execute("""
        CREATE MATERIALIZED VIEW position_snapshots_daily
        WITH (timescaledb.continuous) AS
        SELECT
            position_id,
            user_id,
            time_bucket('1 day', "timestamp") AS bucket,
            last(value_usd, "timestamp") AS value_usd,
            last(cost_basis_fifo_usd, "timestamp") AS cost_basis_fifo_usd,
            last(cost_basis_wac_usd, "timestamp") AS cost_basis_wac_usd,
            last(cumulative_yield_usd, "timestamp") AS cumulative_yield_usd,
            last(cumulative_borrow_cost_usd, "timestamp") AS cumulative_borrow_cost_usd,
            last(net_pnl_fifo_usd, "timestamp") AS net_pnl_fifo_usd,
            last(net_pnl_wac_usd, "timestamp") AS net_pnl_wac_usd
        FROM position_snapshots
        GROUP BY position_id, user_id, bucket
        WITH NO DATA;
    """)

    # ----- Refresh policies (§14) -----
    # Hourly: refresh every 30 min, cover last 2h
    op.execute("""
        SELECT add_continuous_aggregate_policy('position_snapshots_hourly',
            start_offset => INTERVAL '2 hours',
            end_offset => NULL,
            schedule_interval => INTERVAL '30 minutes');
    """)
    # 4-hour: refresh every 1h, cover last 8h
    op.execute("""
        SELECT add_continuous_aggregate_policy('position_snapshots_4h',
            start_offset => INTERVAL '8 hours',
            end_offset => NULL,
            schedule_interval => INTERVAL '1 hour');
    """)
    # Daily: refresh every 4h, cover last 2d
    op.execute("""
        SELECT add_continuous_aggregate_policy('position_snapshots_daily',
            start_offset => INTERVAL '2 days',
            end_offset => NULL,
            schedule_interval => INTERVAL '4 hours');
    """)

    # ----- Retention policies (§14) -----
    # Raw 15-min snapshots: 90 days
    op.execute("""
        SELECT add_retention_policy('position_snapshots', INTERVAL '90 days');
    """)
    # Hourly aggregates: 1 year
    op.execute("""
        SELECT add_retention_policy('position_snapshots_hourly', INTERVAL '365 days');
    """)
    # Daily aggregates: indefinite (no policy)


def downgrade() -> None:
    # Remove retention policies first
    op.execute("SELECT remove_retention_policy('position_snapshots', if_exists => TRUE);")
    op.execute(
        "SELECT remove_retention_policy('position_snapshots_hourly', if_exists => TRUE);"
    )

    # Remove continuous aggregate refresh policies
    op.execute(
        "SELECT remove_continuous_aggregate_policy('position_snapshots_hourly', if_exists => TRUE);"
    )
    op.execute(
        "SELECT remove_continuous_aggregate_policy('position_snapshots_4h', if_exists => TRUE);"
    )
    op.execute(
        "SELECT remove_continuous_aggregate_policy('position_snapshots_daily', if_exists => TRUE);"
    )

    # Drop materialized views (continuous aggregates)
    op.execute("DROP MATERIALIZED VIEW IF EXISTS position_snapshots_daily;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS position_snapshots_4h;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS position_snapshots_hourly;")
