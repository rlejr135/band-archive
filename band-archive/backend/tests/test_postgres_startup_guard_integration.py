"""Optional owned localhost PG17 tests; never accepts an operator target URL."""
import os
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

import app as factory_module
from config import TestingConfig
from extensions import db
from startup_guard import assert_postgres_startup_ready
_spec = importlib.util.spec_from_file_location('owned_pg_startup_fixture',
    Path(__file__).with_name('test_preserve_identity_refresh_postgres.py'))
_fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture_module)
private_pg17 = _fixture_module.private_pg17
_upgrade_postgres = _fixture_module._upgrade_postgres

pytestmark = pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly opt into the provisioned owned localhost PG17 fixture')


def test_private_pg_startup_has_no_ddl_or_automatic_jobs_and_rejects_drift(private_pg17, monkeypatch):
    engine = create_engine(private_pg17)
    try:
        with pytest.raises(RuntimeError, match='PostgreSQL startup validation failed'):
            assert_postgres_startup_ready(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")).scalar_one() == 0
    finally:
        engine.dispose()
    _upgrade_postgres(private_pg17)
    import routes.uploads
    import media_processing
    monkeypatch.setattr(routes.uploads, 'recover_multipart_upload_sessions', lambda app: pytest.fail('automatic multipart recovery'))
    monkeypatch.setattr(media_processing, 'start_worker', lambda app: pytest.fail('automatic audio worker'))
    monkeypatch.setattr(factory_module.storage, 'init_app', lambda app: None)
    class LocalConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = private_pg17
        PG_AUDIO_WORKER_ENABLED = False
        PG_MULTIPART_RECOVERY_ENABLED = False
    statements = []
    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    event.listen(Engine, 'before_cursor_execute', observe)
    app = None
    try:
        app = factory_module.create_app(LocalConfig, start_worker=True)
        assert app.config['POSTGRES_STARTUP_VALIDATED'] is True
        assert app.config['BAND_API_SCHEMA_READY'] is True
        assert set(statements) <= {'SET', 'SELECT', 'WITH', 'SHOW'}
        assert 'audio_worker' not in app.extensions
    finally:
        event.remove(Engine, 'before_cursor_execute', observe)
        if app is not None:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    # Every perturbation and explicit undo is confined to this owned fixture.
    engine = create_engine(private_pg17)
    try:
        for sql, undo in (
            ('ALTER TABLE public.song DISABLE ROW LEVEL SECURITY', 'ALTER TABLE public.song ENABLE ROW LEVEL SECURITY'),
            ("UPDATE public.tenant_backfill_state SET status='planned'", "UPDATE public.tenant_backfill_state SET status='validated'"),
            ("UPDATE public.alembic_version SET version_num='20261006_12'", "UPDATE public.alembic_version SET version_num='20261021_22'"),
            ('ALTER TABLE public.song ADD COLUMN unreviewed_startup_column integer', 'ALTER TABLE public.song DROP COLUMN unreviewed_startup_column'),
            ('GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO PUBLIC', 'REVOKE SELECT ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC'),
            ('CREATE VIEW public.unreviewed_startup_view AS SELECT 1 AS value', 'DROP VIEW public.unreviewed_startup_view'),
            ('CREATE MATERIALIZED VIEW public.unreviewed_startup_materialized_view AS SELECT 1 AS value', 'DROP MATERIALIZED VIEW public.unreviewed_startup_materialized_view'),
            ('CREATE SEQUENCE public.unreviewed_standalone_sequence', 'DROP SEQUENCE public.unreviewed_standalone_sequence'),
        ):
            with engine.begin() as connection:
                connection.execute(text(sql))
            with app.app_context(), pytest.raises(RuntimeError, match='PostgreSQL startup validation failed'):
                assert_postgres_startup_ready(engine)
            # Undo fixture changes explicitly; no source or production repair.
            with engine.begin() as connection:
                connection.execute(text(undo))
            with app.app_context():
                assert_postgres_startup_ready(engine)
    finally:
        engine.dispose()
