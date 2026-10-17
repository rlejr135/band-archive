"""Add private, band-scoped rehearsal availability.

Revision ID: 20261017_17
Revises: 20261017_16
"""
from alembic import op
import sqlalchemy as sa

revision = '20261017_17'
down_revision = '20261017_16'
branch_labels = None
depends_on = None

TABLES = ('band_availability_day', 'band_availability_state', 'band_personal_schedule_note')
PREVIOUS_TABLES = (
    'alembic_version', 'announcement', 'app_user', 'audit_event', 'auth_bootstrap_state', 'band',
    'band_creation_request', 'band_invite_code', 'band_membership', 'band_request_email_outbox',
    'comment', 'direct_upload_session', 'gallery_image', 'legacy_band_claim_token', 'media',
    'media_vote', 'member', 'multipart_upload_part', 'multipart_upload_session', 'personal_log',
    'personal_log_multipart_upload_part', 'personal_log_multipart_upload_session', 'policy_acceptance_receipt',
    'r2_deletion_task', 'rehearsal', 'rehearsal_songs', 'song', 'song_suggestion', 'song_suggestion_vote',
    'song_vote', 'tenant_backfill_state',
)
CLIENT_ROLES = ('anon', 'authenticated')
PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')


def _quote(value):
    return op.get_bind().dialect.identifier_preparer.quote(value)


def _assert_private(bind, tables):
    rows = bind.execute(sa.text("""
        SELECT c.relname, pg_get_userbyid(c.relowner), c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname
    """)).all()
    owner = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if tuple(row[0] for row in rows) != tuple(sorted(tables)):
        raise RuntimeError('Availability migration requires the exact reviewed table topology.')
    if any(row[1] != owner or not row[2] or row[3] for row in rows):
        raise RuntimeError('Availability migration requires owner-managed non-FORCE RLS tables.')
    roles = bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars().all()
    for role in roles:
        for table in tables:
            for privilege in PRIVILEGES:
                if bind.execute(sa.text('SELECT has_table_privilege(:role,:table,:privilege)'), {
                    'role': role, 'table': 'public.' + _quote(table), 'privilege': privilege,
                }).scalar_one():
                    raise RuntimeError('A browser role retains application table privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname='public' AND c.relname=ANY(:tables) AND a.grantee=0
    """), {'tables': list(tables)}).scalar_one():
        raise RuntimeError('PUBLIC retains application table privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE d.defaclrole=current_user::regrole AND d.defaclnamespace IN (0,'public'::regnamespace)
        AND d.defaclobjtype IN ('r','S') AND (a.grantee=0 OR a.grantee IN (
            SELECT oid FROM pg_roles WHERE rolname=ANY(:roles)))
    """), {'roles': list(CLIENT_ROLES)}).scalar_one():
        raise RuntimeError('Unsafe default table/sequence privileges prevent availability migration.')


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Availability security migration requires a live PostgreSQL connection.')
        _assert_private(bind, PREVIOUS_TABLES)
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Availability migration supports only PostgreSQL and SQLite.')
    op.create_table('band_availability_state',
        sa.Column('band_id', sa.Integer(), sa.ForeignKey('band.id', ondelete='CASCADE'), primary_key=True, autoincrement=False),
        sa.Column('generation', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_reset_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_reset_by_membership_id', sa.Integer(), sa.ForeignKey('band_membership.id'), nullable=True),
        sa.CheckConstraint('generation >= 0', name='ck_availability_state_generation'))
    op.create_table('band_availability_day',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('band_id', sa.Integer(), sa.ForeignKey('band.id', ondelete='CASCADE'), nullable=False),
        sa.Column('membership_id', sa.Integer(), sa.ForeignKey('band_membership.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        *(sa.Column(p, sa.String(20), nullable=False, server_default='UNKNOWN') for p in ('morning', 'afternoon', 'evening')),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.UniqueConstraint('band_id', 'membership_id', 'date', name='uq_availability_day_member_date'),
        sa.CheckConstraint('revision > 0', name='ck_availability_day_revision'),
        *(sa.CheckConstraint(f"{p} IN ('AVAILABLE', 'UNAVAILABLE', 'UNKNOWN')", name=f'ck_availability_day_{p}')
          for p in ('morning', 'afternoon', 'evening')))
    op.create_index('ix_availability_day_band_date_member', 'band_availability_day', ['band_id', 'date', 'membership_id'])
    op.create_table('band_personal_schedule_note',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('band_id', sa.Integer(), sa.ForeignKey('band.id', ondelete='CASCADE'), nullable=False),
        sa.Column('membership_id', sa.Integer(), sa.ForeignKey('band_membership.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('period', sa.String(20), nullable=False),
        sa.Column('body', sa.String(200), nullable=True),
        sa.Column('visibility', sa.String(20), nullable=False, server_default='PRIVATE'),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.UniqueConstraint('band_id', 'membership_id', 'date', 'period', name='uq_schedule_note_member_date_period'),
        sa.CheckConstraint('revision > 0', name='ck_schedule_note_revision'),
        sa.CheckConstraint("period IN ('morning', 'afternoon', 'evening')", name='ck_schedule_note_period'),
        sa.CheckConstraint("visibility IN ('PRIVATE', 'BAND')", name='ck_schedule_note_visibility'))
    op.create_index('ix_schedule_note_band_date_member', 'band_personal_schedule_note', ['band_id', 'date', 'membership_id'])
    if bind.dialect.name == 'postgresql':
        roles = bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars().all()
        for table in TABLES:
            bind.exec_driver_sql(f'ALTER TABLE public.{_quote(table)} ENABLE ROW LEVEL SECURITY')
            for role in ('PUBLIC', *roles):
                target = 'PUBLIC' if role == 'PUBLIC' else _quote(role)
                bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public.{_quote(table)} FROM {target}')
        # Generated row IDs are backend-only too, even under provider defaults.
        for table in ('band_availability_day', 'band_personal_schedule_note'):
            sequence = bind.execute(sa.text('SELECT pg_get_serial_sequence(:table,\'id\')'), {'table': 'public.' + table}).scalar_one()
            if sequence:
                for role in ('PUBLIC', *roles):
                    target = 'PUBLIC' if role == 'PUBLIC' else _quote(role)
                    bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON SEQUENCE {sequence} FROM {target}')
        _assert_private(bind, (*PREVIOUS_TABLES, *TABLES))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE ' + ', '.join('public.' + _quote(t) for t in TABLES) + ' IN ACCESS EXCLUSIVE MODE')
    if any(bind.execute(sa.text(f'SELECT count(*) FROM {_quote(table)}')).scalar_one() for table in TABLES):
        raise RuntimeError('Refusing to drop registered availability, reset metadata or personal notes.')
    for table in ('band_personal_schedule_note', 'band_availability_day', 'band_availability_state'):
        op.drop_table(table)
