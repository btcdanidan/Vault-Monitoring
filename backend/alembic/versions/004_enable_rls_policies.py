"""enable_rls_policies

Revision ID: 004_rls
Revises: 003_bootstrap
Create Date: 2025-03-01

Enables PostgreSQL RLS on all user-owned, shared, and admin tables per §19.5.
Creates celery_worker role with BYPASSRLS for workers.
"""
import os
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_rls"
down_revision: Union[str, None] = "003_bootstrap"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USER_OWNED_TABLES = [
    "profiles",
    "wallets",
    "positions",
    "transaction_lots",
    "position_snapshots",
    "recommendations",
    "recommendation_outcomes",
]

SHARED_TABLES = [
    "vaults",
    "vault_metrics",
    "vault_concentration",
    "risk_score_history",
    "price_history",
]

ADMIN_TABLES = [
    "api_usage_log",
    "cost_service_config",
    "cost_throttle_status",
]


def upgrade() -> None:
    # Password from env; escape single quotes for SQL.
    celery_password = os.environ.get("CELERY_DB_PASSWORD", "celery_worker_password")
    celery_password_escaped = celery_password.replace("'", "''")

    # Create celery_worker role with BYPASSRLS (idempotent).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'celery_worker') THEN
                CREATE ROLE celery_worker WITH LOGIN BYPASSRLS PASSWORD '{celery_password_escaped}';
            END IF;
        END $$;
        """
    )

    # Grant connect and usage. current_database() used via DO block.
    op.execute(
        """
        DO $$
        DECLARE
            db name;
        BEGIN
            SELECT current_database() INTO db;
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO celery_worker', db);
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO celery_worker")
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO celery_worker")
    op.execute("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO celery_worker")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO celery_worker"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO celery_worker"
    )

    # ----- User-owned tables: RLS + user isolation policy + FORCE -----
    op.execute(
        "ALTER TABLE profiles ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY profiles_user_isolation ON profiles
        USING (current_setting('app.current_user_id', true)::uuid = id)
        """
    )
    op.execute("ALTER TABLE profiles FORCE ROW LEVEL SECURITY")

    for table in USER_OWNED_TABLES:
        if table == "profiles":
            continue
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_user_isolation ON {table}
            USING (current_setting('app.current_user_id', true)::uuid = user_id)
            """
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # ----- Shared tables: RLS + authenticated read only + FORCE -----
    for table in SHARED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_authenticated_read ON {table}
            FOR SELECT
            USING (current_setting('app.current_user_id', true) IS NOT NULL)
            """
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # ----- Admin tables: RLS + admin-only policy + FORCE -----
    for table in ADMIN_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_admin_only ON {table}
            USING (
                EXISTS (
                    SELECT 1 FROM profiles
                    WHERE id = current_setting('app.current_user_id', true)::uuid
                    AND is_admin = true
                )
            )
            """
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Drop policies and disable RLS (order: drop policy then disable).
    for table in ADMIN_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_admin_only ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in SHARED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_authenticated_read ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS profiles_user_isolation ON profiles")
    op.execute("ALTER TABLE profiles DISABLE ROW LEVEL SECURITY")

    for table in USER_OWNED_TABLES:
        if table == "profiles":
            continue
        op.execute(f"DROP POLICY IF EXISTS {table}_user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Revoke and drop role.
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM celery_worker")
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM celery_worker"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM celery_worker")
    op.execute(
        """
        DO $$
        DECLARE
            db name;
        BEGIN
            SELECT current_database() INTO db;
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM celery_worker', db);
        END $$;
        """
    )
    op.execute("DROP ROLE IF EXISTS celery_worker")
