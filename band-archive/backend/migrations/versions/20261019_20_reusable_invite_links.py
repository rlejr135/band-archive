"""Add reusable band invitations and short-lived verified-email return contexts.

Revision ID: 20261019_20
Revises: 20261018_19
"""
from alembic import op
import sqlalchemy as sa

revision = '20261019_20'
down_revision = '20261018_19'
branch_labels = None
depends_on = None

PREVIOUS_TABLES = (
    'alembic_version', 'announcement', 'app_user', 'audit_event', 'auth_bootstrap_state', 'band',
    'band_availability_day', 'band_availability_state', 'band_creation_request', 'band_invite_code',
    'band_logo', 'band_membership', 'band_personal_schedule_note', 'band_request_email_outbox', 'comment',
    'direct_upload_session', 'gallery_image', 'legacy_band_claim_token', 'media', 'media_vote',
    'member', 'multipart_upload_part', 'multipart_upload_session', 'personal_log',
    'personal_log_multipart_upload_part', 'personal_log_multipart_upload_session',
    'policy_acceptance_receipt', 'r2_deletion_task', 'rehearsal', 'rehearsal_songs', 'song',
    'song_suggestion', 'song_suggestion_vote', 'song_vote', 'tenant_backfill_state',
)
PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')


def _assert_private(bind, tables):
    rows = bind.execute(sa.text("""
        SELECT c.relname, pg_get_userbyid(c.relowner), c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname
    """)).all()
    owner = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if tuple(row[0] for row in rows) != tuple(sorted(tables)):
        raise RuntimeError('Invite migration requires the exact reviewed table topology.')
    if any(row[1] != owner or not row[2] or row[3] for row in rows):
        raise RuntimeError('Invite migration requires owner-managed non-FORCE RLS tables.')
    roles = bind.execute(sa.text("SELECT oid,rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).all()
    role_names = [row.rolname for row in roles]
    for role in roles:
        for table in tables:
            for privilege in PRIVILEGES:
                if bind.execute(sa.text('SELECT has_table_privilege(:role,:table,:privilege)'), {
                    'role': role.rolname, 'table': 'public."' + table.replace('"', '""') + '"',
                    'privilege': privilege,
                }).scalar_one():
                    raise RuntimeError('A browser role retains application table privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname='public' AND c.relname=ANY(:tables)
          AND (CASE WHEN a.grantee=0 THEN false ELSE EXISTS (SELECT 1 FROM pg_roles client
               WHERE client.rolname=ANY(:roles) AND pg_has_role(client.oid,a.grantee,'MEMBER')) END OR a.grantee=0)
    """), {'tables': list(tables), 'roles': role_names}).scalar_one():
        raise RuntimeError('PUBLIC or an effective browser role retains application table privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(:tables)
    """), {'tables': list(tables)}).scalar_one():
        raise RuntimeError('Application tables must not have browser RLS policies.')
    sequences = bind.execute(sa.text("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='S'
    """)).scalars().all()
    for role in role_names:
        for sequence in sequences:
            for privilege in ('USAGE', 'SELECT', 'UPDATE'):
                if bind.execute(sa.text('SELECT has_sequence_privilege(:role,:sequence,:privilege)'), {
                    'role': role, 'sequence': 'public."' + sequence.replace('"', '""') + '"',
                    'privilege': privilege,
                }).scalar_one():
                    raise RuntimeError('A browser role retains application sequence privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('S',c.relowner))) a
        WHERE n.nspname='public' AND c.relkind='S'
          AND (CASE WHEN a.grantee=0 THEN false ELSE EXISTS (SELECT 1 FROM pg_roles client
               WHERE client.rolname=ANY(:roles) AND pg_has_role(client.oid,a.grantee,'MEMBER')) END OR a.grantee=0)
    """), {'roles': role_names}).scalar_one():
        raise RuntimeError('PUBLIC or an effective browser role retains application sequence privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE d.defaclrole=current_user::regrole AND d.defaclnamespace IN (0,'public'::regnamespace)
          AND d.defaclobjtype IN ('r','S')
          AND (CASE WHEN a.grantee=0 THEN false ELSE a.grantee IN
                    (SELECT oid FROM pg_roles WHERE rolname=ANY(:roles)) OR EXISTS
                    (SELECT 1 FROM pg_roles client WHERE client.rolname=ANY(:roles)
                     AND pg_has_role(client.oid,a.grantee,'MEMBER')) END OR a.grantee=0)
    """), {'roles': role_names}).scalar_one():
        raise RuntimeError('Unsafe default table/sequence privileges prevent invite migration.')


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Invite security migration requires a live PostgreSQL connection.')
        _assert_private(bind, PREVIOUS_TABLES)
        bind.exec_driver_sql('LOCK TABLE public.band IN ACCESS EXCLUSIVE MODE')
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Invite migration supports PostgreSQL and SQLite only.')
    op.create_table(
        'band_invite_link',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('band_id', sa.Integer(), sa.ForeignKey('band.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('app_user.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_by_user_id', sa.Integer(), sa.ForeignKey('app_user.id')),
        sa.CheckConstraint('expires_at > created_at', name='ck_band_invite_link_expiry'),
    )
    op.create_index('ix_band_invite_link_band_id', 'band_invite_link', ['band_id'])
    op.create_index('ix_band_invite_link_band_created', 'band_invite_link', ['band_id', 'created_at', 'id'])
    op.create_table(
        'invite_resume_context',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('invite_link_id', sa.Integer(), sa.ForeignKey('band_invite_link.id', ondelete='CASCADE'), nullable=False),
        sa.Column('context_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('expected_email_hmac', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claimed_user_id', sa.Integer(), sa.ForeignKey('app_user.id', ondelete='CASCADE')),
        sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('expires_at > created_at', name='ck_invite_resume_context_expiry'),
        sa.CheckConstraint('(claimed_user_id IS NULL) = (claimed_at IS NULL)',
                           name='ck_invite_resume_context_claim_state'),
    )
    op.create_index('ix_invite_resume_context_invite_link_id', 'invite_resume_context', ['invite_link_id'])
    op.create_index('ix_invite_resume_context_link_live', 'invite_resume_context',
                    ['invite_link_id', 'expires_at', 'completed_at'])
    if bind.dialect.name == 'postgresql':
        roles = bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars().all()
        sequences = bind.execute(sa.text("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='S'
              AND c.relname IN ('band_invite_link_id_seq','invite_resume_context_id_seq')
        """)).scalars().all()
        for sequence in sequences:
            bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON SEQUENCE public."{sequence}" FROM PUBLIC')
            for role in roles:
                bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON SEQUENCE public."{sequence}" FROM "{role}"')
        for table in ('band_invite_link', 'invite_resume_context'):
            bind.exec_driver_sql(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
            bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public."{table}" FROM PUBLIC')
            for role in roles:
                bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public."{table}" FROM "{role}"')
        _assert_private(bind, (*PREVIOUS_TABLES, 'band_invite_link', 'invite_resume_context'))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE public.band_invite_link, public.invite_resume_context IN ACCESS EXCLUSIVE MODE')
    if (bind.execute(sa.text('SELECT count(*) FROM invite_resume_context')).scalar_one()
            or bind.execute(sa.text('SELECT count(*) FROM band_invite_link')).scalar_one()):
        raise RuntimeError('Refusing to discard existing invite links or return contexts.')
    op.drop_table('invite_resume_context')
    op.drop_table('band_invite_link')
