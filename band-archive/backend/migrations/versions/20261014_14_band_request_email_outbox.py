"""Create the private band-request email outbox foundation.

Revision ID: 20261014_14
Revises: 20261010_13

The table stores only event identity and delivery state. It deliberately has
no recipient address, request snapshot, message body, or provider response.
PostgreSQL keeps the Flask-only Data API boundary: RLS is enabled without
FORCE, and browser/PUBLIC table and sequence privileges are revoked.
"""

from alembic import op
import sqlalchemy as sa


revision = '20261014_14'
down_revision = '20261010_13'
branch_labels = None
depends_on = None


TABLE = 'band_request_email_outbox'
PREVIOUS_TABLES = (
    'alembic_version', 'announcement', 'app_user', 'audit_event', 'auth_bootstrap_state', 'band',
    'band_creation_request', 'band_invite_code', 'band_membership', 'comment', 'direct_upload_session',
    'gallery_image', 'legacy_band_claim_token', 'media', 'media_vote', 'member', 'multipart_upload_part',
    'multipart_upload_session', 'personal_log', 'personal_log_multipart_upload_part',
    'personal_log_multipart_upload_session', 'rehearsal', 'rehearsal_songs', 'song', 'song_suggestion',
    'song_suggestion_vote', 'song_vote', 'tenant_backfill_state',
)
CLIENT_ROLES = ('anon', 'authenticated')
TABLE_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')
SEQUENCE_PRIVILEGES = ('USAGE', 'SELECT', 'UPDATE')


def _quote(identifier):
    return op.get_bind().dialect.identifier_preparer.quote(identifier)


def _assert_previous_head_is_hardened(bind):
    rows = bind.execute(sa.text("""
        SELECT cls.relname, pg_get_userbyid(cls.relowner) AS owner,
               cls.relrowsecurity, cls.relforcerowsecurity
        FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
        WHERE namespace.nspname = 'public' AND cls.relkind IN ('r', 'p')
        ORDER BY cls.relname
    """)).mappings().all()
    if tuple(row['relname'] for row in rows) != PREVIOUS_TABLES:
        raise RuntimeError('The recorded security-head table topology is not the reviewed revision-13 schema.')
    current_user = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if any(row['owner'] != current_user or not row['relrowsecurity'] or row['relforcerowsecurity'] for row in rows):
        raise RuntimeError('The recorded security-head ownership or RLS state is not safe to extend.')
    role_names = tuple(bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') ORDER BY rolname"
    )).scalars().all())
    for role in role_names:
        for table in PREVIOUS_TABLES:
            for privilege in TABLE_PRIVILEGES:
                if bind.execute(sa.text(
                    'SELECT has_table_privilege(:role, :table_name, :privilege)'
                ), {'role': role, 'table_name': 'public.' + _quote(table), 'privilege': privilege}).scalar_one():
                    raise RuntimeError('A browser role retains a privilege on the recorded security head.')
    defaults = bind.execute(sa.text("""
        SELECT count(*)
        FROM pg_default_acl AS defaults
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
        WHERE defaults.defaclrole = current_user::regrole
          AND defaults.defaclnamespace = 'public'::regnamespace
          AND defaults.defaclobjtype IN ('r', 'S')
          AND (acl.grantee = 0 OR acl.grantee IN (
              SELECT oid FROM pg_roles WHERE rolname = ANY(:roles)
          ))
    """), {'roles': list(CLIENT_ROLES)}).scalar_one()
    if defaults:
        raise RuntimeError('Unsafe browser-role default privileges prevent extending the security head.')
    global_defaults = bind.execute(sa.text("""
        SELECT count(*)
        FROM pg_default_acl AS defaults
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
        WHERE defaults.defaclrole = current_user::regrole
          AND defaults.defaclnamespace = 0
          AND defaults.defaclobjtype IN ('r', 'S')
          AND (acl.grantee = 0 OR acl.grantee IN (
              SELECT oid FROM pg_roles WHERE rolname = ANY(:roles)
          ))
    """), {'roles': list(CLIENT_ROLES)}).scalar_one()
    if global_defaults:
        raise RuntimeError('Unsafe global browser-role default privileges prevent extending the security head.')
    public_table_grants = bind.execute(sa.text("""
        SELECT count(*) FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid=cls.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl, acldefault('r', cls.relowner))) AS acl
        WHERE namespace.nspname='public' AND cls.relname=ANY(:tables) AND acl.grantee=0
    """), {'tables': list(PREVIOUS_TABLES)}).scalar_one()
    public_sequence_grants = bind.execute(sa.text("""
        SELECT count(*) FROM pg_class AS sequence_class
        JOIN pg_namespace AS sequence_namespace ON sequence_namespace.oid=sequence_class.relnamespace
        JOIN pg_depend AS dependency ON dependency.classid='pg_class'::regclass
          AND dependency.objid=sequence_class.oid AND dependency.refclassid='pg_class'::regclass
          AND dependency.deptype IN ('a','i')
        JOIN pg_class AS table_class ON table_class.oid=dependency.refobjid
        JOIN pg_namespace AS table_namespace ON table_namespace.oid=table_class.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(sequence_class.relacl,
                                               acldefault('S', sequence_class.relowner))) AS acl
        WHERE sequence_class.relkind='S' AND sequence_namespace.nspname='public'
          AND table_namespace.nspname='public' AND table_class.relname=ANY(:tables) AND acl.grantee=0
    """), {'tables': list(PREVIOUS_TABLES)}).scalar_one()
    if public_table_grants or public_sequence_grants:
        raise RuntimeError('PUBLIC retains a privilege on the recorded security head.')


def _revoke_new_table_privileges(bind):
    bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON TABLE public.{_quote(TABLE)} FROM PUBLIC'))
    for role in CLIENT_ROLES:
        exists = bind.execute(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).scalar_one_or_none()
        if exists:
            bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON TABLE public.{_quote(TABLE)} FROM {_quote(role)}'))
    sequences = bind.execute(sa.text("""
        SELECT sequence_namespace.nspname, sequence_class.relname
        FROM pg_class AS sequence_class
        JOIN pg_namespace AS sequence_namespace ON sequence_namespace.oid = sequence_class.relnamespace
        JOIN pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence_class.oid
         AND dependency.refclassid = 'pg_class'::regclass
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_class AS table_class ON table_class.oid = dependency.refobjid
        JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
        WHERE sequence_class.relkind = 'S' AND table_namespace.nspname = 'public'
          AND table_class.relname = :table_name
    """), {'table_name': TABLE}).all()
    for schema_name, sequence_name in sequences:
        target = _quote(schema_name) + '.' + _quote(sequence_name)
        bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON SEQUENCE {target} FROM PUBLIC'))
        for role in CLIENT_ROLES:
            exists = bind.execute(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).scalar_one_or_none()
            if exists:
                bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON SEQUENCE {target} FROM {_quote(role)}'))


def _assert_new_table_is_private(bind):
    table = bind.execute(sa.text("""
        SELECT pg_get_userbyid(cls.relowner), cls.relrowsecurity, cls.relforcerowsecurity,
               has_table_privilege(current_user, cls.oid, 'SELECT'),
               has_table_privilege(current_user, cls.oid, 'INSERT'),
               has_table_privilege(current_user, cls.oid, 'UPDATE'),
               has_table_privilege(current_user, cls.oid, 'DELETE')
        FROM pg_class AS cls JOIN pg_namespace AS namespace ON namespace.oid=cls.relnamespace
        WHERE namespace.nspname='public' AND cls.relname=:table_name AND cls.relkind IN ('r','p')
    """), {'table_name': TABLE}).one()
    current_user = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if table[0] != current_user or not table[1] or table[2] or not all(table[3:]):
        raise RuntimeError('The new outbox table does not satisfy the private-owner RLS contract.')
    role_names = bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
    )).scalars().all()
    for role in role_names:
        for privilege in TABLE_PRIVILEGES:
            if bind.execute(sa.text(
                'SELECT has_table_privilege(:role, :table_name, :privilege)'
            ), {'role': role, 'table_name': 'public.' + _quote(TABLE), 'privilege': privilege}).scalar_one():
                raise RuntimeError('A browser role retains an outbox table privilege.')
    if bind.execute(sa.text("""
        SELECT count(*) FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid=cls.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl, acldefault('r', cls.relowner))) AS acl
        WHERE namespace.nspname='public' AND cls.relname=:table_name AND acl.grantee=0
    """), {'table_name': TABLE}).scalar_one():
        raise RuntimeError('PUBLIC retains an outbox table privilege.')
    sequences = bind.execute(sa.text("""
        SELECT sequence_namespace.nspname, sequence_class.relname,
               pg_get_userbyid(sequence_class.relowner), sequence_class.oid
        FROM pg_class AS sequence_class
        JOIN pg_namespace AS sequence_namespace ON sequence_namespace.oid=sequence_class.relnamespace
        JOIN pg_depend AS dependency ON dependency.classid='pg_class'::regclass
          AND dependency.objid=sequence_class.oid AND dependency.refclassid='pg_class'::regclass
          AND dependency.deptype IN ('a','i')
        JOIN pg_class AS table_class ON table_class.oid=dependency.refobjid
        JOIN pg_namespace AS table_namespace ON table_namespace.oid=table_class.relnamespace
        WHERE sequence_class.relkind='S' AND sequence_namespace.nspname='public'
          AND table_namespace.nspname='public' AND table_class.relname=:table_name
    """), {'table_name': TABLE}).all()
    current_user = bind.execute(sa.text('SELECT current_user')).scalar_one()
    if not sequences:
        raise RuntimeError('The outbox identity sequence is missing.')
    for schema_name, sequence_name, owner, sequence_oid in sequences:
        if owner != current_user:
            raise RuntimeError('The outbox identity sequence is not owned by the migration owner.')
        for privilege in SEQUENCE_PRIVILEGES:
            if not bind.execute(sa.text(
                'SELECT has_sequence_privilege(current_user, :sequence_oid, :privilege)'
            ), {'sequence_oid': sequence_oid, 'privilege': privilege}).scalar_one():
                raise RuntimeError('The migration owner lacks an outbox sequence privilege.')
            for role in role_names:
                if bind.execute(sa.text(
                    'SELECT has_sequence_privilege(:role, :sequence_oid, :privilege)'
                ), {'role': role, 'sequence_oid': sequence_oid, 'privilege': privilege}).scalar_one():
                    raise RuntimeError('A browser role retains an outbox sequence privilege.')
        if bind.execute(sa.text("""
            SELECT count(*) FROM pg_class AS cls
            CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl, acldefault('S', cls.relowner))) AS acl
            WHERE cls.oid=:sequence_oid AND acl.grantee=0
        """), {'sequence_oid': sequence_oid}).scalar_one():
            raise RuntimeError('PUBLIC retains an outbox sequence privilege.')


def upgrade():
    context = op.get_context()
    if context.dialect.name == 'postgresql':
        if context.as_sql:
            raise RuntimeError('The private outbox migration requires a live PostgreSQL connection.')
        _assert_previous_head_is_hardened(op.get_bind())
    elif context.dialect.name != 'sqlite':
        raise RuntimeError('The private outbox migration supports only PostgreSQL and SQLite.')

    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=True),
        sa.Column('recipient_user_id', sa.Integer(), nullable=True),
        sa.Column('decision', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('manual_retry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_token', sa.String(length=64), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error_code', sa.String(length=50), server_default='none', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name='ck_band_request_email_decision'),
        sa.CheckConstraint("status IN ('pending', 'leased', 'sent', 'failed', 'suppressed')", name='ck_band_request_email_status'),
        sa.CheckConstraint('attempt_count > -1', name='ck_band_request_email_attempt_count'),
        sa.CheckConstraint('manual_retry_count > -1', name='ck_band_request_email_manual_retry_count'),
        sa.CheckConstraint(
            "last_error_code IN ('none', 'smtp_unavailable', 'smtp_timeout', 'smtp_rejected', "
            "'recipient_unavailable', 'invalid_configuration', 'unknown')",
            name='ck_band_request_email_error_code',
        ),
        sa.ForeignKeyConstraint(['request_id'], ['band_creation_request.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['recipient_user_id'], ['app_user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_id', name='uq_band_request_email_request'),
    )
    op.create_index('ix_band_request_email_due', TABLE, ['status', 'next_attempt_at', 'id'], unique=False)
    op.create_index('ix_band_request_email_lease', TABLE, ['status', 'lease_until', 'id'], unique=False)

    if context.dialect.name == 'postgresql':
        bind = op.get_bind()
        bind.execute(sa.text(f'ALTER TABLE public.{_quote(TABLE)} ENABLE ROW LEVEL SECURITY'))
        _revoke_new_table_privileges(bind)
        # New serial sequences inherit the same schema default-ACL hardening.
        for role in CLIENT_ROLES:
            if bind.execute(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}).scalar_one_or_none():
                quoted = _quote(role)
                bind.execute(sa.text(
                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM {quoted}'
                ))
                bind.execute(sa.text(
                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM {quoted}'
                ))
        bind.execute(sa.text(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC'
        ))
        bind.execute(sa.text(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC'
        ))
        _assert_new_table_is_private(bind)


def downgrade():
    bind = op.get_bind()
    if 'band_request_email_outbox' in sa.inspect(bind).get_table_names():
        if bind.dialect.name == 'postgresql':
            # Serialize the emptiness check with inserts for the entire
            # migration transaction, otherwise a new event could race the
            # check and be silently dropped with the table.
            bind.exec_driver_sql('LOCK TABLE public.band_request_email_outbox IN ACCESS EXCLUSIVE MODE')
        elif bind.dialect.name == 'sqlite':
            # A no-op write takes SQLite's writer reservation in the current
            # Alembic transaction without issuing a nested BEGIN IMMEDIATE.
            bind.exec_driver_sql('UPDATE band_request_email_outbox SET id = id WHERE 0')
        if bind.execute(sa.text(f'SELECT count(*) FROM {TABLE}')).scalar_one():
            raise RuntimeError(
                'Refusing to drop a populated email outbox; preserve decision-notification event history.'
            )
        op.drop_index('ix_band_request_email_lease', table_name=TABLE)
        op.drop_index('ix_band_request_email_due', table_name=TABLE)
        op.drop_table(TABLE)
