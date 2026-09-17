"""Offline-only coverage for the Phase 5 legacy rehearsal boundary."""

import json
import sqlite3

import pytest
from flask_migrate import upgrade
from sqlalchemy import create_engine, text

from extensions import db
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from tools.legacy_rehearsal.rehearsal import RehearsalError, reconcile, run_rehearsal, safe_target_identity


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
    report = run_rehearsal(str(source), _url(target), "test SQLite compatibility rehearsal", allow_sqlite_target=True)
    assert report["before"]["table_counts"] == report["after"]["table_counts"]
    assert report["after"]["comments"]["roots"] == 1
    assert report["after"]["comments"]["root_target_fields"] == 1
    assert report["after"]["song_votes"]["aggregate"] == [{"target_id": 7, "votes": 1, "calculated_score": 1}]
    assert report["after"]["multipart_relations"]["media_parts"] == 1
    assert {row["key"] for row in report["after"]["object_manifest"]} >= {"media/clip.mp4", "personal_logs/log.mp4", "gallery/image.png"}
    with pytest.raises(RehearsalError, match="target must be empty"):
        run_rehearsal(str(source), _url(target), "test SQLite compatibility rehearsal", allow_sqlite_target=True)


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
        run_rehearsal(str(source), _url(target), "test rollback rehearsal", allow_sqlite_target=True)
    engine = create_engine(_url(target))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM song")).scalar_one() == 0
    finally:
        engine.dispose()


def test_target_label_must_explicitly_identify_a_rehearsal_namespace(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(target)
    with pytest.raises(RehearsalError, match="target label"):
        run_rehearsal(str(source), _url(target), "database", dry_run=True, allow_sqlite_target=True)


def test_postgresql_psycopg_url_contract_redacts_credential_material():
    identity = safe_target_identity("postgresql+psycopg://operator:password@staging.example.test:5432/rehearsal")
    assert identity["dialect"] == "postgresql"
    assert "password" not in json.dumps(identity)
    assert identity["host_fingerprint"]


def test_dry_run_is_source_byte_stable_and_reports_no_credentials(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    _upgrade(target)
    before = source.read_bytes()
    first = run_rehearsal(str(source), _url(target), "test dry-run rehearsal", dry_run=True, allow_sqlite_target=True)
    second = run_rehearsal(str(source), _url(target), "test dry-run rehearsal", dry_run=True, allow_sqlite_target=True)
    assert source.read_bytes() == before
    assert first == second
    assert "opaque" not in json.dumps(first)


def test_partial_historical_variant_is_refused_before_any_stamp_or_import(tmp_path):
    source, target = tmp_path / "old.sqlite", tmp_path / "target.sqlite"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE song (id INTEGER PRIMARY KEY, lyrics TEXT)")
        conn.execute("CREATE TABLE practice_log (id INTEGER PRIMARY KEY)")
    _upgrade(target)
    with pytest.raises(RehearsalError, match="unsupported pre-Alembic SQLite schema fingerprint"):
        run_rehearsal(str(source), _url(target), "test partial variant rehearsal", dry_run=True, allow_sqlite_target=True)
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
