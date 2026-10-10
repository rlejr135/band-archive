"""Build canonical legacy-domain evidence from an immutable v11 dump offline.

The program has no target URL option and no remote connection code.  It
restores the supplied custom dump only into a newly initialized private local
PostgreSQL instance, and emits digest-only evidence for the refresh profile.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import shutil
from pathlib import Path

import click
from sqlalchemy import create_engine, text

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tenant_backfill import LEGACY_BAND_SLUG
from tools.legacy_rehearsal.preserve_identity_refresh import REFRESHED_TABLES, _digest_rows, _read_metadata, _scoped_rows
from tools.legacy_rehearsal.rehearsal import RehearsalError, _ReservedReportOutput


BASELINE_PROOF_VERSION = "phase9-offline-v11-refresh-baseline-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RehearsalError("private baseline PostgreSQL did not start")


def _load_verified_reports(restore_report: Path, v12_apply_report: Path, dump_sha256: str) -> tuple[str, str]:
    try:
        restore_raw, apply_raw = restore_report.read_bytes(), v12_apply_report.read_bytes()
        restore, apply = json.loads(restore_raw), json.loads(apply_raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError("baseline evidence reports are not readable JSON") from error
    # The retained verification utility emits the combined status after it has
    # both restored the immutable v11 dump and upgraded that private copy to
    # v12.  Do not accept an arbitrary success-looking report: these are the
    # only two documented, complete restore attestations.
    if (restore.get("status") not in {"local_restore_verified", "local_restore_and_v12_upgrade_verified"}
            or not restore.get("restored_matches_source")
            or not restore.get("source_before_after_identical") or restore.get("dump_sha256") != dump_sha256
            or apply.get("status") != "staging_v12_verified"
            or apply.get("expected_source_object_sha256") != restore.get("restored", {}).get("object_sha256")):
        raise RehearsalError("baseline evidence reports do not verify the immutable v11→v12 chain")
    return hashlib.sha256(restore_raw).hexdigest(), hashlib.sha256(apply_raw).hexdigest()


def _domain_digests(connection) -> dict:
    metadata = _read_metadata(connection)
    if not REFRESHED_TABLES <= set(metadata.tables):
        raise RehearsalError("offline baseline dump lacks refresh-domain tables")
    rows = connection.execute(text(
        "SELECT id FROM band WHERE slug=:slug AND is_legacy_import IS TRUE AND created_by_user_id IS NULL"
    ), {"slug": LEGACY_BAND_SLUG}).scalars().all()
    if len(rows) != 1:
        raise RehearsalError("offline baseline does not have one ownerless legacy band")
    band_id = rows[0]
    result = {}
    for name in sorted(REFRESHED_TABLES):
        table = metadata.tables[name]
        scoped = _scoped_rows(connection, table, band_id)
        if "band_id" in table.c:
            scoped = [{**dict(row), "band_id": 0} for row in scoped]
        result[name] = _digest_rows(table, scoped)
    return result


@click.command()
@click.option("--baseline-dump", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-dump-sha256", required=True)
@click.option("--verified-restore-report", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--verified-v12-apply-report", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--pg-bin", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--state-root", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--report-output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def cli(baseline_dump: Path, expected_dump_sha256: str, verified_restore_report: Path,
        verified_v12_apply_report: Path, pg_bin: Path, state_root: Path, report_output: Path) -> None:
    """Write immutable-dump-derived typed legacy-domain proof."""
    if len(expected_dump_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_dump_sha256):
        raise click.ClickException("offline_baseline_proof_failed")
    if _sha256(baseline_dump) != expected_dump_sha256:
        raise click.ClickException("offline_baseline_proof_failed")
    for executable in ("initdb.exe", "pg_restore.exe", "postgres.exe"):
        if not (pg_bin / executable).is_file():
            raise click.ClickException("offline_baseline_proof_failed")
    reservation = None
    server = None
    log_handle = None
    try:
        restore_sha, apply_sha = _load_verified_reports(verified_restore_report, verified_v12_apply_report,
                                                        expected_dump_sha256)
        reservation = _ReservedReportOutput(report_output)
        work_path = Path(tempfile.mkdtemp(prefix="refresh-baseline-", dir=state_root))
        try:
            data, log, port = work_path / "pgdata", work_path / "postgres.log", _free_port()
            subprocess.run([str(pg_bin / "initdb.exe"), "--no-locale", "--encoding=UTF8", "--username=postgres",
                            "--auth=trust", "--pgdata", str(data)], check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log_handle = log.open("ab")
            server = subprocess.Popen([str(pg_bin / "postgres.exe"), "-D", str(data), "-h", "127.0.0.1", "-p", str(port)],
                                      stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _wait(port, server)
            environment = {"PGHOST": "127.0.0.1", "PGPORT": str(port), "PGUSER": "postgres", "PGDATABASE": "postgres"}
            subprocess.run([str(pg_bin / "pg_restore.exe"), "--no-owner", "--no-acl", "--exit-on-error", "--clean",
                            "--if-exists", "--host=127.0.0.1", f"--port={port}", "--username=postgres",
                            "--dbname=postgres", str(baseline_dump)], check=True, env={**os.environ, **environment},
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            local_url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
            # The input is a private restored copy.  This uses the normal
            # formal runner so the canonical maps have the exact v12/head
            # column contract, never an ad-hoc reflected approximation.
            subprocess.run([sys.executable, str(Path(__file__).parents[2] / "migration_runner.py"), "upgrade"],
                           check=True, env={**os.environ, **environment, "DATABASE_URL": local_url},
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            engine = create_engine(local_url, future=True)
            try:
                with engine.connect() as connection:
                    domain = _domain_digests(connection)
            finally:
                engine.dispose()
        finally:
            if server is not None:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=15)
                server = None
            if log_handle is not None:
                log_handle.close()
                log_handle = None
            shutil.rmtree(work_path)
        report = {"version": BASELINE_PROOF_VERSION, "status": "offline_v11_baseline_verified",
                  "dump_sha256": expected_dump_sha256, "verified_restore_report_sha256": restore_sha,
                  "verified_v12_apply_report_sha256": apply_sha, "legacy_domain": {"table_digests": domain}}
        reservation.publish(report)
        click.echo(f"report_written sha256={reservation.retain()}")
    except Exception as error:
        if reservation is not None:
            reservation.abort()
        raise click.ClickException("offline_baseline_proof_failed") from error
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=15)
        if log_handle is not None:
            log_handle.close()


if __name__ == "__main__":
    cli()
