"""Fail-closed, offline legacy database migration rehearsal.

This module deliberately has no application entry point and no storage writer.
It is intended for a *copy* of a stopped Fly SQLite volume and an empty,
explicitly-labelled staging database already at Alembic head.  In particular it
does not infer a DATABASE_URL, talk to Supabase/Fly, or make R2 mutations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import click
from alembic.config import Config
from alembic.script import ScriptDirectory
from flask_migrate import upgrade
from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import Connection, Engine, URL, make_url

from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app, stamp_existing_current_schema
from tenant_backfill import TenantBackfillError, apply as apply_tenant_backfill
from extensions import db


class RehearsalError(RuntimeError):
    """A refusal intended to be acted on by an operator, not worked around."""


_SECRET = re.compile(r"(?i)(token|secret|password|credential|authorization|signature|upload_id|r2_upload_id|etag)")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+=,-]*$")
_MANAGED_EXCLUDE = {"alembic_version"}
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


def _assert_supported_source_schema(path: Path) -> None:
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
        return
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


def _copy_and_backfill(source: str, temporary_dir: Path) -> tuple[Path, dict[str, Any]]:
    source_path = preflight_sqlite_snapshot(source)
    _assert_supported_source_schema(source_path)
    source_identity = (source_path.stat().st_size, source_path.stat().st_mtime_ns)
    copy_path = temporary_dir / "legacy-rehearsal-copy.sqlite"
    shutil.copy2(source_path, copy_path)
    if source_identity != (source_path.stat().st_size, source_path.stat().st_mtime_ns):
        raise RehearsalError("source SQLite snapshot changed while being copied")
    source_url = f"sqlite:///{copy_path.as_posix()}"
    app = create_migration_app(source_url)
    try:
        with app.app_context():
            inspector = inspect(db.engine)
            if "alembic_version" not in inspector.get_table_names():
                stamp_existing_current_schema()
            upgrade(directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                report = apply_tenant_backfill(connection)
    except (TenantBackfillError, Exception) as error:
        # Never include a DB URL: drivers often render embedded passwords.
        raise RehearsalError(f"source copy migration/backfill failed: {redact(str(error))}") from error
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    return copy_path, redact(report)


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
        if table not in tables or not _has_columns(connection, table, (pk, band, key_column)):
            continue
        columns = [pk, band, key_column] + ([fallback] if fallback else []) + ([size] if size else [])
        query_columns = ", ".join(f'"{col}"' for col in columns)
        for row in connection.execute(text(f'SELECT {query_columns} FROM "{table}" ORDER BY "{pk}"')).mappings():
            key = row[key_column] or (row[fallback] if fallback else None)
            if not key:
                continue
            key = str(key).strip()
            if not _SAFE_KEY.fullmatch(key) or ".." in key.split("/") or key.startswith("/"):
                defects.append({"kind": "bad_key", "table": table, "id": row[pk]})
                continue
            if prefix and not key.startswith(f"{prefix}/"):
                defects.append({"kind": "cross_prefix_key", "table": table, "id": row[pk]})
                continue
            entry = {"key": key, "owner": {"type": table, "id": row[pk], "band_id": row[band]},
                     "expected_size": row[size] if size else None}
            manifest.append(entry)
            seen[key].append(entry)
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


def _assert_target_ready(engine: Engine, target_label: str, allow_sqlite_target: bool) -> None:
    dialect = engine.dialect.name
    if dialect != "postgresql" and not (dialect == "sqlite" and allow_sqlite_target):
        raise RehearsalError("target must be PostgreSQL; SQLite is test-only and needs --allow-sqlite-target")
    if not any(label.lower() in target_label.lower() for label in ("rehearsal", "staging", "test")):
        raise RehearsalError("target label must explicitly identify a rehearsal/staging/test namespace")
    with engine.connect() as connection:
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


def run_rehearsal(source_sqlite: str, target_url: str, target_label: str, *, dry_run: bool = False,
                  allow_sqlite_target: bool = False) -> dict[str, Any]:
    """Copy/migrate/backfill an SQLite copy and optionally import it once.

    The target is always checked *before* the source work, is never upgraded by
    this tool, and all inserts plus sequence updates occur in one transaction.
    """
    engine = create_engine(target_url, future=True)
    try:
        _assert_target_ready(engine, target_label, allow_sqlite_target)
        with tempfile.TemporaryDirectory(prefix="band-archive-legacy-rehearsal-") as directory:
            source_fingerprint = _source_schema_fingerprint(preflight_sqlite_snapshot(source_sqlite))
            source_copy, backfill = _copy_and_backfill(source_sqlite, Path(directory))
            source_engine = create_engine(f"sqlite:///{source_copy.as_posix()}", future=True)
            try:
                with source_engine.connect() as source_connection:
                    before = reconcile(source_connection)
                    result = {"target": safe_target_identity(target_url), "source_schema": source_fingerprint, "backfill": backfill,
                              "before": before, "dry_run": dry_run}
                    if dry_run:
                        return result
                    with engine.begin() as target_connection:
                        inserted = _copy_rows(source_connection, target_connection)
                        _reset_postgres_sequences(target_connection, _read_metadata(target_connection))
                        after = reconcile(target_connection)
                        if before["table_counts"] != after["table_counts"] or before["primary_keys"] != after["primary_keys"]:
                            raise RehearsalError("post-import table count or primary-key mismatch; transaction rolled back")
                        if after["foreign_key_orphans"] or after["object_defects"]:
                            raise RehearsalError("post-import reconciliation reported relationship/object defects; transaction rolled back")
                        result.update({"inserted": inserted, "after": after})
                        return result
            finally:
                source_engine.dispose()
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
@click.option("--target-label", required=True, help="Must explicitly say rehearsal, staging, or test.")
@click.option("--dry-run", is_flag=True, help="Migrate/backfill only a temporary copy; do not write target.")
@click.option("--allow-sqlite-target", is_flag=True, help="TEST ONLY: permit SQLite compatibility target.")
def cli(source_sqlite: Path, target_url: str, target_label: str, dry_run: bool, allow_sqlite_target: bool) -> None:
    """Run one offline, non-repeatable legacy-migration rehearsal."""
    try:
        report = run_rehearsal(str(source_sqlite), target_url, target_label, dry_run=dry_run,
                               allow_sqlite_target=allow_sqlite_target)
    except RehearsalError as error:
        raise click.ClickException(str(redact(str(error)))) from error
    click.echo(json.dumps(redact(report), default=str, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    cli()
