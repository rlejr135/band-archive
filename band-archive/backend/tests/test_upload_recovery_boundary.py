"""Startup recovery coordination against an owned local SQLite schema.

Recovery algorithms are doubled here so no R2 or worker work can happen. The
actual SQL inspection, app context, ordering and exception propagation remain.
The existing upload tests cover persistent recovery and startup-flag behavior.
"""

import pytest
from flask import Flask, current_app

from extensions import db
import routes.uploads as recovery


@pytest.fixture
def recovery_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _schema(app, *, band_column=True, direct=True):
    with app.app_context():
        with db.engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE multipart_upload_session (id TEXT PRIMARY KEY'
                                       + (', band_id INTEGER' if band_column else '') + ')')
            if direct:
                connection.exec_driver_sql('CREATE TABLE direct_upload_session (id TEXT PRIMARY KEY)')


def _doubles(app, monkeypatch, *, fail=None):
    calls = []

    def recover(label):
        assert current_app._get_current_object() is app
        calls.append(label)
        if fail == label:
            raise RuntimeError('injected recovery failure')

    monkeypatch.setattr(recovery, 'recover_sessions', lambda: recover('multipart'))
    monkeypatch.setattr(recovery, 'recover_direct_sessions', lambda: recover('direct'))
    monkeypatch.setattr(db.session, 'commit', lambda: pytest.fail('coordinator must not add a commit'))
    monkeypatch.setattr(db.session, 'rollback', lambda: pytest.fail('coordinator must not add rollback handling'))
    return calls


@pytest.mark.parametrize(('band_column', 'direct', 'expected'), [
    (False, True, []), (True, False, ['multipart']), (True, True, ['multipart', 'direct']),
])
def test_recovery_keeps_schema_gates_context_and_multipart_before_direct(
        recovery_app, monkeypatch, band_column, direct, expected):
    _schema(recovery_app, band_column=band_column, direct=direct)
    calls = _doubles(recovery_app, monkeypatch)
    assert recovery.recover_multipart_upload_sessions(recovery_app) is None
    assert calls == expected


@pytest.mark.parametrize('failure', ['multipart', 'direct'])
def test_recovery_propagates_failure_without_wrapping_all_phases_in_one_transaction(
        recovery_app, monkeypatch, failure):
    _schema(recovery_app)
    calls = _doubles(recovery_app, monkeypatch, fail=failure)
    with pytest.raises(RuntimeError, match='injected recovery failure'):
        recovery.recover_multipart_upload_sessions(recovery_app)
    assert calls == (['multipart'] if failure == 'multipart' else ['multipart', 'direct'])
