"""Startup policy tests: no target credentials or external service calls."""
from types import SimpleNamespace

import pytest
import app as factory_module
from config import TestingConfig
from startup_guard import assert_postgres_startup_ready, postgres_automation_allowed


@pytest.mark.parametrize('audio,multipart,requested,expected_audio', [
    (False, False, None, False), (False, False, True, False),
    (True, False, False, False), (True, False, True, True),
    (False, True, True, False), (True, True, True, True),
])
def test_pg_factory_is_validated_before_storage_or_independent_automation(
        monkeypatch, audio, multipart, requested, expected_audio):
    calls = []
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    monkeypatch.setattr(factory_module, 'db', SimpleNamespace(engine=engine, init_app=lambda app: None))
    monkeypatch.setattr(factory_module, 'Migrate', lambda *args, **kwargs: None)
    monkeypatch.setattr(factory_module, 'assert_postgres_startup_ready', lambda engine: calls.append('validated'))
    monkeypatch.setattr(factory_module.storage, 'init_app', lambda app: calls.append('storage'))
    monkeypatch.setattr(factory_module, 'baseline_metadata', lambda: pytest.fail('PG must never call create_all'))
    monkeypatch.setattr(factory_module, '_run_migrations', lambda app: pytest.fail('PG must never run migrations'))
    import media_processing
    import routes.uploads
    monkeypatch.setattr(media_processing, 'start_worker', lambda app: calls.append('audio'))
    monkeypatch.setattr(routes.uploads, 'recover_multipart_upload_sessions', lambda app: calls.append('multipart'))
    class LocalConfig(TestingConfig):
        TESTING = False
        PG_AUDIO_WORKER_ENABLED = audio
        PG_MULTIPART_RECOVERY_ENABLED = multipart
        POSTGRES_STARTUP_VALIDATED = True  # Config cannot bless its own target.
    app = factory_module.create_app(LocalConfig, start_worker=requested)
    assert calls[:2] == ['validated', 'storage']
    assert ('audio' in calls) == expected_audio
    assert ('multipart' in calls) == multipart
    assert app.config['POSTGRES_STARTUP_VALIDATED'] is True
    assert app.config['BAND_API_SCHEMA_READY'] is True


def test_pg_guard_failure_stops_before_storage_and_automation(monkeypatch):
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    monkeypatch.setattr(factory_module, 'db', SimpleNamespace(engine=engine, init_app=lambda app: None))
    monkeypatch.setattr(factory_module, 'Migrate', lambda *args, **kwargs: None)
    def reject(engine):
        raise RuntimeError('safe validation refusal')
    monkeypatch.setattr(factory_module, 'assert_postgres_startup_ready', reject)
    monkeypatch.setattr(factory_module.storage, 'init_app', lambda app: pytest.fail('storage initialized before guard'))
    with pytest.raises(RuntimeError, match='safe validation refusal'):
        factory_module.create_app(TestingConfig, start_worker=True)


@pytest.mark.parametrize('testing', [True, False])
def test_pg_explicit_worker_request_never_bypasses_flag(monkeypatch, testing):
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    app = SimpleNamespace(config={'PG_AUDIO_WORKER_ENABLED': False,
                                 'POSTGRES_STARTUP_VALIDATED': True}, testing=testing)
    assert postgres_automation_allowed(app, engine, 'PG_AUDIO_WORKER_ENABLED') is False
    app.config['PG_AUDIO_WORKER_ENABLED'] = True
    app.config['POSTGRES_STARTUP_VALIDATED'] = False
    assert postgres_automation_allowed(app, engine, 'PG_AUDIO_WORKER_ENABLED') is False
    app.config['POSTGRES_STARTUP_VALIDATED'] = True
    assert postgres_automation_allowed(app, engine, 'PG_AUDIO_WORKER_ENABLED') is True
    app.config['PG_AUDIO_WORKER_ENABLED'] = 'true'  # No truthy config-string bypass.
    assert postgres_automation_allowed(app, engine, 'PG_AUDIO_WORKER_ENABLED') is False


def test_direct_worker_call_cannot_recover_or_start_when_pg_disabled(monkeypatch):
    import media_processing
    app = factory_module.Flask(__name__)
    app.config.update(POSTGRES_STARTUP_VALIDATED=True, PG_AUDIO_WORKER_ENABLED=False)
    monkeypatch.setattr(media_processing, 'db', SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))))
    monkeypatch.setattr(media_processing, 'recover_stale_processing', lambda *args: pytest.fail('recovered while disabled'))
    monkeypatch.setattr(media_processing, 'AudioWorker', lambda app: pytest.fail('started while disabled'))
    assert media_processing.start_worker(app) is None


def test_sqlite_automation_policy_is_unchanged():
    engine = SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))
    app = SimpleNamespace(config={})
    assert postgres_automation_allowed(app, engine, 'PG_AUDIO_WORKER_ENABLED') is True
    assert postgres_automation_allowed(app, engine, 'PG_MULTIPART_RECOVERY_ENABLED') is True


def test_pg_errors_do_not_render_driver_secrets():
    class BrokenEngine:
        def connect(self):
            raise RuntimeError('postgresql://user:local-unit-only-secret@localhost/database')
    with pytest.raises(RuntimeError) as caught:
        assert_postgres_startup_ready(BrokenEngine())
    assert 'local-unit-only-secret' not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_docker_context_excludes_all_local_virtualenvs():
    from pathlib import Path
    lines = (Path(__file__).parents[1] / '.dockerignore').read_text().splitlines()
    assert '.venv-test/' in lines and '.venv-*/' in lines
