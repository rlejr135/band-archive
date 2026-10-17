"""Read-only PostgreSQL startup gates; migrations remain operator-only."""
from sqlalchemy import inspect, text

from migration_bootstrap import DATA_API_MANAGED_TABLES, migration_head_revision, schema_contract_differences


def postgres_automation_allowed(app, engine, option):
    """Only a validated PostgreSQL factory and explicit boolean opt-in may run."""
    if engine.dialect.name != 'postgresql':
        return True
    return (app.config.get('POSTGRES_STARTUP_VALIDATED') is True
            and app.config.get(option) is True)


def assert_postgres_startup_ready(engine):
    """Validate one read-only snapshot; requires the factory's app context.

    The context resolves the head through the bundled Flask-Migrate settings.

    Reflection checks columns, defaults, keys, checks, indexes and audit
    triggers. Its head-security checks cover RLS/policies, effective browser
    table/sequence ACLs, PUBLIC tables and owner global/schema default ACLs.
    Additional checks below require owner access, PUBLIC sequence isolation,
    and absence of privileged browser roles or callable SECURITY DEFINER RPC.
    Never stamp, migrate, create, repair or perform an owner CRUD probe here.
    """
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            connection.exec_driver_sql('SET LOCAL search_path = public, pg_catalog')
            tables = set(inspect(connection).get_table_names(schema='public'))
            if tables != set(DATA_API_MANAGED_TABLES):
                raise RuntimeError('unsupported PostgreSQL table topology')
            extra_relations = connection.execute(text("""
                SELECT count(*) FROM pg_class
                WHERE relnamespace='public'::regnamespace AND relkind IN ('v','m','f')
            """)).scalar_one()
            unowned_sequences = connection.execute(text("""
                SELECT count(*) FROM pg_class AS sequence
                WHERE sequence.relnamespace='public'::regnamespace AND sequence.relkind='S'
                  AND NOT EXISTS (
                    SELECT 1 FROM pg_depend AS dependency JOIN pg_class AS managed
                      ON managed.oid=dependency.refobjid
                    WHERE dependency.classid='pg_class'::regclass AND dependency.objid=sequence.oid
                      AND dependency.refclassid='pg_class'::regclass AND dependency.deptype IN ('a','i')
                      AND managed.relnamespace='public'::regnamespace AND managed.relkind IN ('r','p')
                      AND managed.relname=ANY(:table_names))
            """), {'table_names': list(DATA_API_MANAGED_TABLES)}).scalar_one()
            if extra_relations or unowned_sequences:
                raise RuntimeError('unreviewed public relation or standalone sequence')
            if connection.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != [migration_head_revision()]:
                raise RuntimeError('PostgreSQL revision is not the exact head')
            if schema_contract_differences(connection):
                raise RuntimeError('PostgreSQL schema or security contract differs')
            marker = connection.execute(text(
                'SELECT key, status FROM public.tenant_backfill_state'
            )).all()
            if marker != [('phase4_tenant_scope', 'validated')]:
                raise RuntimeError('PostgreSQL tenant backfill is not uniquely validated')
            # The new tables deliberately do not alter the historical
            # membership table to add a composite candidate key. Verify the
            # redundant tenant key without disclosing note bodies.
            inconsistent_calendar = connection.execute(text('''
                SELECT (SELECT count(*) FROM public.band_availability_day d
                    JOIN public.band_membership m ON m.id=d.membership_id WHERE m.band_id<>d.band_id)
                  + (SELECT count(*) FROM public.band_personal_schedule_note n
                    JOIN public.band_membership m ON m.id=n.membership_id WHERE m.band_id<>n.band_id)
                  + (SELECT count(*) FROM public.band_availability_state s
                    JOIN public.band_membership m ON m.id=s.last_reset_by_membership_id WHERE m.band_id<>s.band_id)
            ''')).scalar_one()
            if inconsistent_calendar:
                raise RuntimeError('PostgreSQL availability tenant relationship is invalid')
            ownership = connection.execute(text("""
                SELECT count(*) FROM pg_class AS cls
                WHERE cls.relnamespace='public'::regnamespace AND cls.relkind IN ('r','p')
                  AND cls.relowner <> current_user::regrole
            """)).scalar_one()
            # Check every CRUD privilege independently (comma lists mean ANY).
            crud = connection.execute(text("""
                SELECT count(*) FROM pg_class AS cls
                CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) AS privilege
                WHERE cls.relnamespace='public'::regnamespace AND cls.relkind IN ('r','p')
                  AND has_table_privilege(current_user,cls.oid,privilege)
            """)).scalar_one()
            public_sequences = connection.execute(text("""
                SELECT count(*) FROM pg_class AS cls
                CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl,acldefault('S',cls.relowner))) AS acl
                WHERE cls.relnamespace='public'::regnamespace AND cls.relkind='S' AND acl.grantee=0
            """)).scalar_one()
            privileged_clients = connection.execute(text("""
                WITH RECURSIVE inherited(member_oid,role_oid) AS (
                    SELECT member,roleid FROM pg_auth_members UNION
                    SELECT inherited.member_oid,membership.roleid FROM inherited
                    JOIN pg_auth_members AS membership ON membership.member=inherited.role_oid
                )
                SELECT (SELECT count(*) FROM pg_roles
                    WHERE rolname IN ('anon','authenticated') AND (rolsuper OR rolbypassrls))
                    + (SELECT count(*) FROM inherited
                    JOIN pg_roles AS member ON member.oid=inherited.member_oid
                    JOIN pg_roles AS parent ON parent.oid=inherited.role_oid
                    WHERE member.rolname IN ('anon','authenticated')
                      AND (parent.oid=current_user::regrole OR parent.rolname='service_role'
                           OR parent.rolsuper OR parent.rolbypassrls))
            """)).scalar_one()
            rpc = connection.execute(text("""
                SELECT count(*) FROM pg_proc AS proc CROSS JOIN pg_roles AS client
                WHERE proc.pronamespace='public'::regnamespace AND proc.prokind IN ('f','p')
                  AND proc.prosecdef AND client.rolname IN ('anon','authenticated')
                  AND has_function_privilege(client.rolname,proc.oid,'EXECUTE')
            """)).scalar_one()
            if ownership or crud != len(DATA_API_MANAGED_TABLES) * 4 or public_sequences or privileged_clients or rpc:
                raise RuntimeError('PostgreSQL owner or client isolation is unsafe')
    except Exception:
        # Reflection/driver diagnostics may contain a DSN or private metadata.
        # Startup never repairs a failure or renders the causal exception.
        raise RuntimeError('PostgreSQL startup validation failed; run the reviewed operator checks.') from None
