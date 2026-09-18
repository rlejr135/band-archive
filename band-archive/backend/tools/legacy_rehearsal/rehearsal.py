"""Fail-closed, offline legacy database migration rehearsal.

This module deliberately has no application entry point and no storage writer.
It is intended for a *copy* of a stopped Fly SQLite volume and an empty,
explicitly-labelled staging database already at Alembic head.  In particular it
does not infer a DATABASE_URL, talk to Supabase/Fly, or make R2 mutations.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import base64
import math
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import click
from alembic.config import Config
from alembic.script import ScriptDirectory
from flask_migrate import upgrade
from sqlalchemy import Boolean, Date, DateTime, MetaData, Time, create_engine, inspect, select, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from migration_bootstrap import (
    MIGRATIONS_DIRECTORY, assert_upgrade_preflight, create_migration_app,
    stamp_existing_current_schema,
)
import tenant_backfill
from tenant_backfill import TenantBackfillError, _canonical_key, apply as apply_tenant_backfill
from extensions import db


class RehearsalError(RuntimeError):
    """A refusal intended to be acted on by an operator, not worked around."""


_SECRET = re.compile(r"(?i)(token|secret|password|credential|authorization|signature|upload_id|r2_upload_id|etag)")
_MANAGED_EXCLUDE = {"alembic_version"}
_QUARANTINED_TABLES = {
    "multipart_upload_session", "multipart_upload_part",
    "personal_log_multipart_upload_session", "personal_log_multipart_upload_part",
}
_AUDIT_TRIGGER_SQL = {
    "audit_event_no_update": "create trigger audit_event_no_update before update on audit_event begin select raise(abort, 'audit_event is append-only'); end",
    "audit_event_no_delete": "create trigger audit_event_no_delete before delete on audit_event begin select raise(abort, 'audit_event is append-only'); end",
}
_AUDIT_TRIGGER_REVISIONS = {"20260916_04", "20260916_05"}
_OBJECT_FIELDS = (
    ("song", "id", "band_id", "sheet_music_object_key", "sheet_music", "media", None),
    ("media", "id", "band_id", "object_key", "filename", "media", "file_size"),
    ("media", "id", "band_id", "audio_object_key", "audio_filename", "media", None),
    ("media", "id", "band_id", "video_720_object_key", "video_720_filename", "media", None),
    ("personal_log", "id", "band_id", "object_key", "filename", "personal_logs", "file_size"),
    ("personal_log", "id", "band_id", "audio_object_key", "audio_filename", "personal_logs", None),
    ("gallery_image", "id", "band_id", "object_key", "filename", "gallery", "file_size"),
    ("multipart_upload_session", "id", "band_id", "object_key", None, "", "declared_bytes"),
    ("personal_log_multipart_upload_session", "id", "band_id", "object_key", None, "", "declared_bytes"),
)


def _head_revisions() -> set[str]:
    config = Config(str(MIGRATIONS_DIRECTORY / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    return set(ScriptDirectory.from_config(config).get_heads())


def _known_revisions() -> set[str]:
    config = Config(str(MIGRATIONS_DIRECTORY / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    return {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}


def redact(value: Any) -> Any:
    """Remove connection credentials and opaque capabilities from reports/errors."""
    if isinstance(value, dict):
        return {str(k): ("[redacted]" if _SECRET.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and ("://" in value or _SECRET.search(value)):
        return "[redacted]"
    return value


def safe_target_identity(database_url: str) -> dict[str, str]:
    """Useful, non-secret identity for manifests and error reports."""
    url = make_url(database_url)
    host = url.host or "local"
    fingerprint = hashlib.sha256(f"{url.drivername}|{host}|{url.database or ''}".encode()).hexdigest()[:16]
    return {"dialect": url.get_backend_name(), "host_fingerprint": fingerprint}


def _file_fingerprint(path: Path) -> dict[str, Any]:
    """Stable evidence that does not reveal a local path or any DB content."""
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _validate_target_url(database_url: str, *, expected_target_host: str | None,
                         expected_target_database: str | None,
                         expected_target_schema: str | None,
                         allow_sqlite_target: bool,
                         sqlite_test_target_path: str | None) -> dict[str, str]:
    """Prove target identity from parsed URL/options, never a display label."""
    url = make_url(database_url)
    dialect = url.get_backend_name()
    if dialect == "sqlite":
        if not allow_sqlite_target or not sqlite_test_target_path:
            raise RehearsalError("SQLite target needs --allow-sqlite-target and an explicit test target path")
        actual = _sqlite_path(database_url)
        expected = Path(sqlite_test_target_path).expanduser().resolve()
        if actual != expected:
            raise RehearsalError("SQLite target path does not match the explicit test target path")
        return {"dialect": "sqlite", "schema": "sqlite"}
    if dialect != "postgresql":
        raise RehearsalError("target must be PostgreSQL; SQLite is test-only")
    if not expected_target_host or not expected_target_database or not expected_target_schema:
        raise RehearsalError("PostgreSQL target requires expected host, database, and schema")
    actual_host, actual_database = (url.host or "").lower(), url.database or ""
    if actual_host in {"localhost", "127.0.0.1", "::1"}:
        raise RehearsalError("localhost is not an allowed PostgreSQL rehearsal target")
    if actual_host != expected_target_host.lower() or actual_database != expected_target_database:
        raise RehearsalError("PostgreSQL target host or database does not match the explicit expected identity")
    return {"dialect": "postgresql", "schema": expected_target_schema}


def _sqlite_path(database_url_or_path: str) -> Path:
    raw = str(database_url_or_path)
    if raw.startswith("sqlite:///"):
        raw = raw.removeprefix("sqlite:///")
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise RehearsalError("source must be an explicit SQLite database file")
    if not path.is_file():
        raise RehearsalError("source SQLite snapshot does not exist")
    return path


def preflight_sqlite_snapshot(source: str) -> Path:
    """Perform only read-only source checks before copying it into a temp dir."""
    path = _sqlite_path(source)
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.execute("PRAGMA query_only = ON")
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RehearsalError("source SQLite integrity_check failed")
            # WAL snapshots must be checkpointed by the snapshot procedure. A
            # live WAL would make copying the main db alone inconsistent.
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
            if journal == "wal" and Path(f"{path}-wal").exists():
                raise RehearsalError("source has a live WAL; create a consistent offline snapshot first")
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error as error:
        raise RehearsalError("source SQLite preflight could not read the snapshot") from error
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RehearsalError("source SQLite snapshot changed during preflight")
    return path


def _source_schema_fingerprint(path: Path) -> dict[str, Any]:
    """Read schema names only; never print source rows or opaque user data."""
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = sorted(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ))
        columns = {
            table: sorted(row[1] for row in conn.execute(f'PRAGMA table_info("{table}")'))
            for table in tables
        }
    encoded = json.dumps({"tables": tables, "columns": columns}, sort_keys=True, separators=(",", ":"))
    return {"tables": tables, "columns": columns, "checksum": hashlib.sha256(encoded.encode()).hexdigest()}


def _canonical_sqlite_trigger_sql(value: str | None) -> str:
    """Normalize only presentation differences in SQLite's stored trigger DDL."""
    return re.sub(r"\s+", " ", (value or "").strip()).replace('"', '').replace('`', '').casefold()


def _assert_exact_sqlite_trigger_contract(path: Path, revision: str | None) -> None:
    """Reject extra or altered trigger DDL before source migration can run."""
    expected = _AUDIT_TRIGGER_SQL if revision in _AUDIT_TRIGGER_REVISIONS else {}
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        actual = {
            name: _canonical_sqlite_trigger_sql(sql)
            for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
        }
    normalized_expected = {name: _canonical_sqlite_trigger_sql(sql) for name, sql in expected.items()}
    if actual != normalized_expected:
        raise RehearsalError("source SQLite trigger contract does not match its recorded revision")


def _assert_supported_source_schema(path: Path) -> str | None:
    """Reject partial historical variants before Alembic could alter them.

    The checked-in baseline is the only pre-Alembic layout this rehearsal can
    safely stamp.  Older deployments with, for example, ``practice_log`` or
    ``song.lyrics`` need an explicit, reviewed normalizer; guessing would lose
    content and is not a migration rehearsal.
    """
    fingerprint = _source_schema_fingerprint(path)
    tables = set(fingerprint["tables"])
    if "alembic_version" in tables:
        uri = f"file:{path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            versions = sorted(row[0] for row in conn.execute("SELECT version_num FROM alembic_version"))
        if len(versions) != 1 or versions[0] not in _known_revisions():
            raise RehearsalError(
                "unsupported Alembic source revision; schema fingerprint " + fingerprint["checksum"][:16]
                + ". Do not stamp or import it; restore a known snapshot or review an explicit normalizer."
            )
        return versions[0]
    from migration_bootstrap import BASELINE_TABLE_NAMES
    required = set(BASELINE_TABLE_NAMES)
    missing = sorted(required - tables)
    unsupported = sorted(tables - required)
    if missing or unsupported:
        # Keep the fingerprint (schema-only) actionable without leaking rows.
        raise RehearsalError(
            "unsupported pre-Alembic SQLite schema fingerprint " + fingerprint["checksum"][:16]
            + "; missing=" + ",".join(missing or ["none"])
            + "; extra=" + ",".join(unsupported or ["none"])
            + ". Do not stamp or import it. Build and review an explicit variant normalizer first."
        )
    return None


def _copy_and_backfill(source: str, temporary_dir: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    source_path = preflight_sqlite_snapshot(source)
    source_revision = _assert_supported_source_schema(source_path)
    _assert_exact_sqlite_trigger_contract(source_path, source_revision)
    source_before = _file_fingerprint(source_path)
    snapshot_path = temporary_dir / "legacy-rehearsal-snapshot.sqlite"
    working_path = temporary_dir / "legacy-rehearsal-working.sqlite"
    shutil.copy2(source_path, snapshot_path)
    snapshot_fingerprint = _file_fingerprint(snapshot_path)
    if source_before != _file_fingerprint(source_path) or source_before["sha256"] != snapshot_fingerprint["sha256"]:
        raise RehearsalError("source SQLite snapshot changed while being copied")
    # Keep the copied snapshot immutable as operator evidence. Only its second
    # copy is upgraded/backfilled.
    shutil.copy2(snapshot_path, working_path)
    working_before = _file_fingerprint(working_path)
    if snapshot_fingerprint["sha256"] != working_before["sha256"]:
        raise RehearsalError("working SQLite copy does not match immutable snapshot")
    source_url = f"sqlite:///{working_path.as_posix()}"
    app = create_migration_app(source_url)
    # A rehearsal must be byte/report stable. Phase 4's import-only rows have
    # no historic timestamp, so use the immutable snapshot mtime rather than
    # wall clock time when it creates that operator evidence on the work copy.
    original_backfill_now = tenant_backfill._now
    tenant_backfill._now = lambda: datetime.fromtimestamp(source_before["mtime_ns"] / 1_000_000_000, timezone.utc)
    try:
        with app.app_context():
            inspector = inspect(db.engine)
            if "alembic_version" not in inspector.get_table_names():
                stamp_existing_current_schema()
            else:
                # This validates the exact metadata, columns, indexes,
                # constraints and revision-specific triggers before Alembic is
                # allowed to touch a known stamped source.
                assert_upgrade_preflight()
            upgrade(directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                report = apply_tenant_backfill(connection)
    except Exception as error:
        # Never include driver SQL, parameters, paths, or a DB URL.
        raise RehearsalError("source copy migration/backfill failed") from error
    finally:
        tenant_backfill._now = original_backfill_now
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    if snapshot_fingerprint != _file_fingerprint(snapshot_path):
        raise RehearsalError("immutable copied snapshot changed during rehearsal")
    return working_path, redact(report), {
        "source_before": source_before,
        "immutable_snapshot": snapshot_fingerprint,
        "working_copy_before": working_before,
    }


def _table_names(connection: Connection) -> list[str]:
    return sorted(name for name in inspect(connection).get_table_names() if name not in _MANAGED_EXCLUDE)


def _read_metadata(connection: Connection) -> MetaData:
    metadata = MetaData()
    metadata.reflect(bind=connection)
    for name in list(metadata.tables):
        if name in _MANAGED_EXCLUDE:
            metadata.remove(metadata.tables[name])
    return metadata


def _ordered_tables(metadata: MetaData) -> list:
    # SQLAlchemy's sorter handles association tables after both parents. The
    # only self-reference here is comment.parent_id and stays in one table.
    return list(metadata.sorted_tables)


def _scalar(connection: Connection, statement: str, **params: Any) -> Any:
    return connection.execute(text(statement), params).scalar_one()


def _has_columns(connection: Connection, table: str, columns: Iterable[str]) -> bool:
    found = {item["name"] for item in inspect(connection).get_columns(table)}
    return set(columns) <= found


def _row_checksum(rows: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(redact(row), default=str, sort_keys=True, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _stable_value(value: Any) -> bytes:
    """Type-tagged canonical serialization used only inside SHA-256 digests."""
    if value is None:
        return b"n"
    if isinstance(value, bool):
        return b"b1" if value else b"b0"
    if isinstance(value, bytes):
        return b"y" + base64.b64encode(value)
    if isinstance(value, str):
        return b"s" + value.encode("utf-8")
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii")
    if isinstance(value, Decimal):
        return b"d" + format(value, "f").encode("ascii")
    if isinstance(value, float):
        if math.isnan(value):
            return b"fNaN"
        if math.isinf(value):
            return b"f+Inf" if value > 0 else b"f-Inf"
        return b"f" + value.hex().encode("ascii")
    if isinstance(value, datetime):
        return b"z" + value.isoformat(timespec="microseconds").encode("utf-8")
    if isinstance(value, date):
        return b"D" + value.isoformat().encode("ascii")
    if isinstance(value, time):
        return b"T" + value.isoformat(timespec="microseconds").encode("ascii")
    return b"x" + type(value).__name__.encode("ascii", "backslashreplace") + b":" + str(value).encode("utf-8")


def _stable_column_value(value: Any, column: Any) -> bytes:
    """Normalize dialect return types while retaining the stored value."""
    if value is None:
        return _stable_value(value)
    if isinstance(column.type, Boolean):
        return b"b1" if bool(value) else b"b0"
    if isinstance(column.type, DateTime):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return _stable_value(value)
    if isinstance(column.type, Date) and not isinstance(column.type, DateTime):
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                pass
        return _stable_value(value)
    if isinstance(column.type, Time):
        if isinstance(value, str):
            try:
                value = time.fromisoformat(value)
            except ValueError:
                pass
        return _stable_value(value)
    return _stable_value(value)


def _content_digests(connection: Connection) -> dict[str, dict[str, Any]]:
    """Hash every imported, non-excluded column without exposing row values."""
    metadata = _read_metadata(connection)
    result = {}
    for table in _ordered_tables(metadata):
        if table.name in _QUARANTINED_TABLES:
            continue
        columns = sorted(table.columns, key=lambda column: column.name)
        pk_columns = list(table.primary_key.columns) or columns
        digest, count = hashlib.sha256(), 0
        column_digests = {column.name: hashlib.sha256() for column in columns}
        for row in connection.execute(select(*columns).order_by(*pk_columns)).mappings():
            digest.update(b"table\0" + table.name.encode("utf-8") + b"\0")
            for column in columns:
                encoded = _stable_column_value(row[column.name], column)
                digest.update(column.name.encode("utf-8") + b"\0" + str(len(encoded)).encode("ascii") + b":" + encoded)
                column_digests[column.name].update(str(len(encoded)).encode("ascii") + b":" + encoded + b"\n")
            digest.update(b"\n")
            count += 1
        result[table.name] = {"count": count, "sha256": digest.hexdigest(),
                              "column_sha256": {name: item.hexdigest() for name, item in column_digests.items()}}
    return result


def _quarantine_manifest(connection: Connection) -> dict[str, dict[str, Any]]:
    """Audit omitted live multipart state without exporting capabilities."""
    metadata = _read_metadata(connection)
    result = {}
    for table in sorted(_QUARANTINED_TABLES & set(metadata.tables)):
        obj = metadata.tables[table]
        primary_key = list(obj.primary_key.columns) or list(obj.columns)
        rows = connection.execute(select(*primary_key).order_by(*primary_key)).mappings().all()
        result[table] = {"count": len(rows), "primary_key_checksum": _row_checksum(rows),
                         "reason": "legacy_live_multipart_state_omitted"}
    return result


def _fk_orphans(connection: Connection, table: str) -> list[dict[str, Any]]:
    inspector = inspect(connection)
    defects = []
    for fk in inspector.get_foreign_keys(table):
        local = fk.get("constrained_columns") or []
        remote = fk.get("referred_columns") or []
        remote_table = fk.get("referred_table")
        if len(local) != 1 or len(remote) != 1 or not remote_table:
            continue
        local_col, remote_col = local[0], remote[0]
        # Names originate from reflected schema; no user-supplied SQL names.
        count = _scalar(connection, f'SELECT count(*) FROM "{table}" child LEFT JOIN "{remote_table}" parent '
                            f'ON child."{local_col}" = parent."{remote_col}" '
                            f'WHERE child."{local_col}" IS NOT NULL AND parent."{remote_col}" IS NULL')
        if count:
            defects.append({"relationship": f"{table}.{local_col}->{remote_table}.{remote_col}", "orphans": count})
    return defects


def _vote_summary(connection: Connection, table: str, target: str) -> dict[str, Any]:
    if not _has_columns(connection, table, (target, "value")):
        return {}
    rows = connection.execute(text(
        f'SELECT "{target}" AS target_id, count(*) AS votes, coalesce(sum(value), 0) AS calculated_score '
        f'FROM "{table}" GROUP BY "{target}" ORDER BY "{target}"'
    )).mappings().all()
    cached_table = "song" if table == "song_vote" else "media"
    cached_rows = connection.execute(text(
        f'SELECT item.id AS target_id, item.vote_score AS cached_score, '
        f'coalesce(sum(vote.value), 0) AS calculated_score '
        f'FROM "{cached_table}" item LEFT JOIN "{table}" vote ON vote."{target}" = item.id '
        f'GROUP BY item.id, item.vote_score ORDER BY item.id'
    )).mappings().all()
    mismatches = [row["target_id"] for row in cached_rows if row["cached_score"] != row["calculated_score"]]
    return {"rows": len(rows), "checksum": _row_checksum(rows), "aggregate": [dict(row) for row in rows],
            "cached_score_mismatches": mismatches}


def _comment_summary(connection: Connection) -> dict[str, Any]:
    if "comment" not in _table_names(connection):
        return {}
    rows = connection.execute(text(
        "SELECT id, parent_id, media_id, personal_log_id, band_id FROM comment ORDER BY id"
    )).mappings().all()
    ids = {row["id"] for row in rows}
    roots = targets = missing_parent = 0
    for row in rows:
        if row["parent_id"] is None:
            roots += 1
            targets += int((row["media_id"] is not None) + (row["personal_log_id"] is not None))
        elif row["parent_id"] not in ids:
            missing_parent += 1
    return {"count": len(rows), "roots": roots, "root_target_fields": targets,
            "missing_parents": missing_parent, "checksum": _row_checksum(rows)}


def _object_manifest(connection: Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tables = set(_table_names(connection))
    manifest, defects = [], []
    seen: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for table, pk, band, key_column, fallback, prefix, size in _OBJECT_FIELDS:
        if table in _QUARANTINED_TABLES:
            continue
        if table not in tables or not _has_columns(connection, table, (pk, band, key_column)):
            continue
        columns = [pk, band, key_column] + ([fallback] if fallback else []) + ([size] if size else [])
        query_columns = ", ".join(f'"{col}"' for col in columns)
        for row in connection.execute(text(f'SELECT {query_columns} FROM "{table}" ORDER BY "{pk}"')).mappings():
            key = row[key_column] or (row[fallback] if fallback else None)
            if not key:
                continue
            unresolved = []
            canonical = _canonical_key(key, prefix, f"{table}:{row[pk]}:{key_column}", unresolved)
            if unresolved or canonical is None:
                defects.append({"kind": "invalid_or_cross_prefix_key", "table": table, "id": row[pk]})
                continue
            entry = {"key": canonical, "owner": {"type": table, "id": row[pk], "band_id": row[band]},
                     "expected_size": row[size] if size else None}
            manifest.append(entry)
            seen[canonical].append(entry)
    for key, entries in sorted(seen.items()):
        if len(entries) > 1:
            defects.append({"kind": "duplicate_key", "key": key, "owners": len(entries)})
            bands = {entry["owner"]["band_id"] for entry in entries}
            if len(bands) > 1:
                defects.append({"kind": "cross_band_key", "key": key, "bands": len(bands)})
    manifest.sort(key=lambda row: (row["key"], row["owner"]["type"], str(row["owner"]["id"])))
    return manifest, defects


def reconcile(connection: Connection, actual_objects: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a stable, credential-free DB/object manifest without storage I/O."""
    tables = _table_names(connection)
    metadata = _read_metadata(connection)
    counts, pks, orphans = {}, {}, []
    for table in tables:
        counts[table] = _scalar(connection, f'SELECT count(*) FROM "{table}"')
        obj = metadata.tables[table]
        pk_columns = list(obj.primary_key.columns)
        if pk_columns:
            rows = connection.execute(select(*pk_columns).order_by(*pk_columns)).mappings().all()
            pks[table] = {"count": len(rows), "checksum": _row_checksum(rows)}
        else:
            pks[table] = {"count": counts[table], "checksum": "no_primary_key"}
        orphans.extend(_fk_orphans(connection, table))
    visibility = {}
    for table in tables:
        columns = {item["name"] for item in inspect(connection).get_columns(table)}
        if "band_id" in columns:
            visibility[table] = {"null_band_id": _scalar(connection, f'SELECT count(*) FROM "{table}" WHERE band_id IS NULL')}
            bands = connection.execute(text(
                f'SELECT band_id, count(*) AS rows FROM "{table}" GROUP BY band_id ORDER BY band_id'
            )).mappings().all()
            visibility[table]["band_counts_checksum"] = _row_checksum(bands)
            if "visibility" in columns:
                visibility[table]["null_visibility"] = _scalar(connection, f'SELECT count(*) FROM "{table}" WHERE visibility IS NULL')
    object_manifest, object_defects = _object_manifest(connection)
    association_rows = []
    if "rehearsal_songs" in tables:
        association_rows = connection.execute(text(
            "SELECT rehearsal_id, song_id FROM rehearsal_songs ORDER BY rehearsal_id, song_id"
        )).mappings().all()
    session_rows = []
    for table, session_column in (("multipart_upload_part", "session_id"),
                                  ("personal_log_multipart_upload_part", "session_id")):
        if table in tables:
            session_rows.extend(connection.execute(text(
                f'SELECT "{session_column}" AS session_id, count(*) AS parts FROM "{table}" '
                f'GROUP BY "{session_column}" ORDER BY "{session_column}"'
            )).mappings().all())
    report = {
        "table_counts": counts, "primary_keys": pks, "foreign_key_orphans": sorted(orphans, key=lambda row: row["relationship"]),
        "content_digests": _content_digests(connection),
        "multipart_quarantine": _quarantine_manifest(connection),
        "song_votes": _vote_summary(connection, "song_vote", "song_id"),
        "media_votes": _vote_summary(connection, "media_vote", "media_id"),
        "comments": _comment_summary(connection), "visibility_and_scope": visibility,
        "rehearsal_song_associations": {"count": counts.get("rehearsal_songs", 0),
                                        "checksum": _row_checksum(association_rows)},
        "multipart_relations": {
            "media_sessions": counts.get("multipart_upload_session", 0),
            "media_parts": counts.get("multipart_upload_part", 0),
            "log_sessions": counts.get("personal_log_multipart_upload_session", 0),
            "log_parts": counts.get("personal_log_multipart_upload_part", 0),
            "session_part_checksum": _row_checksum(session_rows),
        },
        "object_manifest": object_manifest, "object_defects": object_defects,
    }
    if actual_objects is not None:
        expected = {row["key"]: row for row in object_manifest}
        actual = {str(row.get("key")): redact(row) for row in actual_objects if row.get("key")}
        report["object_comparison"] = {
            "db_missing_objects": sorted(key for key in expected if key not in actual),
            "orphan_objects": sorted(key for key in actual if key not in expected),
            "size_mismatches": sorted(key for key, row in expected.items()
                                      if row["expected_size"] is not None and key in actual
                                      and actual[key].get("size") not in (None, row["expected_size"])),
        }
    stable = json.dumps(redact(report), default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["checksum"] = hashlib.sha256(stable.encode()).hexdigest()
    return redact(report)


def _assert_target_ready(engine: Engine, identity: dict[str, str]) -> None:
    with engine.connect() as connection:
        if identity["dialect"] == "postgresql":
            actual_schema = connection.execute(text("SELECT current_schema()")).scalar_one()
            if actual_schema != identity["schema"]:
                raise RehearsalError("PostgreSQL target schema does not match the explicit expected schema")
        tables = _table_names(connection)
        versions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() if "alembic_version" in inspect(connection).get_table_names() else []
        if set(versions) != _head_revisions():
            raise RehearsalError("target must already be at the current Alembic head")
        nonempty = {table: _scalar(connection, f'SELECT count(*) FROM "{table}"') for table in tables}
        nonempty = {table: count for table, count in nonempty.items() if count}
        if nonempty:
            raise RehearsalError("target must be empty; this tool refuses repeats/nonempty databases")


def _copy_rows(source: Connection, target: Connection) -> dict[str, int]:
    source_metadata = _read_metadata(source)
    target_metadata = _read_metadata(target)
    if set(source_metadata.tables) != set(target_metadata.tables):
        raise RehearsalError("source copy and target managed table sets differ")
    inserted = {}
    for source_table in _ordered_tables(source_metadata):
        name = source_table.name
        if name in _QUARANTINED_TABLES:
            inserted[name] = 0
            continue
        target_table = target_metadata.tables[name]
        source_columns = [column.name for column in source_table.columns]
        target_columns = {column.name for column in target_table.columns}
        if set(source_columns) != target_columns:
            raise RehearsalError(f"column mismatch for managed table {name}")
        pk_columns = list(source_table.primary_key.columns)
        statement = select(*source_table.columns)
        if pk_columns:
            statement = statement.order_by(*pk_columns)
        rows = [dict(row) for row in source.execute(statement).mappings()]
        if rows:
            target.execute(target_table.insert(), rows)
        inserted[name] = len(rows)
    return inserted


def _reset_postgres_sequences(connection: Connection, metadata: MetaData) -> None:
    if connection.dialect.name != "postgresql":
        return
    preparer = connection.dialect.identifier_preparer
    for table in metadata.sorted_tables:
        pks = list(table.primary_key.columns)
        if len(pks) != 1:
            continue
        pk = pks[0]
        sequence = connection.execute(text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                                      {"table_name": table.name, "column_name": pk.name}).scalar_one()
        if sequence:
            quoted_table, quoted_pk = preparer.quote(table.name), preparer.quote(pk.name)
            maximum = connection.execute(text(f"SELECT max({quoted_pk}) FROM {quoted_table}")).scalar_one()
            if maximum is not None:
                connection.execute(text("SELECT setval(:sequence, :value, true)"), {"sequence": sequence, "value": maximum})


def run_rehearsal(source_sqlite: str, target_url: str, *, expected_target_host: str | None = None,
                  expected_target_database: str | None = None, expected_target_schema: str | None = None,
                  dry_run: bool = False, allow_sqlite_target: bool = False,
                  sqlite_test_target_path: str | None = None) -> dict[str, Any]:
    """Copy/migrate/backfill an SQLite copy and optionally import it once.

    The target is always checked *before* the source work, is never upgraded by
    this tool, and all inserts plus sequence updates occur in one transaction.
    """
    identity = _validate_target_url(
        target_url, expected_target_host=expected_target_host,
        expected_target_database=expected_target_database, expected_target_schema=expected_target_schema,
        allow_sqlite_target=allow_sqlite_target, sqlite_test_target_path=sqlite_test_target_path,
    )
    source_path = _sqlite_path(source_sqlite)
    engine = create_engine(target_url, future=True, hide_parameters=True)
    try:
        _assert_target_ready(engine, identity)
        with tempfile.TemporaryDirectory(prefix="band-archive-legacy-rehearsal-") as directory:
            source_fingerprint = _source_schema_fingerprint(preflight_sqlite_snapshot(source_sqlite))
            source_copy, backfill, source_files = _copy_and_backfill(source_sqlite, Path(directory))
            source_engine = create_engine(f"sqlite:///{source_copy.as_posix()}", future=True)
            try:
                with source_engine.connect() as source_connection:
                    before = reconcile(source_connection)
                    result = {"target": safe_target_identity(target_url), "source_schema": source_fingerprint,
                              "source_files": source_files, "backfill": backfill, "before": before,
                              "multipart_quarantine": before["multipart_quarantine"], "dry_run": dry_run}
                    if dry_run:
                        result["source_files"]["source_after"] = _file_fingerprint(source_path)
                        if result["source_files"]["source_before"] != result["source_files"]["source_after"]:
                            raise RehearsalError("source SQLite snapshot changed during rehearsal")
                        return result
                    with engine.begin() as target_connection:
                        inserted = _copy_rows(source_connection, target_connection)
                        _reset_postgres_sequences(target_connection, _read_metadata(target_connection))
                        after = reconcile(target_connection)
                        retained = lambda values: {key: value for key, value in values.items() if key not in _QUARANTINED_TABLES}
                        if retained(before["table_counts"]) != retained(after["table_counts"]) or retained(before["primary_keys"]) != retained(after["primary_keys"]):
                            raise RehearsalError("post-import table count or primary-key mismatch; transaction rolled back")
                        if before["content_digests"] != after["content_digests"]:
                            differing_tables = sorted(
                                table for table in before["content_digests"]
                                if before["content_digests"].get(table) != after["content_digests"].get(table)
                            )
                            differing_columns = sorted(
                                f"{table}.{column}" for table in differing_tables
                                for column in before["content_digests"][table]["column_sha256"]
                                if before["content_digests"][table]["column_sha256"].get(column)
                                != after["content_digests"].get(table, {}).get("column_sha256", {}).get(column)
                            )
                            raise RehearsalError("post-import content digest mismatch for " + ",".join(differing_columns)
                                                 + "; transaction rolled back")
                        if after["foreign_key_orphans"] or after["object_defects"]:
                            raise RehearsalError("post-import reconciliation reported relationship/object defects; transaction rolled back")
                        result["source_files"]["source_after"] = _file_fingerprint(source_path)
                        if result["source_files"]["source_before"] != result["source_files"]["source_after"]:
                            raise RehearsalError("source SQLite snapshot changed during rehearsal")
                        result.update({"inserted": inserted, "after": after})
                        return result
            finally:
                source_engine.dispose()
    except RehearsalError:
        raise
    except Exception as error:
        raise RehearsalError("rehearsal failed") from error
    finally:
        engine.dispose()


class ReadOnlyS3Adapter:
    """Optional comparison-only adapter. It intentionally exposes no mutation API."""
    def __init__(self, client: Any, bucket: str):
        self._client, self._bucket = client, bucket

    def entries(self) -> list[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        rows = []
        for page in paginator.paginate(Bucket=self._bucket):
            for item in page.get("Contents", []):
                rows.append({"key": item["Key"], "size": item.get("Size"), "etag": item.get("ETag")})
        return rows


@click.command()
@click.option("--source-sqlite", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Stopped/read-only SQLite snapshot. The original is never opened writable.")
@click.option("--target-url", required=True, envvar="REHEARSAL_TARGET_URL", help="Empty staging PostgreSQL URL.")
@click.option("--expected-target-host", help="Exact approved PostgreSQL host (not a display label).")
@click.option("--expected-target-database", help="Exact approved PostgreSQL database/project reference.")
@click.option("--expected-target-schema", help="Exact PostgreSQL schema expected after connection.")
@click.option("--dry-run", is_flag=True, help="Migrate/backfill only a temporary copy; do not write target.")
@click.option("--allow-sqlite-target", is_flag=True, help="TEST ONLY: permit SQLite compatibility target.")
@click.option("--sqlite-test-target-path", type=click.Path(dir_okay=False, path_type=Path),
              help="TEST ONLY: resolved path that must exactly equal the SQLite target URL path.")
def cli(source_sqlite: Path, target_url: str, expected_target_host: str | None,
        expected_target_database: str | None, expected_target_schema: str | None,
        dry_run: bool, allow_sqlite_target: bool, sqlite_test_target_path: Path | None) -> None:
    """Run one offline, non-repeatable legacy-migration rehearsal."""
    try:
        report = run_rehearsal(
            str(source_sqlite), target_url, expected_target_host=expected_target_host,
            expected_target_database=expected_target_database, expected_target_schema=expected_target_schema,
            dry_run=dry_run, allow_sqlite_target=allow_sqlite_target,
            sqlite_test_target_path=str(sqlite_test_target_path) if sqlite_test_target_path else None,
        )
    except Exception as error:
        correlation_id = uuid.uuid4().hex
        # Do not expose driver SQL, parameter values, paths, or credentials.
        raise click.ClickException(f"rehearsal_failed correlation_id={correlation_id}") from error
    click.echo(json.dumps(redact(report), default=str, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    cli()
