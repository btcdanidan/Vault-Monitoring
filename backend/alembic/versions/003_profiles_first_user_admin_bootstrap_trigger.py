"""profiles_first_user_admin_bootstrap_trigger

Revision ID: 003_bootstrap
Revises: 002_cagg
Create Date: 2025-03-01

Adds PostgreSQL trigger on profiles INSERT: when inserting the first row (count = 0),
sets approved=true, is_admin=true, approved_at=now() per §19.3.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_bootstrap"
down_revision: Union[str, None] = "002_cagg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Trigger function: before insert, if profiles table is empty (first user), bootstrap admin.
    op.execute("""
        CREATE OR REPLACE FUNCTION profiles_first_user_bootstrap_trigger()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF (SELECT COUNT(*) FROM profiles) = 0 THEN
                NEW.approved := true;
                NEW.is_admin := true;
                NEW.approved_at := now();
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER tr_profiles_first_user_bootstrap
        BEFORE INSERT ON profiles
        FOR EACH ROW
        EXECUTE PROCEDURE profiles_first_user_bootstrap_trigger();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tr_profiles_first_user_bootstrap ON profiles;")
    op.execute("DROP FUNCTION IF EXISTS profiles_first_user_bootstrap_trigger();")
