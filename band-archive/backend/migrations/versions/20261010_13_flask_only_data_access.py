"""Prevent browser roles from accessing the application data plane directly.

Revision ID: 20261010_13
Revises: 20261006_12

This is deliberately a one-way security migration.  Flask uses the database
owner connection; browser-facing Supabase roles must have no application-table
or sequence privileges and no RLS policies.
"""

from alembic import op
import sqlalchemy as sa


revision = '20261010_13'
down_revision = '20261006_12'
branch_labels = None
depends_on = None


# This is a closed list, not ``ALL TABLES IN SCHEMA public``.  A subsequently
# introduced public table must be deliberately added and reviewed rather than
# inheriting this migration's authority.
MANAGED_TABLES = (
    'alembic_version',
    'announcement',
    'app_user',
    'audit_event',
    'auth_bootstrap_state',
    'band',
    'band_creation_request',
    'band_invite_code',
    'band_membership',
    'comment',
    'direct_upload_session',
    'gallery_image',
    'legacy_band_claim_token',
    'media',
    'media_vote',
    'member',
    'multipart_upload_part',
    'multipart_upload_session',
    'personal_log',
    'personal_log_multipart_upload_part',
    'personal_log_multipart_upload_session',
    'rehearsal',
    'rehearsal_songs',
    'song',
    'song_suggestion',
    'song_suggestion_vote',
    'song_vote',
    'tenant_backfill_state',
)
CLIENT_ROLES = ('anon', 'authenticated')
TABLE_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN')
SEQUENCE_PRIVILEGES = ('USAGE', 'SELECT', 'UPDATE')


def _quote(identifier):
    return op.get_bind().dialect.identifier_preparer.quote(identifier)


def _qualified_table(name):
    return 'public.' + _quote(name)


def _client_roles(bind):
    rows = bind.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
    )).scalars().all()
    return tuple(role for role in CLIENT_ROLES if role in rows)


def _managed_table_rows(bind):
    rows = bind.execute(sa.text("""
        SELECT cls.relname, pg_get_userbyid(cls.relowner) AS owner,
               has_table_privilege(current_user, cls.oid, 'SELECT') AS can_select,
               has_table_privilege(current_user, cls.oid, 'INSERT') AS can_insert,
               has_table_privilege(current_user, cls.oid, 'UPDATE') AS can_update,
               has_table_privilege(current_user, cls.oid, 'DELETE') AS can_delete
        FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
        WHERE namespace.nspname = 'public' AND cls.relkind IN ('r', 'p')
        ORDER BY cls.relname
    """)).mappings().all()
    names = tuple(row['relname'] for row in rows)
    if names != MANAGED_TABLES:
        raise RuntimeError('Public table allowlist does not match the reviewed application schema.')
    current_user = bind.execute(sa.text('SELECT current_user')).scalar_one()
    for row in rows:
        if row['owner'] != current_user or not all(row[key] for key in (
            'can_select', 'can_insert', 'can_update', 'can_delete',
        )):
            raise RuntimeError('Migration role must own and retain CRUD access to every managed table.')
    return rows


def _assert_client_roles_are_not_privileged(bind, table_rows, client_roles):
    """Reject role inheritance that would bypass a direct ACL revoke."""
    if not client_roles:
        return
    privileged_roles = {row['owner'] for row in table_rows}
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'service_role'")).scalar_one_or_none():
        privileged_roles.add('service_role')
    rows = bind.execute(sa.text("""
        WITH RECURSIVE inherited(member_oid, role_oid) AS (
            SELECT member, roleid FROM pg_auth_members
            UNION
            SELECT inherited.member_oid, membership.roleid
            FROM inherited
            JOIN pg_auth_members AS membership ON membership.member = inherited.role_oid
        )
        SELECT member_role.rolname AS member_name, inherited_role.rolname AS inherited_name
        FROM inherited
        JOIN pg_roles AS member_role ON member_role.oid = inherited.member_oid
        JOIN pg_roles AS inherited_role ON inherited_role.oid = inherited.role_oid
        WHERE member_role.rolname = ANY(:client_roles)
          AND inherited_role.rolname = ANY(:privileged_roles)
    """), {
        'client_roles': list(client_roles),
        'privileged_roles': sorted(privileged_roles),
    }).all()
    if rows:
        raise RuntimeError('Browser roles inherit a privileged database role.')


def _managed_sequences(bind):
    # Only sequences owned by one of the closed-list tables are touched.  This
    # covers SERIAL and identity columns without naming-convention assumptions.
    rows = bind.execute(sa.text("""
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
        WHERE sequence_class.relkind = 'S'
          AND table_namespace.nspname = 'public'
          AND table_class.relname = ANY(:table_names)
        ORDER BY sequence_namespace.nspname, sequence_class.relname
    """), {'table_names': list(MANAGED_TABLES)}).all()
    return tuple(rows)


def _revoke_table_clients(bind, table_name, client_roles):
    target = _qualified_table(table_name)
    bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON TABLE {target} FROM PUBLIC'))
    for role in client_roles:
        bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON TABLE {target} FROM {_quote(role)}'))


def _revoke_sequence_clients(bind, schema_name, sequence_name, client_roles):
    target = _quote(schema_name) + '.' + _quote(sequence_name)
    bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON SEQUENCE {target} FROM PUBLIC'))
    for role in client_roles:
        bind.execute(sa.text(f'REVOKE ALL PRIVILEGES ON SEQUENCE {target} FROM {_quote(role)}'))


def _revoke_default_client_privileges(bind, client_roles):
    statements = (
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC',
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC',
    )
    for statement in statements:
        bind.execute(sa.text(statement))
    for role in client_roles:
        quoted = _quote(role)
        bind.execute(sa.text(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM {quoted}'
        ))
        bind.execute(sa.text(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM {quoted}'
        ))


def _assert_no_global_default_client_privileges(bind, client_roles):
    """Schema-local REVOKE cannot override an unsafe owner-global default."""
    count = bind.execute(sa.text("""
        SELECT count(*)
        FROM pg_default_acl AS defaults
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
        WHERE defaults.defaclrole = current_user::regrole
          AND defaults.defaclnamespace = 0
          AND defaults.defaclobjtype IN ('r', 'S')
          AND (
              acl.grantee = 0
              OR acl.grantee IN (SELECT oid FROM pg_roles WHERE rolname = ANY(:client_roles))
          )
    """), {'client_roles': list(client_roles)}).scalar_one()
    if count:
        raise RuntimeError(
            'Migration owner has unsafe global default privileges; remediate them separately before Data API hardening.'
        )


def _assert_postcondition(bind, client_roles):
    states = bind.execute(sa.text("""
        SELECT cls.relname, cls.relrowsecurity, cls.relforcerowsecurity
        FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
        WHERE namespace.nspname = 'public' AND cls.relkind IN ('r', 'p')
        ORDER BY cls.relname
    """)).all()
    if (tuple(name for name, _, _ in states) != MANAGED_TABLES
            or not all(enabled and not forced for _, enabled, forced in states)):
        raise RuntimeError('Managed tables are not all protected by row-level security.')
    policy_count = bind.execute(sa.text("""
        SELECT count(*)
        FROM pg_policies
        WHERE schemaname = 'public' AND tablename = ANY(:table_names)
    """), {'table_names': list(MANAGED_TABLES)}).scalar_one()
    if policy_count:
        raise RuntimeError('Managed tables must not have browser-role RLS policies.')
    for role in client_roles:
        for table in MANAGED_TABLES:
            for privilege in TABLE_PRIVILEGES:
                allowed = bind.execute(sa.text(
                    'SELECT has_table_privilege(:role, :table_name, :privilege)'
                ), {'role': role, 'table_name': _qualified_table(table), 'privilege': privilege}).scalar_one()
                if allowed:
                    raise RuntimeError('Browser role retains a managed-table privilege.')
    for schema_name, sequence_name in _managed_sequences(bind):
        target = _quote(schema_name) + '.' + _quote(sequence_name)
        for role in client_roles:
            for privilege in SEQUENCE_PRIVILEGES:
                allowed = bind.execute(sa.text(
                    'SELECT has_sequence_privilege(:role, :sequence_name, :privilege)'
                ), {'role': role, 'sequence_name': target, 'privilege': privilege}).scalar_one()
                if allowed:
                    raise RuntimeError('Browser role retains a managed-sequence privilege.')
    public_grants = bind.execute(sa.text("""
        SELECT count(*)
        FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl, acldefault('r', cls.relowner))) AS acl
        WHERE namespace.nspname = 'public' AND cls.relname = ANY(:table_names) AND acl.grantee = 0
    """), {'table_names': list(MANAGED_TABLES)}).scalar_one()
    if public_grants:
        raise RuntimeError('PUBLIC retains a managed-table privilege.')
    default_client_grants = bind.execute(sa.text("""
        SELECT count(*)
        FROM pg_default_acl AS defaults
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
        WHERE defaults.defaclrole = current_user::regrole
          AND defaults.defaclnamespace = 'public'::regnamespace
          AND defaults.defaclobjtype IN ('r', 'S')
          AND (
              acl.grantee = 0
              OR acl.grantee IN (SELECT oid FROM pg_roles WHERE rolname = ANY(:client_roles))
          )
    """), {'client_roles': list(client_roles)}).scalar_one()
    if default_client_grants:
        raise RuntimeError('Migration owner retains unsafe public-schema default privileges.')


def upgrade():
    context = op.get_context()
    if context.dialect.name == 'sqlite':
        # SQLite has no PostgreSQL role/RLS equivalent.  Recording this
        # revision is intentional so local structural fixtures stay current.
        return
    if context.dialect.name != 'postgresql' or context.as_sql:
        raise RuntimeError('The Data API security migration requires a live PostgreSQL connection.')

    bind = op.get_bind()
    table_rows = _managed_table_rows(bind)
    client_roles = _client_roles(bind)
    _assert_no_global_default_client_privileges(bind, client_roles)
    _assert_client_roles_are_not_privileged(bind, table_rows, client_roles)
    sequences = _managed_sequences(bind)
    for table_name in MANAGED_TABLES:
        bind.execute(sa.text(f'ALTER TABLE {_qualified_table(table_name)} ENABLE ROW LEVEL SECURITY'))
        _revoke_table_clients(bind, table_name, client_roles)
    for schema_name, sequence_name in sequences:
        _revoke_sequence_clients(bind, schema_name, sequence_name, client_roles)
    _revoke_default_client_privileges(bind, client_roles)
    _assert_postcondition(bind, client_roles)


def downgrade():
    if op.get_context().dialect.name == 'sqlite':
        return
    raise RuntimeError(
        'Data API hardening is one-way: a manual, reviewed security rollback is required; '
        'automatic downgrade will not restore browser access.'
    )
