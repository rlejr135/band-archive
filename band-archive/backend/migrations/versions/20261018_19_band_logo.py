"""Add one optional, same-band gallery logo selection.

Revision ID: 20261018_19
Revises: 20261017_18
"""
from alembic import op
import sqlalchemy as sa

revision = '20261018_19'
down_revision = '20261017_18'
branch_labels = None
depends_on = None

PREVIOUS_TABLES = (
    'alembic_version', 'announcement', 'app_user', 'audit_event', 'auth_bootstrap_state', 'band',
    'band_availability_day', 'band_availability_state', 'band_creation_request', 'band_invite_code',
    'band_membership', 'band_personal_schedule_note', 'band_request_email_outbox', 'comment',
    'direct_upload_session', 'gallery_image', 'legacy_band_claim_token', 'media', 'media_vote',
    'member', 'multipart_upload_part', 'multipart_upload_session', 'personal_log',
    'personal_log_multipart_upload_part', 'personal_log_multipart_upload_session',
    'policy_acceptance_receipt', 'r2_deletion_task', 'rehearsal', 'rehearsal_songs', 'song',
    'song_suggestion', 'song_suggestion_vote', 'song_vote', 'tenant_backfill_state',
)
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
        raise RuntimeError('Logo migration requires the exact reviewed table topology.')
    if any(row[1] != owner or not row[2] or row[3] for row in rows):
        raise RuntimeError('Logo migration requires owner-managed non-FORCE RLS tables.')
    roles = bind.execute(sa.text("SELECT oid,rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).all()
    role_names = [row.rolname for row in roles]
    for role in roles:
        for table in tables:
            for privilege in PRIVILEGES:
                if bind.execute(sa.text('SELECT has_table_privilege(:role,:table,:privilege)'), {
                    'role': role.rolname, 'table': 'public.' + _quote(table), 'privilege': privilege,
                }).scalar_one():
                    raise RuntimeError('A browser role retains application table privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname='public' AND c.relname=ANY(:tables)
          AND (a.grantee=0 OR CASE WHEN a.grantee=0 THEN false ELSE EXISTS (
              SELECT 1 FROM pg_roles client
              WHERE client.rolname=ANY(:roles)
                AND pg_has_role(client.oid,a.grantee,'MEMBER')
          ) END)
    """), {'tables': list(tables), 'roles': role_names}).scalar_one():
        raise RuntimeError('PUBLIC or an effective browser role retains application table privileges.')
    policies = bind.execute(sa.text("""
        SELECT count(*) FROM pg_policy p
        JOIN pg_class c ON c.oid=p.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(:tables)
    """), {'tables': list(tables)}).scalar_one()
    if policies:
        raise RuntimeError('Application tables must not have browser RLS policies.')
    sequences = bind.execute(sa.text("""
        SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='S' ORDER BY c.relname
    """)).all()
    for role in role_names:
        for sequence in sequences:
            for privilege in ('USAGE', 'SELECT', 'UPDATE'):
                if bind.execute(sa.text('SELECT has_sequence_privilege(:role,:sequence,:privilege)'), {
                    'role': role, 'sequence': 'public.' + _quote(sequence.relname),
                    'privilege': privilege,
                }).scalar_one():
                    raise RuntimeError('A browser role retains application sequence privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('S',c.relowner))) a
        WHERE n.nspname='public' AND c.relkind='S'
          AND (a.grantee=0 OR CASE WHEN a.grantee=0 THEN false ELSE EXISTS (
              SELECT 1 FROM pg_roles client
              WHERE client.rolname=ANY(:roles)
                AND pg_has_role(client.oid,a.grantee,'MEMBER')
          ) END)
    """), {'roles': role_names}).scalar_one():
        raise RuntimeError('PUBLIC or an effective browser role retains application sequence privileges.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE d.defaclnamespace IN (0,'public'::regnamespace)
          AND d.defaclobjtype IN ('r','S') AND (a.grantee=0 OR a.grantee IN (
              SELECT oid FROM pg_roles WHERE rolname=ANY(:roles)) OR CASE WHEN a.grantee=0 THEN false ELSE EXISTS (
                  SELECT 1 FROM pg_roles client
                  WHERE client.rolname=ANY(:roles)
                    AND pg_has_role(client.oid,a.grantee,'MEMBER')
              ) END)
    """), {'roles': role_names}).scalar_one():
        raise RuntimeError('Unsafe default table/sequence privileges prevent logo migration.')


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Logo security migration requires a live PostgreSQL connection.')
        _assert_private(bind, PREVIOUS_TABLES)
        bind.exec_driver_sql('LOCK TABLE public.gallery_image IN ACCESS EXCLUSIVE MODE')
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Logo migration supports PostgreSQL and SQLite only.')

    op.create_index('uq_gallery_image_band_id', 'gallery_image', ['band_id', 'id'], unique=True)
    op.create_table(
        'band_logo',
        sa.Column('band_id', sa.Integer(), sa.ForeignKey('band.id', ondelete='CASCADE'),
                  primary_key=True, autoincrement=False),
        sa.Column('gallery_image_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['band_id', 'gallery_image_id'], ['gallery_image.band_id', 'gallery_image.id'],
            ondelete='CASCADE', name='fk_band_logo_gallery_image_same_band',
        ),
    )
    if bind.dialect.name == 'postgresql':
        roles = bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars().all()
        bind.exec_driver_sql('ALTER TABLE public.band_logo ENABLE ROW LEVEL SECURITY')
        for role in ('PUBLIC', *roles):
            target = 'PUBLIC' if role == 'PUBLIC' else _quote(role)
            bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public.band_logo FROM {target}')
        _assert_private(bind, (*PREVIOUS_TABLES, 'band_logo'))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE public.band_logo, public.gallery_image IN ACCESS EXCLUSIVE MODE')
    if bind.execute(sa.text('SELECT count(*) FROM band_logo')).scalar_one():
        raise RuntimeError('Refusing to discard selected band logos.')
    op.drop_table('band_logo')
    op.drop_index('uq_gallery_image_band_id', table_name='gallery_image')
