"""add_admin_rls_profile_policy

Revision ID: 005_admin_rls
Revises: 004_rls
Create Date: 2025-03-01

Adds an admin-access RLS policy on the profiles table so admin users can
SELECT/UPDATE/DELETE all profile rows (required for /api/admin/accounts).
Without this, FORCE ROW LEVEL SECURITY + the user_isolation policy restricts
admins to seeing only their own row.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_admin_rls"
down_revision: Union[str, None] = "004_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Allow admin users full access to all profiles rows.
    # The existing profiles_user_isolation policy continues to restrict
    # non-admin users to their own row.
    op.execute(
        """
        CREATE POLICY profiles_admin_full_access ON profiles
        USING (
            EXISTS (
                SELECT 1 FROM profiles p
                WHERE p.id = current_setting('app.current_user_id', true)::uuid
                AND p.is_admin = true
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS profiles_admin_full_access ON profiles")
