"""Offline-only coverage for the Phase 5 legacy rehearsal boundary."""

import hashlib
import json
import re
import sqlite3
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask_migrate import upgrade
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect

from extensions import db
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from click.testing import CliRunner

from tools.legacy_rehearsal.rehearsal import (
    RehearsalError, _ReservedReportOutput, _assert_supported_source_schema, _baseline_sqlite_contract,
    _contract_without_full_ddl_digests, _file_fingerprint,
    _mapped_sqlite_digests, _normalize_production_legacy_copy,
    _production_legacy_contract, _report_payload, _sqlite_schema_contract, _validate_target_url,
    cli, reconcile, run_rehearsal,
    safe_target_identity,
)


def _insert_lifecycle_media(path, *, pruned=False):
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO song (id,title,artist,upvote_count,downvote_count,vote_score) "
                "VALUES (70,'Lifecycle','Artist',0,0,0)"
            ))
            connection.execute(text(
                "INSERT INTO media ("
                "id,song_id,filename,object_key,file_type,file_size,transcoding_status,audio_filename,"
                "audio_object_key,processing_attempts,video_720_filename,video_720_object_key,"
                "video_720_completed_at,original_pruned_at,upvote_count,downvote_count,vote_score"
                ") VALUES ("
                "71,70,'한글 원본.mp4','media/한글 원본.mp4','video',999,'completed',"
                "'한글 음원.m4a','media/한글 음원.m4a',0,'media/한글 720.mp4',"
                "'media/한글 720.mp4','2026-09-01',:pruned,0,0,0)"
            ), {'pruned': '2026-09-02' if pruned else None})
    finally:
        engine.dispose()


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


def _exact_fly_legacy_fixture(path):
    """Construct the reviewed production layout, including historical order."""
    _legacy_fixture(path)
    production_tables = {
        "comment": """CREATE TABLE comment (
            id INTEGER NOT NULL,
            media_id INTEGER,
            personal_log_id INTEGER,
            parent_id INTEGER,
            author VARCHAR(50) NOT NULL,
            password_hash VARCHAR(200) NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME,
            timestamp_seconds FLOAT,
            PRIMARY KEY (id),
            FOREIGN KEY(media_id) REFERENCES media (id),
            FOREIGN KEY(personal_log_id) REFERENCES personal_log (id),
            FOREIGN KEY(parent_id) REFERENCES comment (id)
        )""",
        "media": """CREATE TABLE media (
            id INTEGER NOT NULL,
            song_id INTEGER NOT NULL,
            filename VARCHAR(200) NOT NULL,
            file_type VARCHAR(20),
            file_size INTEGER,
            created_at DATETIME,
            original_filename VARCHAR(200),
            rehearsal_id INTEGER REFERENCES rehearsal(id),
            is_featured BOOLEAN DEFAULT 0,
            transcoding_status VARCHAR(20) DEFAULT 'pending',
            audio_filename VARCHAR(200),
            processing_error VARCHAR(500),
            processing_started_at DATETIME,
            processing_completed_at DATETIME,
            processing_attempts INTEGER DEFAULT 0,
            processing_heartbeat_at DATETIME,
            video_720_filename VARCHAR(300),
            video_720_source_etag VARCHAR(200),
            video_720_profile VARCHAR(100),
            video_720_completed_at DATETIME,
            upvote_count INTEGER NOT NULL DEFAULT 0,
            downvote_count INTEGER NOT NULL DEFAULT 0,
            vote_score INTEGER NOT NULL DEFAULT 0,
            original_pruned_at DATETIME,
            PRIMARY KEY (id),
            FOREIGN KEY(song_id) REFERENCES song (id)
        )""",
        "multipart_upload_part": """CREATE TABLE multipart_upload_part (
            id INTEGER NOT NULL,
            session_id VARCHAR(36) NOT NULL,
            part_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            etag VARCHAR(500),
            uploaded_bytes BIGINT,
            checksum VARCHAR(200),
            acknowledged_at DATETIME,
            PRIMARY KEY (id),
            CONSTRAINT uq_multipart_session_part UNIQUE (session_id, part_number),
            FOREIGN KEY(session_id) REFERENCES multipart_upload_session (id)
        )""",
        "multipart_upload_session": """CREATE TABLE multipart_upload_session (
            id VARCHAR(36) NOT NULL,
            r2_upload_id VARCHAR(200) NOT NULL,
            object_key VARCHAR(300) NOT NULL,
            original_filename VARCHAR(200) NOT NULL,
            content_type VARCHAR(100) NOT NULL,
            declared_bytes BIGINT NOT NULL,
            song_id INTEGER NOT NULL,
            rehearsal_id INTEGER,
            status VARCHAR(20) NOT NULL,
            media_id INTEGER,
            created_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            completed_at DATETIME,
            capability_token_hash VARCHAR(255),
            completion_started_at DATETIME,
            PRIMARY KEY (id),
            FOREIGN KEY(song_id) REFERENCES song (id),
            FOREIGN KEY(rehearsal_id) REFERENCES rehearsal (id),
            FOREIGN KEY(media_id) REFERENCES media (id)
        )""",
        "personal_log": """CREATE TABLE personal_log (
            id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL,
            filename VARCHAR(200) NOT NULL,
            original_filename VARCHAR(200),
            file_type VARCHAR(20) NOT NULL,
            created_at DATETIME,
            file_size INTEGER,
            transcoding_status VARCHAR(20) DEFAULT 'not_required',
            audio_filename VARCHAR(200),
            processing_error VARCHAR(500),
            processing_started_at DATETIME,
            processing_completed_at DATETIME,
            processing_attempts INTEGER DEFAULT 0,
            processing_heartbeat_at DATETIME,
            PRIMARY KEY (id),
            FOREIGN KEY(member_id) REFERENCES member (id)
        )""",
        "personal_log_multipart_upload_part": """CREATE TABLE personal_log_multipart_upload_part (
            id INTEGER NOT NULL,
            session_id VARCHAR(36) NOT NULL,
            part_number INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            etag VARCHAR(500),
            uploaded_bytes BIGINT,
            checksum VARCHAR(200),
            acknowledged_at DATETIME,
            PRIMARY KEY (id),
            CONSTRAINT uq_personal_log_multipart_session_part UNIQUE (session_id, part_number),
            FOREIGN KEY(session_id) REFERENCES personal_log_multipart_upload_session (id)
        )""",
        "personal_log_multipart_upload_session": """CREATE TABLE personal_log_multipart_upload_session (
            id VARCHAR(36) NOT NULL,
            r2_upload_id VARCHAR(200) NOT NULL,
            object_key VARCHAR(300) NOT NULL,
            original_filename VARCHAR(200) NOT NULL,
            title VARCHAR(200) NOT NULL,
            content_type VARCHAR(100) NOT NULL,
            declared_bytes BIGINT NOT NULL,
            member_id INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL,
            personal_log_id INTEGER,
            created_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            completed_at DATETIME,
            capability_token_hash VARCHAR(255),
            completion_started_at DATETIME,
            PRIMARY KEY (id),
            FOREIGN KEY(member_id) REFERENCES member (id),
            FOREIGN KEY(personal_log_id) REFERENCES personal_log (id)
        )""",
        "rehearsal": """CREATE TABLE rehearsal (
            id INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL,
            date DATE NOT NULL,
            start_date DATE,
            end_date DATE,
            time VARCHAR(20),
            memo TEXT,
            color VARCHAR(7),
            created_at DATETIME,
            updated_at DATETIME,
            location VARCHAR(200),
            latitude FLOAT,
            longitude FLOAT,
            PRIMARY KEY (id)
        )""",
        "song": """CREATE TABLE song (
            id INTEGER NOT NULL,
            title VARCHAR(100) NOT NULL,
            artist VARCHAR(100) NOT NULL,
            status VARCHAR(20),
            lyrics TEXT,
            chords TEXT,
            link VARCHAR(200),
            memo TEXT,
            genre VARCHAR(50),
            difficulty INTEGER,
            sheet_music VARCHAR(200),
            created_at DATETIME,
            updated_at DATETIME,
            upvote_count INTEGER NOT NULL DEFAULT 0,
            downvote_count INTEGER NOT NULL DEFAULT 0,
            vote_score INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (id)
        )""",
    }
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table, ddl in production_tables.items():
            legacy_table = table + "_legacy_shape"
            connection.execute(ddl.replace(f"CREATE TABLE {table}", f"CREATE TABLE {legacy_table}", 1))
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            quoted = ", ".join(f'"{column}"' for column in columns)
            connection.execute(f'INSERT INTO "{legacy_table}" ({quoted}) SELECT {quoted} FROM "{table}"')
            connection.execute(f'DROP TABLE "{table}"')
            connection.execute(f'ALTER TABLE "{legacy_table}" RENAME TO "{table}"')
        connection.execute("DROP TABLE alembic_version")
        connection.execute("PRAGMA foreign_keys = ON")


def _approve_test_fly_signature(monkeypatch, path):
    """Test only: make this locally built structural fixture the reviewed one."""
    import tools.legacy_rehearsal.rehearsal as rehearsal
    objects, aggregate = rehearsal._sqlite_schema_signature(path)
    monkeypatch.setattr(rehearsal, "_APPROVED_FLY_SCHEMA_OBJECTS", objects)
    monkeypatch.setattr(rehearsal, "_APPROVED_FLY_SCHEMA_AGGREGATE", aggregate)


def _replace_master_ddl(path, table, old, new):
    """Use valid replacement tokens only; reopen verifies SQLite can parse it."""
    with sqlite3.connect(path) as connection:
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()[0]
        assert old in ddl
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?",
                           (ddl.replace(old, new, 1), table))
        connection.execute("PRAGMA writable_schema = OFF")


def _append_master_ddl(path, table, suffix):
    with sqlite3.connect(path) as connection:
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()[0]
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?",
                           (ddl + suffix, table))
        connection.execute("PRAGMA writable_schema = OFF")


def _replace_song_with_autoincrement_pk(path):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'song'"
        ).fetchone()[0]
        ddl = ddl.replace("id INTEGER NOT NULL,", "id INTEGER PRIMARY KEY AUTOINCREMENT,", 1)
        ddl = re.sub(r",\s*PRIMARY KEY \(id\)\s*\)$", "\n)", ddl, count=1)
        assert "AUTOINCREMENT" in ddl and "PRIMARY KEY (id)" not in ddl
        ddl = re.sub(
            r'^CREATE TABLE (?:"song"|song)', "CREATE TABLE song_autoincrement",
            ddl, count=1,
        )
        connection.execute(ddl)
        columns = [row[1] for row in connection.execute('PRAGMA table_info("song")')]
        quoted = ", ".join(f'"{column}"' for column in columns)
        connection.execute(f'INSERT INTO "song_autoincrement" ({quoted}) SELECT {quoted} FROM "song"')
        connection.execute('DROP TABLE "song"')
        connection.execute('ALTER TABLE "song_autoincrement" RENAME TO "song"')
        connection.execute("PRAGMA foreign_keys = ON")


def test_exact_fly_legacy_variant_normalizes_only_a_work_copy(tmp_path, monkeypatch):
    source, target = tmp_path / "fly.sqlite", tmp_path / "target.sqlite"
    _exact_fly_legacy_fixture(source)
    _approve_test_fly_signature(monkeypatch, source)
    _upgrade(target)
    before = source.read_bytes()
    report = run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                           sqlite_test_target_path=str(target))
    assert source.read_bytes() == before
    normalization = report["source_files"]["normalization"]
    assert normalization["variant"] == "fly_production_legacy_20260919"
    assert normalization["excluded_columns"]["song.lyrics"]["count"] == 1
    assert normalization["sidecar_mode"] == "0600"
    assert "lyrics" not in report["before"]["content_digests"]["song"]["column_sha256"]


def test_exact_fly_legacy_fixture_matches_reviewed_structured_contract(tmp_path, monkeypatch):
    source = tmp_path / "fly.sqlite"
    _exact_fly_legacy_fixture(source)
    _approve_test_fly_signature(monkeypatch, source)
    actual = _contract_without_full_ddl_digests(_sqlite_schema_contract(source))
    expected = _contract_without_full_ddl_digests(_production_legacy_contract())
    assert actual == expected
    assert _assert_supported_source_schema(source) == (None, "fly_production_legacy_20260919")


def test_exact_fly_legacy_normalizer_builds_exact_rev01_and_writes_digest_only_evidence(tmp_path):
    source, snapshot, normalized = tmp_path / "fly.sqlite", tmp_path / "snapshot.sqlite", tmp_path / "work.sqlite"
    _exact_fly_legacy_fixture(source)
    source_bytes = source.read_bytes()
    shutil.copy2(source, snapshot)
    evidence = _normalize_production_legacy_copy(snapshot, normalized, _file_fingerprint(snapshot), tmp_path)
    assert source.read_bytes() == source_bytes
    assert _sqlite_schema_contract(normalized) == _baseline_sqlite_contract()
    assert evidence["mapped_content_digests"] == _mapped_sqlite_digests(snapshot, _baseline_sqlite_contract())
    sidecar = tmp_path / "normalization-quarantine-evidence.json"
    assert sidecar.is_file()
    assert "typed_sha256" in sidecar.read_text(encoding="utf-8")


@pytest.mark.parametrize("lyrics", ["not empty", "   "])
def test_exact_fly_legacy_variant_rejects_unmapped_lyrics_without_touching_target(tmp_path, lyrics, monkeypatch):
    source, target = tmp_path / "fly.sqlite", tmp_path / "target.sqlite"
    _exact_fly_legacy_fixture(source)
    _approve_test_fly_signature(monkeypatch, source)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE song SET lyrics = ?", (lyrics,))
    _upgrade(target)
    with pytest.raises(RehearsalError, match="category=unmapped_content table=song column=lyrics count=1"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM song").fetchone()[0] == 0


@pytest.mark.parametrize("table,column", [
    ("media", "processing_attempts"), ("media", "transcoding_status"),
    ("personal_log", "processing_attempts"), ("personal_log", "transcoding_status"),
])
def test_exact_fly_legacy_variant_rejects_null_required_mapped_fields(tmp_path, table, column, monkeypatch):
    source, target = tmp_path / "fly.sqlite", tmp_path / "target.sqlite"
    _exact_fly_legacy_fixture(source)
    _approve_test_fly_signature(monkeypatch, source)
    with sqlite3.connect(source) as connection:
        connection.execute(f'UPDATE "{table}" SET "{column}" = NULL')
    _upgrade(target)
    with pytest.raises(RehearsalError, match=rf"category=required_nonnull table={table} column={column} count=1"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))


def test_exact_fly_legacy_variant_refuses_any_additional_schema_drift(tmp_path, monkeypatch):
    source, target = tmp_path / "fly.sqlite", tmp_path / "target.sqlite"
    _exact_fly_legacy_fixture(source)
    _approve_test_fly_signature(monkeypatch, source)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE INDEX unexpected_legacy_song_title ON song(title)")
    _upgrade(target)
    with pytest.raises(RehearsalError, match="category=schema_contract_drift"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))


@pytest.mark.parametrize("mutation", [
    "generated", "autoincrement", "collation", "deferrable", "check", "strict",
    "expression_index", "trigger",
])
def test_approved_fly_signature_rejects_semantic_ddl_drift_before_normalization(tmp_path, monkeypatch, mutation):
    source, target = tmp_path / "fly.sqlite", tmp_path / "target.sqlite"
    _exact_fly_legacy_fixture(source)
    _approve_test_fly_signature(monkeypatch, source)
    if mutation == "generated":
        with sqlite3.connect(source) as connection:
            connection.execute('ALTER TABLE song ADD COLUMN rogue TEXT GENERATED ALWAYS AS (title) VIRTUAL')
    elif mutation == "autoincrement":
        _replace_song_with_autoincrement_pk(source)
    elif mutation == "collation":
        _replace_master_ddl(source, "song", "title VARCHAR(100) NOT NULL", "title VARCHAR(100) COLLATE NOCASE NOT NULL")
    elif mutation == "deferrable":
        _replace_master_ddl(source, "media", "FOREIGN KEY(song_id) REFERENCES song (id)",
                            "FOREIGN KEY(song_id) REFERENCES song (id) DEFERRABLE INITIALLY DEFERRED")
    elif mutation == "check":
        _replace_master_ddl(source, "song_vote", "CHECK (value IN (-1, 1))",
                            "CHECK (value IN (-1, 2))")
    elif mutation == "strict":
        _append_master_ddl(source, "rehearsal_songs", " STRICT")
    elif mutation == "expression_index":
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE INDEX unexpected_expression_song_title ON song(lower(title))")
    else:
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TRIGGER unexpected_legacy_trigger AFTER UPDATE ON song BEGIN SELECT 1; END")
    _upgrade(target)
    with pytest.raises(RehearsalError, match="category=schema_contract_drift"):
        run_rehearsal(str(source), _url(target), dry_run=True, allow_sqlite_target=True,
                      sqlite_test_target_path=str(target))
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM song").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["column_order", "default", "foreign_key"])
def test_exact_fly_legacy_variant_rejects_near_miss_structural_drift(
        tmp_path, monkeypatch, mutation):
    source, target = tmp_path / "fly.sqlite", tmp_path / "target.sqlite"
    _exact_fly_legacy_fixture(source)
    _approve_test_fly_signature(monkeypatch, source)
    if mutation == "column_order":
        _replace_master_ddl(
            source, "comment",
            "created_at DATETIME,\n            timestamp_seconds FLOAT",
            "timestamp_seconds FLOAT,\n            created_at DATETIME",
        )
    elif mutation == "default":
        _replace_master_ddl(
            source, "media", "is_featured BOOLEAN DEFAULT 0",
            "is_featured BOOLEAN DEFAULT 1",
        )
    else:
        _replace_master_ddl(
            source, "media", "FOREIGN KEY(song_id) REFERENCES song (id)",
            "FOREIGN KEY(song_id) REFERENCES song (id) ON DELETE CASCADE",
        )
    _upgrade(target)
    with pytest.raises(RehearsalError, match="category=schema_contract_drift"):
        run_rehearsal(
            str(source), _url(target), dry_run=True, allow_sqlite_target=True,
            sqlite_test_target_path=str(target),
        )
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM song").fetchone()[0] == 0


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


@pytest.mark.parametrize(
    ('pruned', 'actual_keys', 'missing_required', 'retired_absent', 'retired_present'),
    [
        (False, {'media/한글 원본.mp4', 'media/한글 음원.m4a'}, ['media/한글 720.mp4'], [], []),
        (True, {'media/한글 음원.m4a', 'media/한글 720.mp4'}, [], ['media/한글 원본.mp4'], []),
        (True, {'media/한글 원본.mp4', 'media/한글 음원.m4a', 'media/한글 720.mp4'}, [], [], ['media/한글 원본.mp4']),
    ],
)
def test_media_object_manifest_tracks_lifecycle_expectations(
        tmp_path, pruned, actual_keys, missing_required, retired_absent, retired_present):
    path = tmp_path / 'lifecycle.sqlite'
    _upgrade(path)
    _insert_lifecycle_media(path, pruned=pruned)
    actual = [{'key': key, 'size': 123} for key in actual_keys]
    engine = create_engine(_url(path))
    try:
        with engine.connect() as connection:
            report = reconcile(connection, actual_objects=actual)
    finally:
        engine.dispose()

    entries = {row['role']: row for row in report['object_manifest'] if row['owner']['type'] == 'media'}
    assert entries['original']['expectation'] == ('retired_absent' if pruned else 'required_present')
    assert entries['video_720']['expectation'] == 'required_present'
    assert entries['audio']['expectation'] == 'required_present'
    assert entries['original']['expected_size'] == 999
    assert entries['video_720']['expected_size'] is None
    assert entries['audio']['expected_size'] is None
    comparison = report['object_comparison']
    assert comparison['missing_required'] == missing_required
    assert comparison['retired_absent_confirmed'] == retired_absent
    assert comparison['retired_still_present'] == retired_present
    # Derivative sizes are deliberately incomparable to the original's file_size.
    assert comparison['size_mismatches'] == (
        [] if pruned or 'media/한글 원본.mp4' not in actual_keys else ['media/한글 원본.mp4']
    )


def test_non_media_manifest_entries_remain_required_present(tmp_path):
    path = tmp_path / 'gallery.sqlite'
    _legacy_fixture(path)
    _upgrade(path)
    engine = create_engine(_url(path))
    try:
        with engine.connect() as connection:
            report = reconcile(connection, actual_objects=[])
    finally:
        engine.dispose()
    non_media = [row for row in report['object_manifest'] if row['owner']['type'] != 'media']
    assert non_media
    assert {row['expectation'] for row in non_media} == {'required_present'}
    assert set(report['object_comparison']['missing_required']) >= {row['key'] for row in non_media}


def test_lifecycle_manifest_is_canonical_and_deterministic_across_legacy_fallback(tmp_path):
    canonical, legacy = tmp_path / 'canonical.sqlite', tmp_path / 'legacy.sqlite'
    for path in (canonical, legacy):
        _upgrade(path)
        _insert_lifecycle_media(path, pruned=True)
    engine = create_engine(_url(legacy))
    try:
        with engine.begin() as connection:
            connection.execute(text(
                'UPDATE media SET object_key=NULL, audio_object_key=NULL, video_720_object_key=NULL WHERE id=71'
            ))
    finally:
        engine.dispose()

    manifests = []
    for path in (canonical, legacy):
        engine = create_engine(_url(path))
        try:
            with engine.connect() as connection:
                first = reconcile(connection)['object_manifest']
                second = reconcile(connection)['object_manifest']
                assert first == second
                manifests.append(first)
        finally:
            engine.dispose()
    assert manifests[0] == manifests[1]
    assert '한글 원본.mp4' in json.dumps(manifests[0], ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize(
    ('updates', 'expected_defects'),
    [
        (
            {'audio_filename': None, 'audio_object_key': None},
            {('missing_required_derivative', 'audio')},
        ),
        (
            {
                'original_pruned_at': '2026-09-02',
                'video_720_filename': None,
                'video_720_object_key': None,
            },
            {('missing_required_derivative', 'video_720')},
        ),
        (
            {'original_pruned_at': '2026-09-02', 'transcoding_status': 'failed'},
            {
                ('invalid_pruned_lifecycle', 'audio'),
                ('missing_required_derivative', 'audio'),
            },
        ),
        (
            {
                'original_pruned_at': '2026-09-02',
                'transcoding_status': 'failed',
                'audio_filename': None,
                'audio_object_key': None,
                'video_720_filename': None,
                'video_720_object_key': None,
            },
            {
                ('invalid_pruned_lifecycle', 'audio'),
                ('missing_required_derivative', 'audio'),
                ('missing_required_derivative', 'video_720'),
                ('no_playable_derivative', 'playback'),
            },
        ),
    ],
)
def test_media_manifest_fails_closed_for_impossible_derivative_lifecycle(tmp_path, updates, expected_defects):
    path = tmp_path / 'corrupt-lifecycle.sqlite'
    _upgrade(path)
    _insert_lifecycle_media(path)
    assignments = ', '.join(f'"{column}" = :{column}' for column in sorted(updates))
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            connection.execute(text(f'UPDATE media SET {assignments} WHERE id=71'), updates)
        with engine.connect() as connection:
            first = reconcile(connection)
            second = reconcile(connection)
    finally:
        engine.dispose()

    assert first == second
    defects = {
        (row['kind'], row.get('role'))
        for row in first['object_defects']
        if row.get('table') == 'media' and row.get('id') == 71
    }
    assert expected_defects <= defects
    rendered = json.dumps(first['object_defects'], ensure_ascii=False, sort_keys=True)
    assert '한글 원본.mp4' not in rendered
    assert '한글 음원.m4a' not in rendered
    assert '한글 720.mp4' not in rendered


@pytest.mark.parametrize(
    ('file_type', 'status'),
    [('audio', 'not_required'), ('image', 'not_required'), ('video', 'queued'), ('video', 'failed')],
)
def test_manifest_does_not_require_audio_derivative_outside_completed_video_lifecycle(
        tmp_path, file_type, status):
    path = tmp_path / f'legitimate-{file_type}-{status}.sqlite'
    _upgrade(path)
    _insert_lifecycle_media(path)
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            connection.execute(text(
                'UPDATE media SET file_type=:file_type, transcoding_status=:status, '
                'audio_filename=NULL, audio_object_key=NULL, video_720_filename=NULL, '
                'video_720_object_key=NULL, video_720_completed_at=NULL WHERE id=71'
            ), {'file_type': file_type, 'status': status})
        with engine.connect() as connection:
            report = reconcile(connection)
    finally:
        engine.dispose()
    assert not [row for row in report['object_defects'] if row.get('table') == 'media' and row.get('id') == 71]


def test_broken_pruned_lifecycle_blocks_real_rehearsal_import(tmp_path):
    source, target = tmp_path / 'broken-source.sqlite', tmp_path / 'empty-target.sqlite'
    _upgrade(source)
    _insert_lifecycle_media(source, pruned=True)
    source_engine = create_engine(_url(source))
    try:
        with source_engine.begin() as connection:
            connection.execute(text(
                "UPDATE media SET transcoding_status='failed', audio_filename=NULL, audio_object_key=NULL, "
                'video_720_filename=NULL, video_720_object_key=NULL WHERE id=71'
            ))
    finally:
        source_engine.dispose()
    _upgrade(target)

    with pytest.raises(RehearsalError, match='relationship/object defects'):
        run_rehearsal(
            str(source), _url(target), allow_sqlite_target=True,
            sqlite_test_target_path=str(target),
        )
    target_engine = create_engine(_url(target))
    try:
        with target_engine.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM media')).scalar_one() == 0
    finally:
        target_engine.dispose()


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


@pytest.mark.parametrize(
    ("table", "filename"),
    [
        ("gallery_image", "../escape.mp4"),
        ("gallery_image", "gallery/./photo.png"),
        ("gallery_image", "gallery/%2e/photo.png"),
        ("gallery_image", "gallery//photo.png"),
        ("gallery_image", "https://storage.example.test/object.mp4"),
        ("gallery_image", "media/wrong-prefix.png"),
        ("media", "media/foo/./bar"),
    ],
)
def test_phase4_canonical_key_rules_refuse_traversal_url_and_cross_prefix_values(tmp_path, table, filename):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    _legacy_fixture(source)
    engine = create_engine(_url(source))
    try:
        with engine.begin() as connection:
            if table == "gallery_image":
                connection.execute(text("UPDATE gallery_image SET filename = :filename WHERE id = 12"),
                                   {"filename": filename})
            else:
                connection.execute(text("UPDATE media SET filename = :filename WHERE id = 10"),
                                   {"filename": filename})
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


def test_cli_report_output_writes_unicode_as_utf8_without_shell_round_trip(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite"
    source.touch()
    output = tmp_path / "retained-report.json"
    key = "gallery/한글 사진 🎵.png"
    report = {"before": {"object_manifest": [{"key": key}]}}
    import tools.legacy_rehearsal.rehearsal as rehearsal

    def publish_report(*args, **kwargs):
        kwargs["report_publisher"](report)
        return report

    monkeypatch.setattr(rehearsal, "run_rehearsal", publish_report)

    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://user:value@host/db",
        "--expected-target-host", "host", "--expected-target-database", "db",
        "--expected-target-schema", "public", "--report-output", str(output),
    ])

    assert result.exit_code == 0
    payload = output.read_bytes()
    assert payload.endswith(b"\n")
    assert b"\\u" not in payload
    assert json.loads(payload.decode("utf-8"))["before"]["object_manifest"][0]["key"] == key
    assert "\ufffd" not in payload.decode("utf-8")
    assert key not in result.output
    assert f"sha256={hashlib.sha256(payload).hexdigest()}" in result.output
    assert list(tmp_path.glob(f".{output.name}.*")) == []


def test_cli_report_output_is_retained_for_successful_import(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    output = tmp_path / "retained-report.json"
    _legacy_fixture(source)
    _upgrade(target)

    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", _url(target),
        "--allow-sqlite-target", "--sqlite-test-target-path", str(target),
        "--report-output", str(output),
    ])

    assert result.exit_code == 0
    payload = output.read_bytes()
    report = json.loads(payload.decode("utf-8"))
    assert report["after"]["table_counts"]["song"] == 1
    assert f"sha256={hashlib.sha256(payload).hexdigest()}" in result.output
    assert list(tmp_path.glob(f".{output.name}.*")) == []


def test_cli_report_output_probes_publication_before_rehearsal(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite"
    source.touch()
    output = tmp_path / "retained-report.json"
    import tools.legacy_rehearsal.rehearsal as rehearsal
    called = []
    monkeypatch.setattr(rehearsal, "run_rehearsal", lambda *args, **kwargs: called.append(True))
    monkeypatch.setattr(rehearsal.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("hard links unavailable")))

    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://user:value@host/db",
        "--expected-target-host", "host", "--expected-target-database", "db",
        "--expected-target-schema", "public", "--report-output", str(output),
    ])

    assert result.exit_code != 0
    assert "rehearsal_failed correlation_id=" in result.output
    assert "hard links unavailable" not in result.output
    assert called == []
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*")) == []


def test_report_probe_collision_is_not_deleted(tmp_path, monkeypatch):
    output = tmp_path / "retained-report.json"
    probe = tmp_path / f".{output.name}.fixed.probe"
    probe.write_bytes(b"other-process")
    import tools.legacy_rehearsal.rehearsal as rehearsal
    monkeypatch.setattr(rehearsal.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))

    with pytest.raises(FileExistsError):
        _ReservedReportOutput(output)

    assert probe.read_bytes() == b"other-process"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []


def test_report_probe_replacement_is_not_deleted(tmp_path, monkeypatch):
    output = tmp_path / "retained-report.json"
    import tools.legacy_rehearsal.rehearsal as rehearsal
    real_link = rehearsal.os.link
    replaced = []

    def replace_probe(source, destination):
        real_link(source, destination)
        if str(destination).endswith(".probe"):
            destination = Path(destination)
            destination.unlink()
            destination.write_bytes(b"other-process")
            replaced.append(destination)

    monkeypatch.setattr(rehearsal.os, "link", replace_probe)

    with pytest.raises(RehearsalError, match="probe changed identity"):
        _ReservedReportOutput(output)

    assert len(replaced) == 1
    assert replaced[0].read_bytes() == b"other-process"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []


def test_report_temp_path_replacement_is_not_deleted(tmp_path):
    output = tmp_path / "retained-report.json"
    reservation = _ReservedReportOutput(output)
    visible_temp = reservation.temporary
    assert visible_temp is not None
    # Windows prevents pathname replacement while the mkstemp handle is open;
    # close only that handle to model replacement immediately before cleanup.
    import tools.legacy_rehearsal.rehearsal as rehearsal
    rehearsal.os.close(reservation.descriptor)
    reservation.descriptor = -1
    visible_temp.unlink()
    visible_temp.write_bytes(b"other-process")

    reservation.abort()

    assert visible_temp.read_bytes() == b"other-process"
    assert not output.exists()


def test_cli_report_output_cleans_reservation_when_rehearsal_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite"
    source.touch()
    output = tmp_path / "retained-report.json"
    import tools.legacy_rehearsal.rehearsal as rehearsal
    monkeypatch.setattr(rehearsal, "run_rehearsal", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("database failure")))

    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://user:value@host/db",
        "--expected-target-host", "host", "--expected-target-database", "db",
        "--expected-target-schema", "public", "--report-output", str(output),
    ])

    assert result.exit_code != 0
    assert "database failure" not in result.output
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*")) == []


def test_report_destination_race_does_not_overwrite_and_rolls_back_import(tmp_path):
    source, target = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    output = tmp_path / "retained-report.json"
    _legacy_fixture(source)
    _upgrade(target)
    reservation = _ReservedReportOutput(output)
    output.write_bytes(b"racer")

    with pytest.raises(RehearsalError, match="rehearsal failed"):
        run_rehearsal(
            str(source), _url(target), allow_sqlite_target=True,
            sqlite_test_target_path=str(target), report_publisher=reservation.publish,
        )
    reservation.abort()

    assert output.read_bytes() == b"racer"
    assert list(tmp_path.glob(f".{output.name}.*")) == []
    engine = create_engine(_url(target))
    try:
        with engine.connect() as connection:
            tables = [name for name in inspect(connection).get_table_names() if name != "alembic_version"]
            assert all(connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one() == 0
                       for name in tables)
    finally:
        engine.dispose()


def test_report_payload_is_deterministic_for_non_ascii_manifest_keys():
    key = "media/한글 음원 🎵.m4a"
    first = {
        "before": {
            "object_manifest": [
                {"key": key, "owner": {"type": "media", "id": 7, "band_id": 1}},
            ],
            "table_counts": {"media": 1},
        },
        "dry_run": True,
    }
    second = {
        "dry_run": True,
        "before": {
            "table_counts": {"media": 1},
            "object_manifest": [
                {"owner": {"band_id": 1, "id": 7, "type": "media"}, "key": key},
            ],
        },
    }

    first_payload = _report_payload(first)
    second_payload = _report_payload(second)

    assert first_payload == second_payload
    assert hashlib.sha256(first_payload).digest() == hashlib.sha256(second_payload).digest()
    assert json.loads(first_payload.decode("utf-8"))["before"]["object_manifest"][0]["key"] == key
    assert "\ufffd" not in first_payload.decode("utf-8")


def test_cli_report_output_refuses_to_overwrite_existing_evidence(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite"
    source.touch()
    output = tmp_path / "retained-report.json"
    output.write_bytes(b"retained")
    import tools.legacy_rehearsal.rehearsal as rehearsal
    called = []
    monkeypatch.setattr(rehearsal, "run_rehearsal", lambda *args, **kwargs: called.append(True))

    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://user:value@host/db",
        "--expected-target-host", "host", "--expected-target-database", "db",
        "--expected-target-schema", "public", "--report-output", str(output),
    ])

    assert result.exit_code != 0
    assert "rehearsal_failed correlation_id=" in result.output
    assert output.read_bytes() == b"retained"
    assert called == []
