"""Offline-only coverage for the Phase 5 legacy rehearsal boundary."""

import json
import sqlite3

import pytest
from flask_migrate import upgrade
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect

from extensions import db
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from click.testing import CliRunner

from tools.legacy_rehearsal.rehearsal import (
    RehearsalError, _validate_target_url, cli, reconcile, run_rehearsal,
    safe_target_identity,
)


def _url(path):
    return f"sqlite:///{path.as_posix()}"


def _upgrade(path, revision="head"):
    app = create_migration_app(_url(path))
    try:
        with app.app_context():
            upgrade(revision=revision, directory=str(MIGRATIONS_DIRECTORY))
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _legacy_fixture(path):
    _upgrade(path, "20260914_01")
    engine = create_engine(_url(path))
    try:
        with engine.begin() as conn:
            # Every row represents only historic relational data.  Secrets and
            # capability values are intentionally absent from test reports.
            conn.execute(text("INSERT INTO song (id,title,artist,upvote_count,downvote_count,vote_score) VALUES (7,'S','A',1,0,1)"))
            conn.execute(text("INSERT INTO rehearsal (id,title,date) VALUES (8,'R','2026-01-01')"))
            conn.execute(text("INSERT INTO rehearsal_songs (rehearsal_id,song_id) VALUES (8,7)"))
            conn.execute(text("INSERT INTO member (id,name,instrument) VALUES (9,'M','bass')"))
            conn.execute(text("INSERT INTO media (id,song_id,rehearsal_id,filename,transcoding_status,processing_attempts,upvote_count,downvote_count,vote_score) VALUES (10,7,8,'clip.mp4','not_required',0,1,0,1)"))
            conn.execute(text("INSERT INTO personal_log (id,member_id,title,filename,file_type,transcoding_status,processing_attempts) VALUES (11,9,'L','log.mp4','video','not_required',0)"))
            conn.execute(text("INSERT INTO gallery_image (id,filename) VALUES (12,'image.png')"))
            conn.execute(text("INSERT INTO comment (id,media_id,author,password_hash,content) VALUES (13,10,'a','hash','root')"))
            conn.execute(text("INSERT INTO comment (id,parent_id,author,password_hash,content) VALUES (14,13,'a','hash','reply')"))
            conn.execute(text("INSERT INTO song_vote (id,song_id,voter_hash,value,created_at,updated_at) VALUES (15,7,'voter-song',1,'2026-01-01','2026-01-01')"))
            conn.execute(text("INSERT INTO media_vote (id,media_id,voter_hash,value,created_at,updated_at) VALUES (16,10,'voter-media',1,'2026-01-01','2026-01-01')"))
            conn.execute(text("INSERT INTO multipart_upload_session (id,r2_upload_id,object_key,original_filename,content_type,declared_bytes,song_id,status,created_at,expires_at) VALUES ('s1','opaque','media/inflight.mp4','inflight.mp4','video/mp4',99,7,'initiated','2026-01-01','2026-01-02')"))
            conn.execute(text("INSERT INTO multipart_upload_part (id,session_id,part_number,uploaded_bytes,created_at) VALUES (17,'s1',1,99,'2026-01-01')"))
            conn.execute(text("INSERT INTO personal_log_multipart_upload_session (id,r2_upload_id,object_key,original_filename,title,content_type,declared_bytes,member_id,status,created_at,expires_at) VALUES ('s2','opaque','personal_logs/inflight-log.mp4','inflight-log.mp4','L','video/mp4',44,9,'initiated','2026-01-01','2026-01-02')"))
            conn.execute(text("INSERT INTO personal_log_multipart_upload_part (id,session_id,part_number,uploaded_bytes,created_at) VALUES (18,'s2',1,44,'2026-01-01')"))
    finally:
        engine.dispose()


def test_rehearsal_preserves_fixture_relationships_and_refuses_repeat(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(target)
    report = run_rehearsal(str(source), _url(target), allow_sqlite_target=True,
                           sqlite_test_target_path=str(target))
    assert {key: value for key, value in report["before"]["table_counts"].items()
            if "multipart" not in key} == {key: value for key, value in report["after"]["table_counts"].items()
                                            if "multipart" not in key}
    assert report["after"]["comments"]["roots"] == 1
    assert report["after"]["comments"]["root_target_fields"] == 1
    assert report["after"]["song_votes"]["aggregate"] == [{"target_id": 7, "votes": 1, "calculated_score": 1}]
    assert report["after"]["multipart_relations"]["media_parts"] == 0
    assert report["multipart_quarantine"]["multipart_upload_session"]["count"] == 1
    assert report["multipart_quarantine"]["multipart_upload_part"]["count"] == 1
    assert {row["key"] for row in report["after"]["object_manifest"]} >= {"media/clip.mp4", "personal_logs/log.mp4", "gallery/image.png"}
    with pytest.raises(RehearsalError, match="target must be empty"):
        run_rehearsal(str(source), _url(target), allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))


def test_rehearsal_rolls_back_a_post_import_reconciliation_failure(tmp_path, monkeypatch):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(target)
    import tools.legacy_rehearsal.rehearsal as rehearsal
    original, calls = rehearsal.reconcile, []

    def fail_after_target_import(connection, *args, **kwargs):
        report = original(connection, *args, **kwargs)
        calls.append(report)
        if len(calls) == 2:
            report["object_defects"] = [{"kind": "forced_test_failure"}]
        return report

    monkeypatch.setattr(rehearsal, "reconcile", fail_after_target_import)
    with pytest.raises(RehearsalError, match="transaction rolled back"):
        run_rehearsal(str(source), _url(target), allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))
    engine = create_engine(_url(target))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM song")).scalar_one() == 0
    finally:
        engine.dispose()


def test_sqlite_target_needs_exact_test_only_resolved_path(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(target)
    with pytest.raises(RehearsalError, match="explicit test target path"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True)
    with pytest.raises(RehearsalError, match="does not match"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(tmp_path / "other.sqlite"))


def test_postgresql_psycopg_url_contract_redacts_credential_material():
    identity = safe_target_identity("postgresql+psycopg://operator:password@staging.example.test:5432/rehearsal")
    assert identity["dialect"] == "postgresql"
    assert "password" not in json.dumps(identity)
    assert identity["host_fingerprint"]
    assert str(text("SELECT current_schema()").compile(dialect=postgresql_dialect())) == "SELECT current_schema()"
    with pytest.raises(RehearsalError, match="host or database"):
        _validate_target_url(
            "postgresql+psycopg://operator:password@production.example.test/rehearsal",
            expected_target_host="staging.example.test", expected_target_database="rehearsal",
            expected_target_schema="public", allow_sqlite_target=False, sqlite_test_target_path=None,
        )
    with pytest.raises(RehearsalError, match="localhost"):
        _validate_target_url(
            "postgresql+psycopg://operator:password@localhost/rehearsal",
            expected_target_host="localhost", expected_target_database="rehearsal",
            expected_target_schema="public", allow_sqlite_target=False, sqlite_test_target_path=None,
        )


def test_dry_run_is_source_byte_stable_and_reports_no_credentials(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(target)
    before = source.read_bytes()
    first = run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                          sqlite_test_target_path=str(target))
    second = run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                           sqlite_test_target_path=str(target))
    assert source.read_bytes() == before
    assert first == second
    assert first["source_files"]["source_before"]["sha256"] == first["source_files"]["source_after"]["sha256"]
    assert first["source_files"]["source_before"]["sha256"] == first["source_files"]["immutable_snapshot"]["sha256"]
    assert first["source_files"]["source_before"]["sha256"] == first["source_files"]["working_copy_before"]["sha256"]
    assert "opaque" not in json.dumps(first)


def test_partial_historical_variant_is_refused_before_any_stamp_or_import(tmp_path):
    source, target = tmp_path / "old.sqlite", tmp_path / "target.sqlite"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE song (id INTEGER PRIMARY KEY, lyrics TEXT)")
        conn.execute("CREATE TABLE practice_log (id INTEGER PRIMARY KEY)")
    _upgrade(target)
    with pytest.raises(RehearsalError, match="unsupported pre-Alembic SQLite schema fingerprint"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))
    engine = create_engine(_url(target))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM song")).scalar_one() == 0
    finally:
        engine.dispose()


def test_reconcile_optional_actual_listing_is_compare_only(tmp_path):
    path = tmp_path / "db.sqlite"
    _legacy_fixture(path)
    _upgrade(path)
    engine = create_engine(_url(path))
    try:
        with engine.connect() as connection:
            report = reconcile(connection, actual_objects=[{"key": "unexpected", "size": 1}])
        assert report["object_comparison"]["orphan_objects"] == ["unexpected"]
    finally:
        engine.dispose()


def test_content_digest_detects_non_primary_key_tampering_and_rolls_back(tmp_path, monkeypatch):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(target)
    import tools.legacy_rehearsal.rehearsal as rehearsal
    original = rehearsal._copy_rows

    def corrupt_after_copy(source_connection, target_connection):
        inserted = original(source_connection, target_connection)
        target_connection.execute(text("UPDATE song SET title = 'tampered' WHERE id = 7"))
        return inserted

    monkeypatch.setattr(rehearsal, "_copy_rows", corrupt_after_copy)
    with pytest.raises(RehearsalError, match="content digest mismatch"):
        run_rehearsal(str(source), _url(target), allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))
    engine = create_engine(_url(target))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM song")).scalar_one() == 0
    finally:
        engine.dispose()


def test_known_stamped_source_with_unexpected_index_is_refused(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    engine = create_engine(_url(source))
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE INDEX unexpected_song_title_idx ON song(title)"))
    finally:
        engine.dispose()
    _upgrade(target)
    with pytest.raises(RehearsalError, match="source copy migration/backfill failed"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))


@pytest.mark.parametrize("schema_mutation", [
    "CREATE TABLE unexpected_rehearsal_data (id INTEGER PRIMARY KEY)",
    "CREATE TRIGGER unexpected_song_update AFTER UPDATE ON song BEGIN SELECT 1; END",
])
def test_known_stamped_source_with_extra_table_or_trigger_is_refused(tmp_path, schema_mutation):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    engine = create_engine(_url(source))
    try:
        with engine.begin() as connection:
            connection.execute(text(schema_mutation))
    finally:
        engine.dispose()
    _upgrade(target)
    expected = ("source SQLite trigger contract" if schema_mutation.startswith("CREATE TRIGGER")
                else "source copy migration/backfill failed")
    with pytest.raises(RehearsalError, match=expected):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))


def test_known_hardened_source_with_the_expected_audit_triggers_is_accepted(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(source)
    _upgrade(target)
    report = run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                           sqlite_test_target_path=str(target))
    assert report["dry_run"] is True


def test_phase4_canonical_key_rules_allow_korean_spaces_without_leaking_upload_state(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    engine = create_engine(_url(source))
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE gallery_image SET filename = '한글 사진.png' WHERE id = 12"))
    finally:
        engine.dispose()
    _upgrade(target)
    report = run_rehearsal(str(source), _url(target), allow_sqlite_target=True,
                           sqlite_test_target_path=str(target))
    assert "gallery/한글 사진.png" in {item["key"] for item in report["after"]["object_manifest"]}
    assert report["after"]["table_counts"]["multipart_upload_session"] == 0
    assert report["after"]["table_counts"]["multipart_upload_part"] == 0
    assert "s1" not in json.dumps(report["after"])
    assert "opaque" not in json.dumps(report)


@pytest.mark.parametrize("filename", ["../escape.mp4", "https://storage.example.test/object.mp4", "media/wrong-prefix.png"])
def test_phase4_canonical_key_rules_refuse_traversal_url_and_cross_prefix_values(tmp_path, filename):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    engine = create_engine(_url(source))
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE gallery_image SET filename = :filename WHERE id = 12"), {"filename": filename})
    finally:
        engine.dispose()
    _upgrade(target)
    with pytest.raises(RehearsalError, match="source copy migration/backfill failed"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))


def test_cli_error_never_exposes_driver_sql_or_credentials(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite"
    source.touch()
    import tools.legacy_rehearsal.rehearsal as rehearsal
    monkeypatch.setattr(rehearsal, "run_rehearsal", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("SELECT * FROM users WHERE password='sensitive-value'")))
    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://user:sensitive-value@host/db",
        "--expected-target-host", "host", "--expected-target-database", "db", "--expected-target-schema", "public",
    ])
    assert result.exit_code != 0
    assert "rehearsal_failed correlation_id=" in result.output
    assert "sensitive-value" not in result.output
    assert "SELECT" not in result.output
