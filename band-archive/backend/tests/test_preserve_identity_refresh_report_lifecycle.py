"""Durable evidence lifecycle contracts for the refresh CLI."""

import json
import sqlite3

from click.testing import CliRunner

from tools.legacy_rehearsal.preserve_identity_refresh import cli


def test_cli_keeps_prepared_report_when_post_publication_step_fails(tmp_path, monkeypatch):
    source, output = tmp_path / "source.sqlite", tmp_path / "refresh.json"
    sqlite3.connect(source).close()

    import tools.legacy_rehearsal.preserve_identity_refresh as refresh
    monkeypatch.setattr(refresh, "load_reviewed_profile", lambda path: {"version": "phase9-preserve-identity-refresh-v1"})
    monkeypatch.setattr(refresh, "load_write_freeze_attestation", lambda path, fingerprint: {})
    monkeypatch.setattr(refresh, "load_readonly_object_proof", lambda path, fingerprint: {"objects": []})

    def fail_after_prepared(*args, report_publisher, **kwargs):
        report_publisher({"status": "prepared", "mode": "preserve_identity_refresh"})
        raise RuntimeError("injected post-publication failure")

    monkeypatch.setattr(refresh, "run_preserve_identity_refresh", fail_after_prepared)
    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://operator:value@example.test/database",
        "--expected-target-host", "example.test", "--expected-target-database", "database",
        "--expected-target-schema", "public", "--approved-tls-ca-sha256", "a" * 64,
        "--write-freeze-attestation", str(source), "--reviewed-baseline-profile", str(source),
        "--readonly-object-manifest-proof", str(source), "--report-output", str(output),
    ])

    assert result.exit_code != 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "prepared"
    assert not (tmp_path / "refresh.json.committed").exists()
