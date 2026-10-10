"""Local contracts for externally pinned object-inventory evidence.

These tests deliberately use synthetic JSON only.  They exercise no R2 client
and do not represent an actual remote inventory capture.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from click.testing import CliRunner

import tools.legacy_rehearsal.preserve_identity_refresh as refresh
from tools.legacy_rehearsal.rehearsal import RehearsalError


SOURCE = {"sha256": "a" * 64, "size": 7, "mtime_ns": 9}
ACCOUNT, BUCKET = "b" * 64, "c" * 64


def _write_json(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _proof(objects=None):
    objects = [{"key": "media/a.mp4", "size": 4}] if objects is None else objects
    inventory_sha = (refresh._inventory_sha256(objects)
                     if all(isinstance(item, dict) and "size" in item for item in objects) else "0" * 64)
    return {
        "version": refresh.OBJECT_PROOF_VERSION,
        "source_snapshot": SOURCE,
        "capture": {
            "method": "operator-readonly-list-head", "capture_id": "capture-20261010-1",
            "inventory_complete": True, "inventory_count": len(objects),
            "inventory_sha256": inventory_sha,
            "account_sha256": ACCOUNT, "bucket_sha256": BUCKET,
        },
        "objects": objects,
    }


def _load_proof(path, expected):
    return refresh.load_readonly_object_proof(
        path, SOURCE, expected_sha256=expected,
        expected_account_sha256=ACCOUNT, expected_bucket_sha256=BUCKET,
    )


def _disposition(key, kind, proof, *, source=SOURCE):
    capture = proof["capture"]
    return {
        "key": key, "kind": kind, "action": "retain_no_mutation", "source_snapshot": source,
        "account_sha256": capture["account_sha256"], "bucket_sha256": capture["bucket_sha256"],
        "inventory_sha256": capture["inventory_sha256"], "capture_id": capture["capture_id"],
        "approval_provenance": "operator-reviewed",
    }


def test_readonly_object_proof_rejects_duplicate_keys_and_missing_sizes(tmp_path):
    duplicate = _proof([{"key": "media/a.mp4", "size": 1}, {"key": "media/a.mp4", "size": 2}])
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_sha = _write_json(duplicate_path, duplicate)
    with pytest.raises(RehearsalError, match="invalid objects"):
        _load_proof(duplicate_path, duplicate_sha)

    missing_size = _proof([{"key": "media/a.mp4"}])
    missing_size_path = tmp_path / "missing-size.json"
    missing_size_sha = _write_json(missing_size_path, missing_size)
    with pytest.raises(RehearsalError, match="invalid objects"):
        _load_proof(missing_size_path, missing_size_sha)


def test_sidecars_require_external_byte_pins_and_bound_reviewed_dispositions(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")
    with pytest.raises(RehearsalError, match="externally approved SHA-256"):
        refresh.load_reviewed_profile(profile_path, "0" * 64)

    proof_path = tmp_path / "proof.json"
    proof = _proof()
    proof_sha = _write_json(proof_path, proof)
    loaded_proof = _load_proof(proof_path, proof_sha)
    approval_path = tmp_path / "approval.json"
    bad = {
        "version": refresh.OBJECT_DISPOSITION_APPROVAL_VERSION,
        "dispositions": [_disposition("media/untracked.mp4", "orphan", proof, source={**SOURCE, "size": 8})],
    }
    approval_sha = _write_json(approval_path, bad)
    with pytest.raises(RehearsalError, match="not bound"):
        refresh.load_approved_object_dispositions(
            approval_path, expected_sha256=approval_sha, source_fingerprint=SOURCE, object_proof=loaded_proof,
        )


def test_inventory_dispositions_must_be_exact_and_retain_only():
    proof = _proof([
        {"key": "media/required.mp4", "size": 1},
        {"key": "media/retired.mp4", "size": 2},
        {"key": "media/orphan.mp4", "size": 3},
    ])
    reconciliation = {
        "object_manifest": [
            {"key": "media/required.mp4", "expectation": "required_present"},
            {"key": "media/retired.mp4", "expectation": "retired_absent"},
        ],
        "object_comparison": {"retired_still_present": ["media/retired.mp4"]},
    }
    approval = {"dispositions": [
        _disposition("media/retired.mp4", "retired", proof),
        _disposition("media/orphan.mp4", "orphan", proof),
    ]}
    refresh._assert_object_inventory_dispositions(reconciliation, proof, approval)
    with pytest.raises(RehearsalError, match="unreviewed or stale"):
        refresh._assert_object_inventory_dispositions(reconciliation, proof, {"dispositions": approval["dispositions"][:1]})


def test_cli_rejects_unpinned_object_proof_before_refresh_runner(tmp_path, monkeypatch):
    source, output = tmp_path / "source.sqlite", tmp_path / "report.json"
    sqlite3.connect(source).close()
    proof_path = tmp_path / "proof.json"
    _write_json(proof_path, _proof())
    called = []
    monkeypatch.setattr(refresh, "load_reviewed_profile", lambda path, digest: {"version": refresh.PROFILE_VERSION})
    monkeypatch.setattr(refresh, "load_write_freeze_attestation", lambda path, fingerprint: {})
    monkeypatch.setattr(refresh, "run_preserve_identity_refresh", lambda *args, **kwargs: called.append(True))
    result = CliRunner().invoke(refresh.cli, [
        "--source-sqlite", str(source), "--target-url", "postgresql://operator:value@example.test/database",
        "--expected-target-host", "example.test", "--expected-target-database", "database", "--expected-target-schema", "public",
        "--approved-tls-ca-sha256", "d" * 64, "--write-freeze-attestation", str(source),
        "--reviewed-baseline-profile", str(source), "--expected-reviewed-baseline-profile-sha256", "e" * 64,
        "--readonly-object-manifest-proof", str(proof_path),
        "--expected-readonly-object-manifest-proof-sha256", "0" * 64,
        "--expected-object-account-sha256", ACCOUNT, "--expected-object-bucket-sha256", BUCKET,
        "--approved-object-disposition-manifest", str(source),
        "--expected-approved-object-disposition-manifest-sha256", "f" * 64,
        "--report-output", str(output), "--dry-run",
    ])
    assert result.exit_code != 0
    assert called == []
