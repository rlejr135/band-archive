"""Add the private, durable per-resource R2 deletion queue.

Revision ID: 20261016_15
Revises: 20261014_14
"""

from alembic import op
import sqlalchemy as sa


revision = '20261016_15'
down_revision = '20261014_14'
branch_labels = None
depends_on = None

TABLE = 'r2_deletion_task'
PREVIOUS_TABLES = (
    'alembic_version', 'announcement', 'app_user', 'audit_event', 'auth_bootstrap_state', 'band',
    'band_creation_request', 'band_invite_code', 'band_membership', 'band_request_email_outbox',
    'comment', 'direct_upload_session', 'gallery_image', 'legacy_band_claim_token', 'media',
    'media_vote', 'member', 'multipart_upload_part', 'multipart_upload_session', 'personal_log',
    'personal_log_multipart_upload_part', 'personal_log_multipart_upload_session', 'rehearsal',
    'rehearsal_songs', 'song', 'song_suggestion', 'song_suggestion_vote', 'song_vote',
    'tenant_backfill_state',
)
CLIENT_ROLES = ('anon', 'authenticated')
TABLE_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')


def _quote(identifier):
    return op.get_bind().dialect.identifier_preparer.quote(identifier)


def _assert_previous_head_is_hardened(bind):
    rows = bind.execute(sa.text("""
        SELECT cls.relname, pg_get_userbyid(cls.relowner) AS owner,
               cls.relrowsecurity, cls.relforcerowsecurity
        FROM pg_class AS cls JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        WHERE ns.nspname='public' AND cls.relkind IN ('r','p') ORDER BY cls.relname
    """)).mappings().all()
    if tuple(row['relname'] for row in rows) != PREVIOUS_TABLES:
        raise RuntimeError('Recorded revision-14 table topology is not the reviewed schema.')
    current_user = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if any(row['owner'] != current_user or not row['relrowsecurity'] or row['relforcerowsecurity'] for row in rows):
        raise RuntimeError('Recorded revision-14 ownership or RLS state is unsafe.')
    roles = bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') ORDER BY rolname"
    )).scalars().all()
    for role in roles:
        for table in PREVIOUS_TABLES:
            for privilege in TABLE_PRIVILEGES:
                if bind.execute(sa.text(
                    'SELECT has_table_privilege(:role,:table_name,:privilege)'
                ), {'role': role, 'table_name': 'public.' + _quote(table), 'privilege': privilege}).scalar_one():
                    raise RuntimeError('A browser role retains a revision-14 table privilege.')
    public_acl = bind.execute(sa.text("""
        SELECT count(*) FROM pg_class AS cls JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl,acldefault('r',cls.relowner))) AS acl
        WHERE ns.nspname='public' AND cls.relname=ANY(:tables) AND acl.grantee=0
    """), {'tables': list(PREVIOUS_TABLES)}).scalar_one()
    if public_acl:
        raise RuntimeError('PUBLIC retains a revision-14 table privilege.')
    defaults = bind.execute(sa.text("""
        SELECT count(*) FROM pg_default_acl AS d
        CROSS JOIN LATERAL aclexplode(d.defaclacl) AS acl
        WHERE d.defaclrole=current_user::regrole AND d.defaclnamespace IN (0,'public'::regnamespace)
          AND d.defaclobjtype IN ('r','S') AND (acl.grantee=0 OR acl.grantee IN (
            SELECT oid FROM pg_roles WHERE rolname=ANY(:roles)))
    """), {'roles': list(CLIENT_ROLES)}).scalar_one()
    if defaults:
        raise RuntimeError('Unsafe browser/PUBLIC default privileges prevent extending revision 14.')


def _assert_new_table_private(bind):
    row = bind.execute(sa.text("""
        SELECT pg_get_userbyid(cls.relowner), cls.relrowsecurity, cls.relforcerowsecurity,
               has_table_privilege(current_user,cls.oid,'SELECT'),
               has_table_privilege(current_user,cls.oid,'INSERT'),
               has_table_privilege(current_user,cls.oid,'UPDATE'),
               has_table_privilege(current_user,cls.oid,'DELETE')
        FROM pg_class AS cls JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        WHERE ns.nspname='public' AND cls.relname=:table AND cls.relkind IN ('r','p')
    """), {'table': TABLE}).one()
    current_user = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if row[0] != current_user or not row[1] or row[2] or not all(row[3:]):
        raise RuntimeError('Deletion task table owner/RLS contract failed.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_policy AS policy
        JOIN pg_class AS cls ON cls.oid=policy.polrelid
        JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        WHERE ns.nspname='public' AND cls.relname=:table
    """), {'table': TABLE}).scalar_one():
        raise RuntimeError('Deletion task table must not have browser-visible row policies.')
    roles = bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')"
    )).scalars().all()
    for role in roles:
        for privilege in TABLE_PRIVILEGES:
            if bind.execute(sa.text(
                'SELECT has_table_privilege(:role,:table_name,:privilege)'
            ), {'role': role, 'table_name': 'public.' + _quote(TABLE), 'privilege': privilege}).scalar_one():
                raise RuntimeError('Browser role retains deletion-task table privilege.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class AS cls JOIN pg_namespace AS ns ON ns.oid=cls.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl,acldefault('r',cls.relowner))) AS acl
        WHERE ns.nspname='public' AND cls.relname=:table AND acl.grantee=0
    """), {'table': TABLE}).scalar_one():
        raise RuntimeError('PUBLIC retains deletion-task table privilege.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class AS sequence JOIN pg_depend AS dependency
          ON dependency.classid='pg_class'::regclass AND dependency.objid=sequence.oid
         AND dependency.refclassid='pg_class'::regclass AND dependency.deptype IN ('a','i')
        JOIN pg_class AS managed ON managed.oid=dependency.refobjid
        WHERE sequence.relnamespace='public'::regnamespace AND sequence.relkind='S'
          AND managed.relnamespace='public'::regnamespace AND managed.relname=:table
    """), {'table': TABLE}).scalar_one():
        # This migration deliberately uses a UUID string key and must add no
        # sequence (including a standalone/default sequence).
        raise RuntimeError('Unexpected standalone public sequence appeared.')


def upgrade():
    context = op.get_context()
    if context.dialect.name == 'postgresql':
        if context.as_sql:
            raise RuntimeError('Deletion task security migration requires a live PostgreSQL connection.')
        _assert_previous_head_is_hardened(op.get_bind())
    elif context.dialect.name != 'sqlite':
        raise RuntimeError('Deletion task migration supports only PostgreSQL and SQLite.')

    op.create_table(
        TABLE,
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('band_id', sa.Integer(), nullable=False),
        sa.Column('resource_type', sa.String(length=32), nullable=False),
        sa.Column('resource_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='queued', nullable=False),
        sa.Column('object_keys', sa.JSON(none_as_null=True), nullable=True),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_token', sa.String(length=64), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(length=40), server_default='none', nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("resource_type IN ('media','personal_log','gallery')",
                           name='ck_r2_deletion_task_resource_type'),
        sa.CheckConstraint("status IN ('queued','processing','retry_wait','blocked','completed')",
                           name='ck_r2_deletion_task_status'),
        sa.CheckConstraint('attempt_count >= 0', name='ck_r2_deletion_task_attempt_count'),
        sa.CheckConstraint('resource_id > 0', name='ck_r2_deletion_task_resource_id'),
        sa.CheckConstraint("error_code IN ('none','storage_unavailable','key_conflict','invalid_key','unknown')",
                           name='ck_r2_deletion_task_error_code'),
        sa.CheckConstraint("(status='completed' AND object_keys IS NULL) OR "
                           "(status!='completed' AND object_keys IS NOT NULL)",
                           name='ck_r2_deletion_task_manifest_state'),
        sa.CheckConstraint("(status='processing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
                           "(status!='processing' AND lease_token IS NULL AND lease_expires_at IS NULL)",
                           name='ck_r2_deletion_task_lease_state'),
        sa.ForeignKeyConstraint(['band_id'], ['band.id']),
        sa.ForeignKeyConstraint(['actor_user_id'], ['app_user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('band_id','resource_type','resource_id',name='uq_r2_deletion_task_target'),
    )
    op.create_index('ix_r2_deletion_task_band_id', TABLE, ['band_id'])
    op.create_index('ix_r2_deletion_task_due', TABLE, ['status', 'available_at', 'created_at'])
    op.create_index('ix_r2_deletion_task_lease', TABLE, ['status', 'lease_expires_at'])

    if context.dialect.name == 'postgresql':
        bind = op.get_bind()
        bind.execute(sa.text(f'ALTER TABLE public.{_quote(TABLE)} ENABLE ROW LEVEL SECURITY'))
        bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON TABLE public.{_quote(TABLE)} FROM PUBLIC'))
        for role in CLIENT_ROLES:
            if bind.execute(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).scalar_one_or_none():
                bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON TABLE public.{_quote(TABLE)} FROM {_quote(role)}'))
        bind.execute(sa.text(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC'
        ))
        bind.execute(sa.text(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC'
        ))
        for role in CLIENT_ROLES:
            if bind.execute(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).scalar_one_or_none():
                quoted = _quote(role)
                bind.execute(sa.text(
                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM {quoted}'
                ))
                bind.execute(sa.text(
                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM {quoted}'
                ))
        _assert_new_table_private(bind)


def downgrade():
    bind = op.get_bind()
    if TABLE in sa.inspect(bind).get_table_names():
        if bind.dialect.name == 'postgresql':
            bind.exec_driver_sql(f'LOCK TABLE public.{_quote(TABLE)} IN ACCESS EXCLUSIVE MODE')
        elif bind.dialect.name == 'sqlite':
            bind.exec_driver_sql(f'UPDATE {TABLE} SET id=id WHERE 0')
        if bind.execute(sa.text(f'SELECT count(*) FROM {TABLE}')).scalar_one():
            raise RuntimeError('Refusing to drop deletion task or completion audit history.')
        op.drop_index('ix_r2_deletion_task_lease', table_name=TABLE)
        op.drop_index('ix_r2_deletion_task_due', table_name=TABLE)
        op.drop_index('ix_r2_deletion_task_band_id', table_name=TABLE)
        op.drop_table(TABLE)
