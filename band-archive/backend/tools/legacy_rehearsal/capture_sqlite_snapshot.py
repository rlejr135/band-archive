"""Create a consistent, explicitly non-final SQLite backup from a Fly volume.

The source is opened read-only and copied with SQLite's online backup API.
This is evidence collection only: it neither stops writers nor creates a
write-freeze attestation, so an output from this module is never final-cutover
authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any
from urllib.parse import quote
import uuid

import click

from tools.legacy_rehearsal.rehearsal import RehearsalError, _ReservedReportOutput, _file_fingerprint


SNAPSHOT_CAPTURE_VERSION = "phase9-sqlite-readonly-backup-v1"
ADAPTER_VERSION = "phase9-sqlite-readonly-capture-adapter-v1"
BACKUP_TIMEOUT_SECONDS = 120
BACKUP_PAGES_PER_STEP = 256


def _capture_identity(value: str | None) -> str:
    candidate = value or str(uuid.uuid4())
    try:
        if str(uuid.UUID(candidate)) != candidate:
            raise ValueError
    except ValueError as error:
        raise RehearsalError("SQLite capture identity is invalid") from error
    return candidate


def _sqlite_readonly_uri(path: Path) -> str:
    return "file:" + quote(path.resolve().as_posix(), safe="/") + "?mode=ro"


def _integrity_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    if rows != [("ok",)]:
        raise RehearsalError("SQLite integrity_check failed")


def _safe_output(path: Path, work_dir: Path) -> Path:
    destination, workspace = path.expanduser().resolve(), work_dir.expanduser().resolve()
    if not workspace.is_dir() or not destination.parent.is_dir():
        raise RehearsalError("snapshot work and output directories must already exist")
    try:
        destination.relative_to(workspace)
    except ValueError as error:
        raise RehearsalError("snapshot output must stay within the owned temporary workspace") from error
    if os.path.lexists(destination):
        raise RehearsalError("snapshot output already exists")
    return destination


def _publish_snapshot(temp_path: Path, destination: Path) -> None:
    """Atomically publish an owned temporary file without replacing a path."""
    if os.path.lexists(destination):
        raise RehearsalError("snapshot output already exists")
    try:
        os.link(temp_path, destination)
    except FileExistsError as error:
        raise RehearsalError("snapshot output already exists") from error
    except OSError as error:
        raise RehearsalError("snapshot output cannot be published atomically") from error
    if not destination.is_file() or destination.is_symlink():
        raise RehearsalError("snapshot output publication changed identity")
    # A hard link is atomic, but make the directory entry durable before the
    # caller can retain the corresponding manifest.  Windows does not expose a
    # portable directory-fsync API; Fly runs Linux, where failure is fatal.
    if os.name != "nt":
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            _remove_snapshot_if_still_owned(temp_path, destination)
            raise RehearsalError("snapshot output directory cannot be synchronized") from error


def _remove_snapshot_if_still_owned(temp_path: Path, destination: Path) -> None:
    """Remove a failed publication only while it is still our hard link."""
    try:
        temporary_stat = temp_path.stat()
        destination_stat = destination.stat()
        if (destination.is_symlink() or temporary_stat.st_dev != destination_stat.st_dev
                or temporary_stat.st_ino != destination_stat.st_ino):
            return
        destination.unlink()
    except OSError:
        # Preserve an uncertain path rather than risk unlinking a replacement.
        return


def _backup_with_deadline(source: sqlite3.Connection, destination: sqlite3.Connection) -> None:
    """Perform a bounded online backup without touching the source database."""
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise RehearsalError("SQLite backup deadline exceeded")

    source.backup(destination, pages=BACKUP_PAGES_PER_STEP, progress=progress)
    if time.monotonic() > deadline:
        raise RehearsalError("SQLite backup deadline exceeded")


def capture_sqlite_backup(source: Path, output: Path, manifest_output: Path, *, work_dir: Path,
                          capture_id: str | None = None, captured_at: str | None = None) -> dict[str, Any]:
    """Back up read-only SQLite bytes and retain a non-freeze capture manifest."""
    source = source.expanduser().resolve()
    if not source.is_file():
        raise RehearsalError("SQLite source does not exist")
    destination = _safe_output(output, work_dir)
    manifest_destination = _safe_output(manifest_output, work_dir)
    if destination == manifest_destination:
        raise RehearsalError("snapshot and manifest outputs must be distinct")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".sqlite-backup-", suffix=".tmp", dir=work_dir)
    os.close(descriptor)
    temporary = Path(temporary_name)
    reservation = None
    snapshot_published = False
    try:
        with sqlite3.connect(_sqlite_readonly_uri(source), uri=True) as source_connection:
            source_connection.execute("PRAGMA query_only = ON")
            _integrity_check(source_connection)
            with sqlite3.connect(temporary) as destination_connection:
                _backup_with_deadline(source_connection, destination_connection)
                destination_connection.commit()
                _integrity_check(destination_connection)
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        snapshot = _file_fingerprint(temporary)
        manifest = {
            "version": SNAPSHOT_CAPTURE_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "capture_id": _capture_identity(capture_id),
            "captured_at": captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "snapshot": snapshot,
            "integrity_check": "ok",
            "writer_freeze_attestation": False,
            "status": "captured_not_frozen",
        }
        # Reserve the JSON first so a manifest race cannot leave an untracked
        # published database.  Both outputs are exclusively linked, never replaced.
        reservation = _ReservedReportOutput(manifest_destination)
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        reservation.publish_bytes(manifest_bytes)
        _publish_snapshot(temporary, destination)
        snapshot_published = True
        reservation.retain()
        return manifest
    except Exception:
        if reservation is not None:
            reservation.abort()
        if snapshot_published:
            _remove_snapshot_if_still_owned(temporary, destination)
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _require_fly_paths(source: Path, work_dir: Path) -> None:
    """Keep the live source under /data and all backup bytes under /tmp."""
    source, work_dir = source.expanduser().resolve(), work_dir.expanduser().resolve()
    try:
        source.relative_to(Path("/data"))
        work_dir.relative_to(Path("/tmp"))
    except ValueError as error:
        raise RehearsalError("capture requires a /data source and an owned /tmp workspace") from error


@click.command()
@click.option("--source-sqlite", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--manifest-output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--work-dir", default="/tmp", show_default=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
def cli(source_sqlite: Path, output: Path, manifest_output: Path, work_dir: Path) -> None:
    """Capture a consistent source backup, explicitly without a writer freeze."""
    try:
        _require_fly_paths(source_sqlite, work_dir)
        manifest = capture_sqlite_backup(source_sqlite, output, manifest_output, work_dir=work_dir)
        click.echo(json.dumps({"status": manifest["status"], "integrity_check": "ok",
                               "snapshot_sha256": manifest["snapshot"]["sha256"],
                               "snapshot_size": manifest["snapshot"]["size"]}, sort_keys=True))
    except Exception as error:
        raise click.ClickException("readonly_sqlite_snapshot_capture_failed") from error


if __name__ == "__main__":
    cli()
