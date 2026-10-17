"""Add join introductions and private policy acceptance receipts.

Revision ID: 20261017_16
Revises: 20261016_15
"""

from alembic import op
import sqlalchemy as sa


revision = '20261017_16'
down_revision = '20261016_15'
branch_labels = None
depends_on = None

TABLE = 'policy_acceptance_receipt'
PREVIOUS_TABLES = (
    'alembic_version', 'announcement', 'app_user', 'audit_event', 'auth_bootstrap_state', 'band',
    'band_creation_request', 'band_invite_code', 'band_membership', 'band_request_email_outbox',
    'comment', 'direct_upload_session', 'gallery_image', 'legacy_band_claim_token', 'media',
    'media_vote', 'member', 'multipart_upload_part', 'multipart_upload_session', 'personal_log',
    'personal_log_multipart_upload_part', 'personal_log_multipart_upload_session', 'r2_deletion_task',
    'rehearsal', 'rehearsal_songs', 'song', 'song_suggestion', 'song_suggestion_vote', 'song_vote',
    'tenant_backfill_state',
)
CLIENT_ROLES = ('anon', 'authenticated')
TABLE_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')


def _quote(identifier):
    return op.get_bind().dialect.identifier_preparer.quote(identifier)


def _assert_previous_head_is_hardened(bind):
    rows = bind.execute(sa.text("""
        SELECT cls.relname, pg_get_userbyid(cls.relowner), cls.relrowsecurity, cls.relforcerowsecurity
        FROM pg_class AS cls JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        WHERE ns.nspname='public' AND cls.relkind IN ('r','p') ORDER BY cls.relname
    """)).all()
    if tuple(row[0] for row in rows) != PREVIOUS_TABLES:
        raise RuntimeError('Recorded revision-15 table topology is not the reviewed schema.')
    current_user = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if any(row[1] != current_user or not row[2] or row[3] for row in rows):
        raise RuntimeError('Recorded revision-15 ownership or RLS state is unsafe.')
    roles = bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') ORDER BY rolname"
    )).scalars().all()
    for role in roles:
        for table in PREVIOUS_TABLES:
            for privilege in TABLE_PRIVILEGES:
                if bind.execute(sa.text('SELECT has_table_privilege(:role,:table,:privilege)'), {
                    'role': role, 'table': 'public.' + _quote(table), 'privilege': privilege,
                }).scalar_one():
                    raise RuntimeError('A browser role retains a revision-15 table privilege.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class AS cls JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl,acldefault('r',cls.relowner))) AS acl
        WHERE ns.nspname='public' AND cls.relname=ANY(:tables) AND acl.grantee=0
    """), {'tables': list(PREVIOUS_TABLES)}).scalar_one():
        raise RuntimeError('PUBLIC retains a revision-15 table privilege.')
    unsafe_defaults = bind.execute(sa.text("""
        SELECT count(*) FROM pg_default_acl AS d
        CROSS JOIN LATERAL aclexplode(d.defaclacl) AS acl
        WHERE d.defaclrole=current_user::regrole AND d.defaclnamespace IN (0,'public'::regnamespace)
          AND d.defaclobjtype='r' AND (acl.grantee=0 OR acl.grantee IN (
            SELECT oid FROM pg_roles WHERE rolname=ANY(:roles)))
    """), {'roles': list(CLIENT_ROLES)}).scalar_one()
    if unsafe_defaults:
        raise RuntimeError('Unsafe browser/PUBLIC default table privileges prevent revision 16.')


def _assert_receipt_private(bind):
    row = bind.execute(sa.text("""
        SELECT pg_get_userbyid(cls.relowner), cls.relrowsecurity, cls.relforcerowsecurity,
               has_table_privilege(current_user,cls.oid,'SELECT'),
               has_table_privilege(current_user,cls.oid,'INSERT'),
               has_table_privilege(current_user,cls.oid,'UPDATE'),
               has_table_privilege(current_user,cls.oid,'DELETE')
        FROM pg_class AS cls JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        WHERE ns.nspname='public' AND cls.relname=:table AND cls.relkind IN ('r','p')
    """), {'table': TABLE}).one()
    owner = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if row[0] != owner or not row[1] or row[2] or not all(row[3:]):
        raise RuntimeError('Policy receipt ownership/RLS contract failed.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=:table
    """), {'table': TABLE}).scalar_one():
        raise RuntimeError('Policy receipt table must have no browser-visible row policies.')
    roles = bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')"
    )).scalars().all()
    for role in roles:
        for privilege in TABLE_PRIVILEGES:
            if bind.execute(sa.text('SELECT has_table_privilege(:role,:table,:privilege)'), {
                'role': role, 'table': 'public.' + _quote(TABLE), 'privilege': privilege,
            }).scalar_one():
                raise RuntimeError('A browser role retains policy receipt table privilege.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl
        WHERE n.nspname='public' AND c.relname=:table AND acl.grantee=0
    """), {'table': TABLE}).scalar_one():
        raise RuntimeError('PUBLIC retains policy receipt table privilege.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class s JOIN pg_depend d ON d.objid=s.oid
        JOIN pg_class t ON t.oid=d.refobjid JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE s.relkind='S' AND t.relname=:table AND n.nspname='public'
    """), {'table': TABLE}).scalar_one():
        raise RuntimeError('Policy receipt table must not introduce a sequence.')


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Policy acceptance security migration requires a live PostgreSQL connection.')
        _assert_previous_head_is_hardened(bind)
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Policy acceptance migration supports only PostgreSQL and SQLite.')

    op.add_column('band_membership', sa.Column('join_introduction', sa.String(length=300), nullable=True))
    op.create_table(
        TABLE,
        sa.Column('user_id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('terms_version', sa.String(length=100), nullable=False),
        sa.Column('privacy_version', sa.String(length=100), nullable=False),
        sa.Column('terms_accepted', sa.Boolean(), nullable=False),
        sa.Column('age_14_plus', sa.Boolean(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("terms_version <> ''", name='ck_policy_receipt_terms_version'),
        sa.CheckConstraint("privacy_version <> ''", name='ck_policy_receipt_privacy_version'),
        sa.CheckConstraint('terms_accepted IS TRUE', name='ck_policy_receipt_terms_accepted'),
        sa.CheckConstraint('age_14_plus IS TRUE', name='ck_policy_receipt_age_assertion'),
        sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql(f'ALTER TABLE public.{_quote(TABLE)} ENABLE ROW LEVEL SECURITY')
        bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public.{_quote(TABLE)} FROM PUBLIC')
        for role in CLIENT_ROLES:
            if bind.execute(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).scalar_one_or_none():
                bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public.{_quote(TABLE)} FROM {_quote(role)}')
        bind.exec_driver_sql('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC')
        for role in CLIENT_ROLES:
            if bind.execute(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).scalar_one_or_none():
                bind.exec_driver_sql(
                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM {_quote(role)}'
                )
        _assert_receipt_private(bind)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql(f'LOCK TABLE public.{_quote(TABLE)}, public.band_membership IN ACCESS EXCLUSIVE MODE')
    elif bind.dialect.name == 'sqlite':
        bind.exec_driver_sql(f'UPDATE {TABLE} SET user_id=user_id WHERE 0')
    if bind.execute(sa.text(f'SELECT count(*) FROM {TABLE}')).scalar_one():
        raise RuntimeError('Refusing to drop policy acceptance receipts.')
    if bind.execute(sa.text('SELECT count(*) FROM band_membership WHERE join_introduction IS NOT NULL')).scalar_one():
        raise RuntimeError('Refusing to drop stored join introductions.')
    op.drop_table(TABLE)
    with op.batch_alter_table('band_membership') as batch:
        batch.drop_column('join_introduction')
