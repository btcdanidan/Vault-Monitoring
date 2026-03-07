"""fix_schema_add_indexes_risk_tables

Revision ID: 006_schema_fix
Revises: 005_admin_rls
Create Date: 2026-03-07

Fixes audit findings Phase 1:
- Change transaction_lots.position_id FK from CASCADE to SET NULL (lot immutability)
- Change position_snapshots.position_id FK from CASCADE to SET NULL
- Add missing indexes on positions, transaction_lots, wallets, vaults
- Add unique constraint on position group key
- Create risk engine static tables (chain_risk_scores, protocol_risk_factors)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006_schema_fix"
down_revision: Union[str, None] = "005_admin_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Fix CASCADE DELETE on transaction_lots.position_id → SET NULL
    #    Critical Invariant #2: lots are immutable, must never cascade-delete.
    #    NOTE: TimescaleDB hypertables silently drop FKs, so this FK may not
    #    exist at runtime. We handle both cases gracefully.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            -- Drop existing FK if present (hypertables may have silently dropped it)
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'transaction_lots_position_id_fkey'
                  AND table_name = 'transaction_lots'
            ) THEN
                ALTER TABLE transaction_lots
                    DROP CONSTRAINT transaction_lots_position_id_fkey;
            END IF;
        END $$;
    """)

    # Re-add as a CHECK-style reference (not enforced FK on hypertable,
    # but documents the intent). For application-level enforcement we rely
    # on the service layer.
    # On regular tables this would be:
    #   ALTER TABLE transaction_lots ADD CONSTRAINT ... REFERENCES positions(id) ON DELETE SET NULL;
    # But on hypertables FK constraints are not supported, so we skip.

    # 2. Same for position_snapshots (also a hypertable)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'position_snapshots_position_id_fkey'
                  AND table_name = 'position_snapshots'
            ) THEN
                ALTER TABLE position_snapshots
                    DROP CONSTRAINT position_snapshots_position_id_fkey;
            END IF;
        END $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Add missing indexes
    # -------------------------------------------------------------------------

    # positions indexes
    op.create_index(
        "ix_positions_user_id_wallet_id",
        "positions",
        ["user_id", "wallet_id"],
    )
    op.create_index(
        "ix_positions_user_id_status",
        "positions",
        ["user_id", "status"],
    )

    # transaction_lots indexes (hypertable — indexes on hypertables are fine)
    op.create_index(
        "ix_transaction_lots_user_id_wallet_address",
        "transaction_lots",
        ["user_id", "wallet_address"],
    )
    op.create_index(
        "ix_transaction_lots_tx_hash",
        "transaction_lots",
        ["tx_hash"],
    )
    op.create_index(
        "ix_transaction_lots_position_id",
        "transaction_lots",
        ["position_id"],
    )

    # vaults indexes
    op.create_index(
        "ix_vaults_protocol_chain",
        "vaults",
        ["protocol", "chain"],
    )

    # wallets indexes
    op.create_index(
        "ix_wallets_user_id_is_active",
        "wallets",
        ["user_id", "is_active"],
    )

    # -------------------------------------------------------------------------
    # 4. Add unique constraint on position group key
    #    Prevents duplicate positions for same (user, wallet, chain, protocol,
    #    vault_or_market_id, position_type).
    # -------------------------------------------------------------------------
    op.create_index(
        "uq_positions_group_key",
        "positions",
        ["user_id", "wallet_id", "chain", "protocol", "vault_or_market_id", "position_type"],
        unique=True,
    )

    # -------------------------------------------------------------------------
    # 5. Create risk engine static tables (§6)
    # -------------------------------------------------------------------------
    op.create_table(
        "chain_risk_scores",
        sa.Column("chain", sa.String(20), primary_key=True),
        sa.Column(
            "score",
            sa.Numeric(5, 1),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "protocol_risk_factors",
        sa.Column("protocol", sa.String(30), primary_key=True),
        sa.Column(
            "audit_quality",
            sa.Numeric(5, 1),
            nullable=False,
            comment="Weight: 30%",
        ),
        sa.Column(
            "contract_maturity",
            sa.Numeric(5, 1),
            nullable=False,
            comment="Weight: 25%",
        ),
        sa.Column(
            "exploit_history",
            sa.Numeric(5, 1),
            nullable=False,
            comment="Weight: 20%",
        ),
        sa.Column(
            "admin_key_risk",
            sa.Numeric(5, 1),
            nullable=False,
            comment="Weight: 15%",
        ),
        sa.Column(
            "bug_bounty",
            sa.Numeric(5, 1),
            nullable=False,
            comment="Weight: 10%",
        ),
        sa.Column(
            "composite_score",
            sa.Numeric(5, 1),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # -------------------------------------------------------------------------
    # 6. Add lot immutability trigger — prevent UPDATE on amount column
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_lot_amount_update()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.amount IS DISTINCT FROM NEW.amount THEN
                RAISE EXCEPTION 'transaction_lots.amount is immutable (Critical Invariant #2)';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_lot_immutable_amount
        BEFORE UPDATE ON transaction_lots
        FOR EACH ROW
        EXECUTE FUNCTION prevent_lot_amount_update();
    """)


def downgrade() -> None:
    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS trg_lot_immutable_amount ON transaction_lots;")
    op.execute("DROP FUNCTION IF EXISTS prevent_lot_amount_update();")

    # Drop risk tables
    op.drop_table("protocol_risk_factors")
    op.drop_table("chain_risk_scores")

    # Drop unique constraint on positions
    op.drop_index("uq_positions_group_key", table_name="positions")

    # Drop indexes
    op.drop_index("ix_wallets_user_id_is_active", table_name="wallets")
    op.drop_index("ix_vaults_protocol_chain", table_name="vaults")
    op.drop_index("ix_transaction_lots_position_id", table_name="transaction_lots")
    op.drop_index("ix_transaction_lots_tx_hash", table_name="transaction_lots")
    op.drop_index("ix_transaction_lots_user_id_wallet_address", table_name="transaction_lots")
    op.drop_index("ix_positions_user_id_status", table_name="positions")
    op.drop_index("ix_positions_user_id_wallet_id", table_name="positions")
