"""Offline contracts for the operator-only source and R2 evidence adapters."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import shutil
import sqlite3

import pytest
from click.testing import CliRunner

from tools.legacy_rehearsal import build_readonly_object_proof as binder
from tools.legacy_rehearsal import capture_readonly_r2_inventory as r2_capture
from tools.legacy_rehearsal.capture_sqlite_snapshot import capture_sqlite_backup
from tools.legacy_rehearsal.preserve_identity_refresh import load_readonly_object_proof
from tools.legacy_rehearsal.rehearsal import RehearsalError, _file_fingerprint


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
ENDPOINT = "https://abcdefgh.r2.cloudflarestorage.com"
BUCKET = "archive-bucket"


class FakeReadOnlyR2:
    def __init__(self, pages, heads):
        self.pages = list(pages)
        self.heads = {key: list(values) for key, values in heads.items()}
        self.calls = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(("list", kwargs))
        return self.pages.pop(0)

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        return self.heads[kwargs["Key"]].pop(0)

    def __getattr__(self, name):
        if name in {"put_object", "copy_object", "delete_object", "upload_file", "create_multipart_upload"}:
            raise AssertionError(f"unexpected mutating R2 method: {name}")
        raise AttributeError(name)


def _pages():
    return [
        {"IsTruncated": True, "NextContinuationToken": "cursor-1",
         "Contents": [{"Key": "a", "Size": 1, "ETag": "etag-a", "LastModified": NOW}]},
        {"IsTruncated": False,
         "Contents": [{"Key": "b", "Size": 2, "ETag": "etag-b", "LastModified": NOW}]},
        {"IsTruncated": True, "NextContinuationToken": "cursor-1",
         "Contents": [{"Key": "a", "Size": 1, "ETag": "etag-a", "LastModified": NOW}]},
        {"IsTruncated": False,
         "Contents": [{"Key": "b", "Size": 2, "ETag": "etag-b", "LastModified": NOW}]},
    ]


def _heads(second_etag="etag-a"):
    return {
        "a": [
            {"ContentLength": 1, "ETag": "etag-a", "LastModified": NOW},
            {"ContentLength": 1, "ETag": second_etag, "LastModified": NOW},
        ],
        "b": [
            {"ContentLength": 2, "ETag": "etag-b", "LastModified": NOW},
            {"ContentLength": 2, "ETag": "etag-b", "LastModified": NOW},
        ],
    }


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r2_capture_pages_all_keys_heads_metadata_twice_without_mutations():
    client = FakeReadOnlyR2(_pages(), _heads())
    raw = r2_capture.capture_two_pass_inventory(
        client, bucket=BUCKET, endpoint=ENDPOINT, max_objects=10,
        capture_id="00000000-0000-0000-0000-000000000001", captured_at="2026-10-10T00:00:00Z",
    )
    assert raw["inventory_complete"] is True
    assert raw["inventory_count"] == 2
    assert raw["request_budget"]["observed_head_requests"] == 4
    assert raw["passes"] == [{"page_count": 2, "terminal_cursor": None}] * 2
    assert [item["key"] for item in raw["objects"]] == ["a", "b"]
    assert [name for name, _ in client.calls] == ["list", "head", "list", "head", "list", "head", "list", "head"]
    assert all(call[1]["Bucket"] == BUCKET for call in client.calls)
    assert r2_capture._account_from_endpoint(ENDPOINT) == "abcdefgh"
    with pytest.raises(RehearsalError):
        r2_capture._account_from_endpoint("http://abcdefgh.r2.cloudflarestorage.com")


def test_r2_capture_accepts_subsecond_list_time_and_preserves_it_across_passes():
    pages = _pages()
    for index in (0, 2):
        pages[index]["Contents"][0]["LastModified"] = NOW.replace(microsecond=123_000)
    raw = r2_capture.capture_two_pass_inventory(
        FakeReadOnlyR2(pages, _heads()), bucket=BUCKET, endpoint=ENDPOINT, max_objects=10,
        capture_id="00000000-0000-0000-0000-000000000007", captured_at="2026-10-10T00:00:00Z",
    )
    assert raw["objects"][0]["last_modified"] == "2026-10-10T00:00:00.123000Z"


def test_r2_plan_reports_bounded_budget_without_constructing_a_client(monkeypatch):
    monkeypatch.setattr(r2_capture, "_environment_client", lambda: (_ for _ in ()).throw(AssertionError("network")))
    result = CliRunner().invoke(r2_capture.cli, ["--plan", "--max-objects", "10"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "max_head_requests": 20, "max_list_requests": 22, "max_objects": 10, "status": "plan",
    }


def test_r2_diagnostic_is_bounded_and_redacts_first_object_metadata(monkeypatch):
    listed_modified = NOW.replace(microsecond=123_000)
    headed_modified = NOW
    client = FakeReadOnlyR2(
        [{"IsTruncated": False, "Contents": [{"Key": "private-object-key", "Size": 1,
                                                  "ETag": "private-list-etag", "LastModified": listed_modified}]}],
        {"private-object-key": [{"ContentLength": 1, "ETag": "private-list-etag",
                                  "LastModified": headed_modified}]},
    )
    monkeypatch.setenv("S3_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("S3_ACCESS_KEY", "private-access-key")
    monkeypatch.setenv("S3_SECRET_KEY", "private-secret-key")
    monkeypatch.setattr(r2_capture, "_environment_client", lambda: (client, BUCKET, ENDPOINT))
    result = CliRunner().invoke(r2_capture.cli, ["--diagnose-first-object"])
    assert result.exit_code == 0
    observed = json.loads(result.output)
    assert observed["status"] == "observed"
    assert observed["request_count"] == {"list": 1, "head": 1}
    assert observed["parity"]["size_equal"] is True
    assert observed["parity"]["etag_equal"] is True
    assert observed["parity"]["last_modified_equal"] is False
    assert observed["parity"]["last_modified_second_equal"] is True
    assert observed["list"]["first_object"]["last_modified_has_subsecond"] is True
    assert observed["head"]["last_modified_has_subsecond"] is False
    assert [name for name, _ in client.calls] == ["list", "head"]
    for secret in ("private-object-key", "private-list-etag", "private-access-key", "private-secret-key", ENDPOINT):
        assert secret not in result.output


def test_r2_diagnostic_never_requests_provider_for_invalid_config_or_mode_combinations(monkeypatch, tmp_path):
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)
    monkeypatch.setattr(r2_capture, "_environment_client", lambda: (_ for _ in ()).throw(AssertionError("network")))
    result = CliRunner().invoke(r2_capture.cli, ["--diagnose-first-object"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "config": {"all_s3_variables_present": False, "endpoint_canonical": False},
        "request_count": {"head": 0, "list": 0},
        "status": "config_invalid",
    }
    for arguments in (("--diagnose-first-object", "--plan"),
                      ("--diagnose-first-object", "--output", str(tmp_path / "private.json"))):
        result = CliRunner().invoke(r2_capture.cli, list(arguments))
        assert result.exit_code != 0
        assert result.output == "Error: readonly_r2_inventory_capture_failed\n"
    assert not (tmp_path / "private.json").exists()


def test_r2_diagnostic_suppresses_provider_failure_details(monkeypatch):
    class FailingList:
        def list_objects_v2(self, **kwargs):
            raise RuntimeError("https://private-endpoint private-object-key")

    monkeypatch.setenv("S3_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("S3_ACCESS_KEY", "private-access-key")
    monkeypatch.setenv("S3_SECRET_KEY", "private-secret-key")
    monkeypatch.setattr(r2_capture, "_environment_client", lambda: (FailingList(), BUCKET, ENDPOINT))
    result = CliRunner().invoke(r2_capture.cli, ["--diagnose-first-object"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "config": {"all_s3_variables_present": True, "endpoint_canonical": True},
        "request_count": {"head": 0, "list": 1},
        "status": "list_request_failed",
    }
    assert "private" not in result.output


def test_r2_diagnostic_suppresses_head_failure_details(monkeypatch):
    class FailingHead:
        def list_objects_v2(self, **kwargs):
            return {"IsTruncated": False, "Contents": [{"Key": "private-object-key", "Size": 1,
                                                            "ETag": "private-list-etag", "LastModified": NOW}]}

        def head_object(self, **kwargs):
            raise RuntimeError("https://private-endpoint private-object-key")

    monkeypatch.setenv("S3_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("S3_ACCESS_KEY", "private-access-key")
    monkeypatch.setenv("S3_SECRET_KEY", "private-secret-key")
    monkeypatch.setattr(r2_capture, "_environment_client", lambda: (FailingHead(), BUCKET, ENDPOINT))
    result = CliRunner().invoke(r2_capture.cli, ["--diagnose-first-object"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "config": {"all_s3_variables_present": True, "endpoint_canonical": True},
        "list": {
            "contents_is_list": True,
            "explicit_completeness": True,
            "first_object": {
                "etag_usable": True,
                "is_mapping": True,
                "key_usable": True,
                "last_modified_has_subsecond": False,
                "last_modified_has_timezone": True,
                "last_modified_is_datetime": True,
                "last_modified_is_millisecond_aligned": True,
                "size_valid": True,
            },
            "first_object_present": True,
            "is_mapping": True,
        },
        "request_count": {"head": 1, "list": 1},
        "status": "head_request_failed",
    }
    assert "private" not in result.output


def test_r2_capture_refuses_same_size_overwrite_or_partial_cursor():
    changed_pages = _pages()
    changed_pages[2]["Contents"][0]["ETag"] = "etag-overwritten"
    with pytest.raises(RehearsalError, match="changed"):
        r2_capture.capture_two_pass_inventory(
            FakeReadOnlyR2(changed_pages, _heads(second_etag="etag-overwritten")),
            bucket=BUCKET, endpoint=ENDPOINT, max_objects=10,
        )
    client = FakeReadOnlyR2(
        [{"IsTruncated": True, "Contents": []}],
        {},
    )
    with pytest.raises(RehearsalError, match="continuation"):
        r2_capture.capture_inventory_pass(client, BUCKET, max_objects=10)
    pages = _pages()
    pages[0]["Contents"][0]["ETag"] = "stale-list-etag"
    with pytest.raises(RehearsalError, match="list/head"):
        r2_capture.capture_inventory_pass(FakeReadOnlyR2(pages, _heads()), BUCKET, max_objects=10)
    pages = _pages()
    pages[0]["Contents"][0]["Size"] = 9
    with pytest.raises(RehearsalError, match="list/head"):
        r2_capture.capture_inventory_pass(FakeReadOnlyR2(pages, _heads()), BUCKET, max_objects=10)
    pages = _pages()
    pages[0]["Contents"][0]["LastModified"] = NOW.replace(hour=1)
    with pytest.raises(RehearsalError, match="list/head"):
        r2_capture.capture_inventory_pass(FakeReadOnlyR2(pages, _heads()), BUCKET, max_objects=10)


def test_r2_capture_rejects_subsecond_change_between_full_passes():
    pages = _pages()
    pages[0]["Contents"][0]["LastModified"] = NOW.replace(microsecond=123_000)
    pages[2]["Contents"][0]["LastModified"] = NOW.replace(microsecond=456_000)
    with pytest.raises(RehearsalError, match="changed between"):
        r2_capture.capture_two_pass_inventory(
            FakeReadOnlyR2(pages, _heads()), bucket=BUCKET, endpoint=ENDPOINT, max_objects=10,
        )


def test_sqlite_backup_and_local_binding_keep_nonfreeze_provenance(tmp_path):
    source, output, manifest_path = tmp_path / "source.sqlite", tmp_path / "backup.sqlite", tmp_path / "backup.json"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO item (value) VALUES ('unchanged-source')")
    manifest = capture_sqlite_backup(source, output, manifest_path, work_dir=tmp_path,
                                     capture_id="00000000-0000-0000-0000-000000000002",
                                     captured_at="2026-10-10T00:00:01Z")
    assert manifest["status"] == "captured_not_frozen"
    assert manifest["writer_freeze_attestation"] is False
    assert manifest["snapshot"] == _file_fingerprint(output)
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT value FROM item").fetchone()[0] == "unchanged-source"

    raw = r2_capture.capture_two_pass_inventory(
        FakeReadOnlyR2(_pages(), _heads()), bucket=BUCKET, endpoint=ENDPOINT, max_objects=10,
        capture_id="00000000-0000-0000-0000-000000000003", captured_at="2026-10-10T00:00:02Z",
    )
    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes((json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode())
    # A secure transfer can change mtime.  Copy bytes without preserving source
    # metadata: the binder must accept SHA+size equality, not fake an mtime.
    transferred = tmp_path / "transferred.sqlite"
    shutil.copyfile(output, transferred)
    assert _file_fingerprint(transferred)["sha256"] == manifest["snapshot"]["sha256"]
    assert _file_fingerprint(transferred)["size"] == manifest["snapshot"]["size"]
    assert _file_fingerprint(transferred)["mtime_ns"] != manifest["snapshot"]["mtime_ns"]
    proof_path, binding_path = tmp_path / "proof.json", tmp_path / "binding.json"
    proof, binding = binder.build_object_proof(
        transferred, manifest_path, expected_snapshot_manifest_sha256=_sha(manifest_path),
        raw_inventory_path=raw_path, expected_raw_inventory_sha256=_sha(raw_path),
        proof_output=proof_path, binding_output=binding_path,
        binding_id="00000000-0000-0000-0000-000000000004", bound_at="2026-10-10T00:00:03Z",
    )
    assert binding["binding_assertion"] == "local_file_identity_only"
    assert binding["writer_freeze_attestation"] is False
    assert proof["source_snapshot"] == _file_fingerprint(transferred)
    assert binding["source_capture"]["captured_snapshot"] == manifest["snapshot"]
    loaded = load_readonly_object_proof(
        proof_path, _file_fingerprint(transferred), expected_sha256=_sha(proof_path),
        expected_account_sha256=raw["scope"]["account_sha256"],
        expected_bucket_sha256=raw["scope"]["bucket_sha256"],
    )
    assert loaded == proof
    with transferred.open("ab") as stream:
        stream.write(b"changed-after-transfer")
    with pytest.raises(RehearsalError, match="transferred snapshot differs"):
        binder.build_object_proof(
            transferred, manifest_path, expected_snapshot_manifest_sha256=_sha(manifest_path),
            raw_inventory_path=raw_path, expected_raw_inventory_sha256=_sha(raw_path),
            proof_output=tmp_path / "changed-proof.json", binding_output=tmp_path / "changed-binding.json",
        )


def test_binder_refuses_unapproved_raw_inventory_before_output(tmp_path):
    snapshot, manifest, raw = tmp_path / "snapshot.sqlite", tmp_path / "manifest.json", tmp_path / "raw.json"
    sqlite3.connect(snapshot).close()
    manifest.write_text("{}", encoding="utf-8")
    raw.write_text("{}", encoding="utf-8")
    with pytest.raises(RehearsalError, match="independently supplied"):
        binder.build_object_proof(
            snapshot, manifest, expected_snapshot_manifest_sha256="0" * 64,
            raw_inventory_path=raw, expected_raw_inventory_sha256="1" * 64,
            proof_output=tmp_path / "proof.json", binding_output=tmp_path / "binding.json",
        )
    assert not (tmp_path / "proof.json").exists()
    assert not (tmp_path / "binding.json").exists()


def test_binder_aborts_first_reservation_if_second_reservation_fails(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot.sqlite"
    sqlite3.connect(snapshot).close()
    fingerprint = _file_fingerprint(snapshot)
    manifest = {
        "version": "phase9-sqlite-readonly-backup-v1",
        "adapter_version": "phase9-sqlite-readonly-capture-adapter-v1",
        "capture_id": "00000000-0000-0000-0000-000000000005",
        "captured_at": "2026-10-10T00:00:04Z",
        "snapshot": fingerprint,
        "integrity_check": "ok",
        "writer_freeze_attestation": False,
        "status": "captured_not_frozen",
    }
    raw = {
        "version": "phase9-r2-readonly-inventory-capture-v1",
        "adapter_version": "phase9-r2-readonly-capture-adapter-v1",
        "capture_id": "00000000-0000-0000-0000-000000000006",
        "captured_at": "2026-10-10T00:00:05Z",
        "scope": {name: "0" * 64 for name in ("account_sha256", "bucket_sha256", "endpoint_sha256")},
        "inventory_complete": True,
        "inventory_count": 0,
        "inventory_metadata_sha256": r2_capture._canonical_sha256([]),
        "request_budget": {"max_objects": 1, "max_list_requests": 4, "max_head_requests": 2,
                           "observed_list_requests": 2, "observed_head_requests": 0},
        "passes": [{"page_count": 1, "terminal_cursor": None}] * 2,
        "objects": [],
    }
    manifest_path, raw_path = tmp_path / "manifest.json", tmp_path / "raw.json"
    manifest_path.write_bytes((json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode())
    raw_path.write_bytes((json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode())
    original = binder._ReservedReportOutput
    calls = 0

    def fail_second_reservation(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second reservation failure")
        return original(path)

    monkeypatch.setattr(binder, "_ReservedReportOutput", fail_second_reservation)
    proof, binding = tmp_path / "proof.json", tmp_path / "binding.json"
    with pytest.raises(OSError, match="second reservation"):
        binder.build_object_proof(
            snapshot, manifest_path, expected_snapshot_manifest_sha256=_sha(manifest_path),
            raw_inventory_path=raw_path, expected_raw_inventory_sha256=_sha(raw_path),
            proof_output=proof, binding_output=binding,
        )
    assert not proof.exists()
    assert not binding.exists()
