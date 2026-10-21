"""Private PostgreSQL integration contracts for the claimed-archive refresh.

These tests create an owned localhost PostgreSQL 17 cluster.  They never use
the operator-facing URL validator: that validator deliberately rejects
localhost and requires a real pinned CA, while this module exercises the
transaction core behind an explicit monkeypatch seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
import socket
import subprocess
import time
import importlib.util
import os
from pathlib import Path

import pytest
from flask_migrate import upgrade
from sqlalchemy import create_engine, text
from sqlalchemy.engine.base import RootTransaction

from extensions import db
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from tenant_backfill import apply as apply_tenant_backfill
from tools.legacy_rehearsal.rehearsal import RehearsalError, _copy_and_backfill, _file_fingerprint, reconcile
from tools.legacy_rehearsal.preserve_identity_refresh import (
    PROFILE_VERSION, PROTECTED_TABLES, REFRESHED_TABLES, _insert_source_domain,
    _table_digests, run_preserve_identity_refresh,
)

# Do not discover or download a server: this optional suite uses only the
# explicitly provisioned local fixture binary.
PG_BIN = Path(__file__).parents[1] / "tools" / ".state" / "postgresql117" / "pgsql" / "bin"
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PRIVATE_PG_INTEGRATION") != "1",
    reason="set RUN_PRIVATE_PG_INTEGRATION=1 with a provisioned private PostgreSQL 17 fixture",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def private_pg17(tmp_path):
    if not all((PG_BIN / name).is_file() for name in ("initdb.exe", "postgres.exe")):
        pytest.skip("private PostgreSQL 17 fixture binaries are unavailable")
    port, data, log = _free_port(), tmp_path / "pgdata", tmp_path / "postgres.log"
    subprocess.run([str(PG_BIN / "initdb.exe"), "--no-locale", "--encoding=UTF8", "--username=postgres",
                    "--auth=trust", "--pgdata", str(data)], check=True, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with log.open("ab") as stream:
        process = subprocess.Popen([str(PG_BIN / "postgres.exe"), "-D", str(data), "-h", "127.0.0.1",
                                    "-p", str(port)], stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("owned PostgreSQL exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("owned PostgreSQL did not start")
            url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
            readiness_engine = create_engine(url, connect_args={"connect_timeout": 2})
            try:
                sql_deadline = time.monotonic() + 20
                while True:
                    try:
                        with readiness_engine.connect() as connection:
                            connection.execute(text("SELECT 1")).scalar_one()
                        break
                    except Exception as error:
                        original = getattr(error, "orig", None)
                        sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
                        known_startup = sqlstate == "57P03" or "the database system is starting up" in str(original).lower()
                        if not known_startup or time.monotonic() >= sql_deadline:
                            raise
                        time.sleep(0.1)
            finally:
                readiness_engine.dispose()
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)


def _upgrade_postgres(url: str) -> None:
    app = create_migration_app(url)
    try:
        with app.app_context():
            upgrade(revision="20260916_05", directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                apply_tenant_backfill(connection)
            upgrade(revision="head", directory=str(MIGRATIONS_DIRECTORY))
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _claimed_target(engine):
    with engine.begin() as connection:
        user_id = connection.execute(text(
            "INSERT INTO app_user (supabase_user_id,email,status,email_verified,is_platform_admin,created_at,updated_at) "
            "VALUES ('private-subject','private@example.test','active',true,true,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) RETURNING id"
        )).scalar_one()
        connection.execute(text("INSERT INTO auth_bootstrap_state (key,claimed_by_user_id,claimed_at) "
                                "VALUES ('initial_platform_admin',:user,CURRENT_TIMESTAMP)"), {"user": user_id})
        band_id = connection.execute(text("SELECT id FROM band WHERE is_legacy_import IS TRUE")).scalar_one()
        connection.execute(text("UPDATE band SET created_by_user_id=:user WHERE id=:band"), {"user": user_id, "band": band_id})
        connection.execute(text("INSERT INTO band_membership (band_id,user_id,role,status,requested_at,approved_at) "
                                "VALUES (:band,:user,'owner','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
                           {"band": band_id, "user": user_id})
        connection.execute(text("INSERT INTO legacy_band_claim_token (band_id,purpose,token_hash,issued_by_user_id,created_at,expires_at,redeemed_at,redeemed_by_user_id) "
                                "VALUES (:band,'initial_owner',:hash,:user,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,:user)"),
                           {"band": band_id, "user": user_id, "hash": "d" * 64})
        for event in ("legacy_claim_issued", "legacy_claim_redeemed", "legacy_owner_assigned"):
            connection.execute(text("INSERT INTO audit_event (band_id,actor_user_id,event_type,created_at) "
                                    "VALUES (:band,:user,:event,CURRENT_TIMESTAMP)"),
                               {"band": band_id, "user": user_id, "event": event})
    return band_id


def _fixture(tmp_path, private_pg17):
    # Reuse the historical-only fixture; it includes a nonempty graph and the
    # migration path under test strips its quarantined multipart rows.
    spec = importlib.util.spec_from_file_location("private_legacy_fixture", Path(__file__).with_name("test_legacy_rehearsal.py"))
    assert spec and spec.loader
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)

    source_path, work = tmp_path / "source.sqlite", tmp_path / "work"
    fixture_module._legacy_fixture(source_path)
    work.mkdir()
    source_copy, _, _ = _copy_and_backfill(str(source_path), work,
                                            expected_source_fingerprint=_file_fingerprint(source_path))
    source_engine, target_engine = create_engine(f"sqlite:///{source_copy.as_posix()}"), create_engine(private_pg17)
    _upgrade_postgres(private_pg17)
    try:
        target_band_id = _claimed_target(target_engine)
        with source_engine.connect() as source, target_engine.begin() as target:
            source_band_id = target.execute(text("SELECT id FROM band WHERE is_legacy_import IS TRUE")).scalar_one()
            _insert_source_domain(source, target, source_band_id, target_band_id)
        with target_engine.connect() as target:
            profile = {
                "version": PROFILE_VERSION, "target_writes_frozen": True,
                "provenance": {"baseline_proof": {"file": "baseline.json", "sha256": "0" * 64},
                               "post_claim": {"file": "postclaim.json", "sha256": "1" * 64}},
                "protected": {"table_digests": _table_digests(target, PROTECTED_TABLES)},
                "legacy_domain": {"table_digests": _table_digests(target, REFRESHED_TABLES, band_id=target_band_id)},
            }
            objects = reconcile(target)["object_manifest"]
        return source_path, target_engine, target_band_id, profile, {"objects": objects}
    finally:
        source_engine.dispose()


def _internal_target_seam(monkeypatch):
    import tools.legacy_rehearsal.preserve_identity_refresh as refresh
    monkeypatch.setattr(refresh, "_validate_refresh_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(refresh, "_validate_target_url", lambda *args, **kwargs: {"dialect": "postgresql", "schema": "public"})


def _sequence_values(connection):
    """Capture every sequence owned by the refresh graph without lowering it."""
    rows = connection.execute(text(
        "SELECT c.relname, pg_sequence_last_value(c.oid) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind='S' AND n.nspname=current_schema() ORDER BY c.relname"
    )).all()
    return dict(rows)


def _refresh(source, url, profile, object_proof, prepared, marker):
    return run_preserve_identity_refresh(
        str(source), url, expected_target_host="private", expected_target_database="postgres",
        expected_target_schema="public", source_write_freeze_attestation={"version": "phase9-write-freeze-v1", "writes_frozen": True, "snapshot": _file_fingerprint(source)},
        reviewed_profile=profile, object_proof=object_proof,
        report_publisher=lambda value: prepared.append(value) or "a" * 64,
        committed_marker_publisher=marker.append if hasattr(marker, "append") else marker,
    )


@pytest.mark.postgres_integration
def test_private_postgres_refresh_happy_path_preserves_claimed_identity(tmp_path, private_pg17, monkeypatch):
    _internal_target_seam(monkeypatch)
    source, target, band_id, profile, object_proof = _fixture(tmp_path, private_pg17)
    with target.begin() as connection:
        connection.execute(text(
            "INSERT INTO band_request_email_outbox "
            "(request_id,recipient_user_id,decision,status,next_attempt_at,created_at,updated_at) "
            "VALUES (NULL,NULL,'approved','pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP), "
            "(NULL,NULL,'rejected','sent',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "UPDATE band_request_email_outbox SET sent_at=CURRENT_TIMESTAMP WHERE status='sent'"
        ))
    with target.connect() as connection:
        profile["protected"]["table_digests"] = _table_digests(connection, PROTECTED_TABLES)
        outbox_before = profile["protected"]["table_digests"]["band_request_email_outbox"]
    with create_engine(f"sqlite:///{source.as_posix()}").begin() as source_connection:
        source_connection.execute(text("UPDATE song SET title='changed in frozen source' WHERE id=7"))
    prepared, marker = [], []
    result = _refresh(source, private_pg17, profile, object_proof, prepared, marker)
    assert result["status"] == "prepared"
    assert prepared[0]["status"] == "prepared"
    assert marker[0]["status"] == "committed"
    with target.connect() as connection:
        assert connection.execute(text("SELECT title FROM song WHERE id=7")).scalar_one() == "changed in frozen source"
        assert _table_digests(connection, PROTECTED_TABLES) == profile["protected"]["table_digests"]
        assert _table_digests(connection, PROTECTED_TABLES)["band_request_email_outbox"] == outbox_before
        assert _table_digests(connection, REFRESHED_TABLES, band_id=band_id)["song"]["count"] == 1
    target.dispose()


@pytest.mark.postgres_integration
@pytest.mark.parametrize('state', ('active', 'released'))
def test_private_postgres_refresh_refuses_existing_rights_case_without_writes(
        tmp_path, private_pg17, monkeypatch, state):
    _internal_target_seam(monkeypatch)
    source, target, band_id, profile, object_proof = _fixture(tmp_path, private_pg17)
    with target.begin() as connection:
        song_id = connection.execute(text('SELECT id FROM song WHERE band_id=:band_id'),
                                     {'band_id': band_id}).scalar_one()
        connection.execute(text("""INSERT INTO content_restriction
            (band_id, resource_type, resource_id, case_ref, reason_code, state, created_at, released_at)
            VALUES (:band_id, 'song', :song_id, :case_ref, 'privacy', :state,
                    CURRENT_TIMESTAMP, :released_at)"""),
            {'band_id': band_id, 'song_id': song_id, 'case_ref': f'preserve-case-{state}', 'state': state,
             'released_at': datetime.now(timezone.utc) if state == 'released' else None})
    with target.connect() as connection:
        case_before = connection.execute(text("""SELECT band_id, resource_type, resource_id, case_ref,
            reason_code, state, created_at, released_at FROM content_restriction""")).one()
        song_before = connection.execute(text('SELECT title FROM song WHERE id=:id'),
                                         {'id': song_id}).scalar_one()
    source_engine = create_engine(f'sqlite:///{source.as_posix()}')
    try:
        with source_engine.begin() as connection:
            connection.execute(text("UPDATE song SET title='must not refresh' WHERE id=7"))
    finally:
        source_engine.dispose()
    source_before = _file_fingerprint(source)
    prepared, marker = [], []
    with pytest.raises(RehearsalError, match='refresh target contains unsupported new content'):
        _refresh(source, private_pg17, profile, object_proof, prepared, marker)
    assert prepared == marker == []
    assert _file_fingerprint(source) == source_before
    with target.connect() as connection:
        assert connection.execute(text("""SELECT band_id, resource_type, resource_id, case_ref,
            reason_code, state, created_at, released_at FROM content_restriction""")).one() == case_before
        assert connection.execute(text('SELECT title FROM song WHERE id=:id'),
                                  {'id': song_id}).scalar_one() == song_before
    target.dispose()


@pytest.mark.postgres_integration
def test_private_postgres_report_failure_rolls_back_without_lowering_sequence(tmp_path, private_pg17, monkeypatch):
    _internal_target_seam(monkeypatch)
    source, target, _, profile, object_proof = _fixture(tmp_path, private_pg17)
    with target.begin() as connection:
        sequence = connection.execute(text("SELECT pg_get_serial_sequence('song','id')")).scalar_one()
        connection.execute(text("SELECT setval(:sequence, 999, true)"), {"sequence": sequence})
    with target.connect() as connection:
        sequences_before = _sequence_values(connection)
    with create_engine(f"sqlite:///{source.as_posix()}").begin() as connection:
        connection.execute(text("UPDATE song SET title='must roll back' WHERE id=7"))
    prepared = []
    # Force the pre-commit publisher specifically; the target must stay old.
    published = []
    with pytest.raises(Exception, match="preserve-identity refresh failed"):
        run_preserve_identity_refresh(
            str(source), private_pg17, expected_target_host="private", expected_target_database="postgres", expected_target_schema="public",
            source_write_freeze_attestation={"version": "phase9-write-freeze-v1", "writes_frozen": True, "snapshot": _file_fingerprint(source)},
            reviewed_profile=profile, object_proof=object_proof,
            report_publisher=lambda value: published.append(value) or (_ for _ in ()).throw(RuntimeError("publish failed")),
        )
    assert published and published[0]["status"] == "prepared"
    with target.connect() as connection:
        assert connection.execute(text("SELECT title FROM song WHERE id=7")).scalar_one() == "S"
        assert _table_digests(connection, PROTECTED_TABLES) == profile["protected"]["table_digests"]
        sequences_after = _sequence_values(connection)
        assert all(sequences_after[name] >= value for name, value in sequences_before.items() if value is not None)
        assert connection.execute(text("SELECT nextval('song_id_seq')")).scalar_one() > 999
    target.dispose()


@pytest.mark.postgres_integration
def test_private_postgres_commit_failure_leaves_only_prepared_evidence(tmp_path, private_pg17, monkeypatch):
    _internal_target_seam(monkeypatch)
    source, target, _, profile, object_proof = _fixture(tmp_path, private_pg17)
    with create_engine(f"sqlite:///{source.as_posix()}").begin() as connection:
        connection.execute(text("UPDATE song SET title='commit must fail' WHERE id=7"))
    prepared, marker = [], []
    original_commit = RootTransaction.commit

    def fail_postgres_commit(self):
        if self.connection.engine.dialect.name == "postgresql":
            raise RuntimeError("injected commit failure")
        return original_commit(self)

    monkeypatch.setattr(RootTransaction, "commit", fail_postgres_commit)
    with pytest.raises(Exception):
        _refresh(source, private_pg17, profile, object_proof, prepared, marker)
    assert prepared and prepared[0]["status"] == "prepared"
    assert marker == []
    with target.connect() as connection:
        assert connection.execute(text("SELECT title FROM song WHERE id=7")).scalar_one() == "S"
    target.dispose()


@pytest.mark.postgres_integration
def test_private_postgres_committed_marker_failure_keeps_prepared_evidence(tmp_path, private_pg17, monkeypatch):
    _internal_target_seam(monkeypatch)
    source, target, _, profile, object_proof = _fixture(tmp_path, private_pg17)
    with create_engine(f"sqlite:///{source.as_posix()}").begin() as connection:
        connection.execute(text("UPDATE song SET title='committed despite marker failure' WHERE id=7"))
    prepared = []
    with pytest.raises(Exception) as failure:
        _refresh(source, private_pg17, profile, object_proof, prepared,
                 lambda value: (_ for _ in ()).throw(RuntimeError("marker publish failed")))
    assert "preserve-identity refresh failed" in str(failure.value)
    assert failure.value.__cause__ and "marker publish failed" in str(failure.value.__cause__)
    assert prepared and prepared[0]["status"] == "prepared"
    with target.connect() as connection:
        assert connection.execute(text("SELECT title FROM song WHERE id=7")).scalar_one() == "committed despite marker failure"
    target.dispose()


@pytest.mark.postgres_integration
def test_private_postgres_malformed_source_graph_is_refused_before_target_write(tmp_path, private_pg17, monkeypatch):
    _internal_target_seam(monkeypatch)
    source, target, _, profile, object_proof = _fixture(tmp_path, private_pg17)
    with create_engine(f"sqlite:///{source.as_posix()}").begin() as connection:
        connection.execute(text("UPDATE comment SET parent_id=id WHERE id=13"))
    with target.connect() as connection:
        protected_before = _table_digests(connection, PROTECTED_TABLES)
        domain_before = _table_digests(connection, REFRESHED_TABLES, band_id=1)
    with pytest.raises(Exception, match="source copy migration/backfill failed"):
        _refresh(source, private_pg17, profile, object_proof, [], [])
    with target.connect() as connection:
        assert _table_digests(connection, PROTECTED_TABLES) == protected_before
        assert _table_digests(connection, REFRESHED_TABLES, band_id=1) == domain_before
    target.dispose()
