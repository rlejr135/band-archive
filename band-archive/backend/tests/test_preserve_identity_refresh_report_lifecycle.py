"""Durable evidence lifecycle contracts for the refresh CLI."""

import json
import sqlite3

from click.testing import CliRunner

from tools.legacy_rehearsal.preserve_identity_refresh import cli


def test_cli_keeps_prepared_report_when_post_publication_step_fails(tmp_path, monkeypatch):
    source, output = tmp_path / "source.sqlite", tmp_path / "refresh.json"
    sqlite3.connect(source).close()

    import tools.legacy_rehearsal.preserve_identity_refresh as refresh
    monkeypatch.setattr(refresh, "load_reviewed_profile", lambda path, digest: {"version": refresh.PROFILE_VERSION})
    monkeypatch.setattr(refresh, "load_write_freeze_attestation", lambda path, fingerprint: {})
    monkeypatch.setattr(refresh, "load_readonly_object_proof", lambda path, fingerprint, **kwargs: {"objects": []})
    monkeypatch.setattr(refresh, "load_approved_object_dispositions", lambda path, **kwargs: {"dispositions": []})

    def fail_after_prepared(*args, report_publisher, **kwargs):
        report_publisher({"status": "prepared", "mode": "preserve_identity_refresh"})
        raise RuntimeError("injected post-publication failure")

    monkeypatch.setattr(refresh, "run_preserve_identity_refresh", fail_after_prepared)
    result = CliRunner().invoke(cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://operator:value@example.test/database",
        "--expected-target-host", "example.test", "--expected-target-database", "database",
        "--expected-target-schema", "public", "--approved-tls-ca-sha256", "a" * 64,
        "--write-freeze-attestation", str(source), "--reviewed-baseline-profile", str(source),
        "--expected-reviewed-baseline-profile-sha256", "b" * 64,
        "--readonly-object-manifest-proof", str(source), "--expected-readonly-object-manifest-proof-sha256", "c" * 64,
        "--expected-object-account-sha256", "d" * 64, "--expected-object-bucket-sha256", "e" * 64,
        "--approved-object-disposition-manifest", str(source),
        "--expected-approved-object-disposition-manifest-sha256", "f" * 64,
        "--report-output", str(output),
    ])

    assert result.exit_code != 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "prepared"
    assert not (tmp_path / "refresh.json.committed").exists()
