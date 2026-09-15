"""Harden Phase 3 ownership, request, and audit invariants.

Revision ID: 20260916_04
Revises: 20260915_03
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = '20260916_04'
down_revision = '20260915_03'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'uq_band_membership_active_owner', 'band_membership', ['band_id'], unique=True,
        sqlite_where=sa.text("role = 'owner' AND status = 'active'"),
        postgresql_where=sa.text("role = 'owner' AND status = 'active'"),
    )
    op.create_index(
        'uq_band_creation_request_pending_user_slug', 'band_creation_request',
        ['requested_by_user_id', 'slug'], unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )
    _create_audit_immutability_triggers()


def _create_audit_immutability_triggers():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        op.execute("""
            CREATE TRIGGER audit_event_no_update
            BEFORE UPDATE ON audit_event
            BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER audit_event_no_delete
            BEFORE DELETE ON audit_event
            BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END
        """)
    elif bind.dialect.name == 'postgresql':
        op.execute("""
            CREATE FUNCTION audit_event_append_only() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'audit_event is append-only'; END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("CREATE TRIGGER audit_event_no_update BEFORE UPDATE ON audit_event "
                   "FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()")
        op.execute("CREATE TRIGGER audit_event_no_delete BEFORE DELETE ON audit_event "
                   "FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_delete')
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_update')
    elif bind.dialect.name == 'postgresql':
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_delete ON audit_event')
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event')
        op.execute('DROP FUNCTION IF EXISTS audit_event_append_only()')
    op.drop_index('uq_band_creation_request_pending_user_slug', table_name='band_creation_request')
    op.drop_index('uq_band_membership_active_owner', table_name='band_membership')
