"""Case-based content access restrictions; no content or object modification.

Revision ID: 20261021_22
Revises: 20261020_21
"""
import importlib.util
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = '20261021_22'
down_revision = '20261020_21'
branch_labels = None
depends_on = None


def _security():
    # Reuse the immutable reviewed ACL contract, retaining its exact SQL.
    path = Path(__file__).with_name('20261019_20_reusable_invite_links.py')
    spec = importlib.util.spec_from_file_location('restriction_security_revision20', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade():
    bind = op.get_bind()
    security = _security()
    previous = (*security.PREVIOUS_TABLES, 'band_invite_link', 'invite_resume_context')
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Restriction migration requires a live PostgreSQL connection.')
        security._assert_private(bind, previous)
        bind.exec_driver_sql('LOCK TABLE public.audit_event IN SHARE MODE')
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Restrictions support PostgreSQL and SQLite only.')
    op.create_table(
        'content_restriction',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('band_id', sa.Integer(), sa.ForeignKey('band.id'), nullable=False),
        sa.Column('resource_type', sa.String(20), nullable=False),
        sa.Column('resource_id', sa.Integer(), nullable=False),
        sa.Column('case_ref', sa.String(36), nullable=False),
        sa.Column('reason_code', sa.String(20), nullable=False),
        sa.Column('state', sa.String(10), nullable=False, server_default='active'),
        sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('app_user.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('released_by_user_id', sa.Integer(), sa.ForeignKey('app_user.id', ondelete='SET NULL')),
        sa.Column('released_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('band_id', 'resource_type', 'resource_id', 'case_ref',
                            name='uq_content_restriction_case_target'),
        sa.CheckConstraint('resource_id > 0', name='ck_content_restriction_resource_id'),
        sa.CheckConstraint("resource_type IN ('song','rehearsal','member','media','personal_log',"
                           "'gallery','announcement','suggestion','comment')",
                           name='ck_content_restriction_resource_type'),
        sa.CheckConstraint("reason_code IN ('copyright','privacy','portrait','other_rights')",
                           name='ck_content_restriction_reason'),
        sa.CheckConstraint("state IN ('active','released')", name='ck_content_restriction_state'),
        sa.CheckConstraint("(state = 'active' AND released_at IS NULL AND released_by_user_id IS NULL) OR "
                           "(state = 'released' AND released_at IS NOT NULL)",
                           name='ck_content_restriction_release'),
    )
    op.create_index('ix_content_restriction_active_target', 'content_restriction',
                    ['band_id', 'resource_type', 'resource_id'],
                    sqlite_where=sa.text("state = 'active'"), postgresql_where=sa.text("state = 'active'"))
    op.create_index('ix_audit_event_upload_policy_target', 'audit_event', ['target_type', 'target_id'],
                    sqlite_where=sa.text("event_type = 'upload_policy_confirmed'"),
                    postgresql_where=sa.text("event_type = 'upload_policy_confirmed'"))
    if bind.dialect.name == 'postgresql':
        roles = bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars().all()
        bind.exec_driver_sql('ALTER TABLE public.content_restriction ENABLE ROW LEVEL SECURITY')
        for role in ('PUBLIC', *roles):
            target = 'PUBLIC' if role == 'PUBLIC' else '"' + role + '"'
            bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public.content_restriction FROM {target}')
            bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON SEQUENCE public.content_restriction_id_seq FROM {target}')
        security._assert_private(bind, (*previous, 'content_restriction'))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE public.content_restriction IN ACCESS EXCLUSIVE MODE')
    if bind.execute(sa.text('SELECT count(*) FROM content_restriction')).scalar_one():
        raise RuntimeError('Refusing to discard rights-request restriction records.')
    op.drop_index('ix_audit_event_upload_policy_target', table_name='audit_event')
    op.drop_table('content_restriction')
