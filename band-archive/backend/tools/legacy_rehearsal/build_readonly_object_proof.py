"""Bind transferred local capture artifacts into the strict refresh v2 proof.

This module makes a local file-identity assertion only.  It does not claim
that a remote R2 observation and a SQLite backup were simultaneous, does not
contact a provider, and cannot create a writer-freeze attestation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
import uuid

import click

from tools.legacy_rehearsal.capture_readonly_r2_inventory import (
    ADAPTER_VERSION as R2_ADAPTER_VERSION,
    RAW_INVENTORY_VERSION,
)
from tools.legacy_rehearsal.capture_sqlite_snapshot import (
    ADAPTER_VERSION as SQLITE_ADAPTER_VERSION,
    SNAPSHOT_CAPTURE_VERSION,
)
from tools.legacy_rehearsal.preserve_identity_refresh import OBJECT_PROOF_VERSION, _inventory_sha256
from tools.legacy_rehearsal.rehearsal import RehearsalError, _ReservedReportOutput, _file_fingerprint


BINDING_VERSION = "phase9-local-object-proof-binding-v1"
ADAPTER_VERSION = "phase9-local-object-proof-binder-v1"


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _pinned_json(path: Path, expected_sha256: str, label: str) -> tuple[dict[str, Any], str]:
    if not _is_sha256(expected_sha256):
        raise RehearsalError(f"{label} expected SHA-256 is invalid")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError(f"{label} is not readable JSON") from error
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise RehearsalError(f"{label} differs from its independently supplied SHA-256")
    if not isinstance(value, dict):
        raise RehearsalError(f"{label} has an invalid shape")
    return value, actual


def _valid_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _valid_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00").tzinfo == timezone.utc
    except ValueError:
        return False


def _validate_snapshot_manifest(value: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    required = {"version", "adapter_version", "capture_id", "captured_at", "snapshot", "integrity_check",
                "writer_freeze_attestation", "status"}
    captured_snapshot = value.get("snapshot")
    if (not isinstance(captured_snapshot, dict)
            or set(captured_snapshot) != {"sha256", "size", "mtime_ns"}
            or not _is_sha256(captured_snapshot.get("sha256"))
            or type(captured_snapshot.get("size")) is not int or captured_snapshot["size"] < 0
            or type(captured_snapshot.get("mtime_ns")) is not int or captured_snapshot["mtime_ns"] < 0):
        raise RehearsalError("SQLite capture manifest snapshot is invalid")
    # Secure transport need not preserve nanosecond mtime.  The proof binds the
    # transferred bytes by SHA-256 and size, while the captured fingerprint is
    # retained separately as provenance rather than disguised as local state.
    if (captured_snapshot["sha256"] != snapshot.get("sha256")
            or captured_snapshot["size"] != snapshot.get("size")):
        raise RehearsalError("SQLite transferred snapshot differs from its capture manifest")
    if (set(value) != required or value.get("version") != SNAPSHOT_CAPTURE_VERSION
            or value.get("adapter_version") != SQLITE_ADAPTER_VERSION or not _valid_uuid(value.get("capture_id"))
            or not _valid_utc_timestamp(value.get("captured_at"))
            or value.get("integrity_check") != "ok" or value.get("writer_freeze_attestation") is not False
            or value.get("status") != "captured_not_frozen"):
        raise RehearsalError("SQLite capture manifest is invalid or claims a writer freeze")


def _canonical_metadata_sha256(objects: list[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(objects, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_raw_inventory(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    required = {"version", "adapter_version", "capture_id", "captured_at", "scope", "inventory_complete",
                "inventory_count", "inventory_metadata_sha256", "request_budget", "passes", "objects"}
    if (set(value) != required or value.get("version") != RAW_INVENTORY_VERSION
            or value.get("adapter_version") != R2_ADAPTER_VERSION or not _valid_uuid(value.get("capture_id"))
            or not _valid_utc_timestamp(value.get("captured_at")) or value.get("inventory_complete") is not True
            or type(value.get("inventory_count")) is not int or value["inventory_count"] < 0
            or not _is_sha256(value.get("inventory_metadata_sha256"))):
        raise RehearsalError("raw R2 inventory capture is invalid")
    scope = value.get("scope")
    if (not isinstance(scope, dict) or set(scope) != {"account_sha256", "bucket_sha256", "endpoint_sha256"}
            or not all(_is_sha256(scope.get(name)) for name in scope)):
        raise RehearsalError("raw R2 inventory scope is invalid")
    budget = value.get("request_budget")
    budget_keys = {"max_objects", "max_list_requests", "max_head_requests", "observed_list_requests", "observed_head_requests"}
    if (not isinstance(budget, dict) or set(budget) != budget_keys
            or any(type(budget[name]) is not int or budget[name] < 0 for name in budget_keys)
            or budget["max_objects"] < value["inventory_count"]
            or budget["max_list_requests"] != 2 * (budget["max_objects"] + 1)
            or budget["max_head_requests"] != 2 * budget["max_objects"]
            or budget["observed_list_requests"] > budget["max_list_requests"]
            or budget["observed_head_requests"] != 2 * value["inventory_count"]
            or budget["observed_head_requests"] > budget["max_head_requests"]):
        raise RehearsalError("raw R2 inventory request budget is invalid")
    passes = value.get("passes")
    if (not isinstance(passes, list) or len(passes) != 2
            or any(not isinstance(item, dict) or set(item) != {"page_count", "terminal_cursor"}
                   or type(item["page_count"]) is not int or item["page_count"] < 1
                   or item["terminal_cursor"] is not None for item in passes)):
        raise RehearsalError("raw R2 inventory completeness evidence is invalid")
    if budget["observed_list_requests"] != sum(item["page_count"] for item in passes):
        raise RehearsalError("raw R2 inventory request observations are invalid")
    objects = value.get("objects")
    if not isinstance(objects, list) or len(objects) != value["inventory_count"]:
        raise RehearsalError("raw R2 inventory object count is invalid")
    previous_key = None
    validated: list[dict[str, Any]] = []
    for item in objects:
        if (not isinstance(item, dict) or set(item) != {"key", "size", "etag", "last_modified"}
                or not isinstance(item.get("key"), str) or not item["key"]
                or type(item.get("size")) is not int or item["size"] < 0
                or not isinstance(item.get("etag"), str) or not item["etag"]
                or not isinstance(item.get("last_modified"), str) or not item["last_modified"]
                or (previous_key is not None and item["key"] <= previous_key)):
            raise RehearsalError("raw R2 inventory object metadata is invalid")
        validated.append(dict(item))
        previous_key = item["key"]
    if _canonical_metadata_sha256(validated) != value["inventory_metadata_sha256"]:
        raise RehearsalError("raw R2 inventory metadata differs from its capture digest")
    return validated


def _bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def build_object_proof(snapshot_path: Path, snapshot_manifest_path: Path, *, expected_snapshot_manifest_sha256: str,
                       raw_inventory_path: Path, expected_raw_inventory_sha256: str,
                       proof_output: Path, binding_output: Path,
                       binding_id: str | None = None, bound_at: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create v2 proof and a provenance sidecar from already-transferred files."""
    if proof_output.expanduser().resolve() == binding_output.expanduser().resolve():
        raise RehearsalError("object proof and binding outputs must be distinct")
    snapshot = _file_fingerprint(snapshot_path)
    snapshot_manifest, snapshot_manifest_sha256 = _pinned_json(
        snapshot_manifest_path, expected_snapshot_manifest_sha256, "SQLite capture manifest"
    )
    _validate_snapshot_manifest(snapshot_manifest, snapshot)
    raw, raw_sha256 = _pinned_json(raw_inventory_path, expected_raw_inventory_sha256, "raw R2 inventory capture")
    objects = _validate_raw_inventory(raw)
    proof_objects = [{"key": item["key"], "size": item["size"]} for item in objects]
    scope = raw["scope"]
    proof = {
        "version": OBJECT_PROOF_VERSION,
        "source_snapshot": snapshot,
        "capture": {
            "method": "operator-readonly-list-head",
            "capture_id": raw["capture_id"],
            "inventory_complete": True,
            "inventory_count": len(proof_objects),
            "inventory_sha256": _inventory_sha256(proof_objects),
            "account_sha256": scope["account_sha256"],
            "bucket_sha256": scope["bucket_sha256"],
        },
        "objects": proof_objects,
    }
    proof_bytes = _bytes(proof)
    local_binding_id = binding_id or str(uuid.uuid4())
    local_bound_at = bound_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not _valid_uuid(local_binding_id) or not _valid_utc_timestamp(local_bound_at):
        raise RehearsalError("local object-proof binding identity is invalid")
    binding = {
        "version": BINDING_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "binding_id": local_binding_id,
        "bound_at": local_bound_at,
        "binding_assertion": "local_file_identity_only",
        "writer_freeze_attestation": False,
        "source_capture": {
            "capture_id": snapshot_manifest["capture_id"], "captured_at": snapshot_manifest["captured_at"],
            "adapter_version": snapshot_manifest["adapter_version"], "manifest_sha256": snapshot_manifest_sha256,
            "captured_snapshot": snapshot_manifest["snapshot"],
        },
        "source_snapshot": snapshot,
        "r2_capture": {
            "capture_id": raw["capture_id"], "captured_at": raw["captured_at"],
            "adapter_version": raw["adapter_version"], "raw_inventory_sha256": raw_sha256,
            "inventory_metadata_sha256": raw["inventory_metadata_sha256"],
        },
        "object_proof_sha256": hashlib.sha256(proof_bytes).hexdigest(),
    }
    proof_reservation = None
    binding_reservation = None
    try:
        proof_reservation = _ReservedReportOutput(proof_output)
        binding_reservation = _ReservedReportOutput(binding_output)
        proof_reservation.publish_bytes(proof_bytes)
        binding_reservation.publish_bytes(_bytes(binding))
        proof_reservation.retain()
        binding_reservation.retain()
    except Exception:
        if proof_reservation is not None:
            proof_reservation.abort()
        if binding_reservation is not None:
            binding_reservation.abort()
        raise
    return proof, binding


@click.command()
@click.option("--snapshot", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--snapshot-capture-manifest", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-snapshot-capture-manifest-sha256", required=True)
@click.option("--raw-inventory", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-raw-inventory-sha256", required=True)
@click.option("--proof-output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--binding-output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def cli(snapshot: Path, snapshot_capture_manifest: Path, expected_snapshot_capture_manifest_sha256: str,
        raw_inventory: Path, expected_raw_inventory_sha256: str, proof_output: Path, binding_output: Path) -> None:
    """Create strict v2 proof locally after independent artifact approval."""
    try:
        proof, binding = build_object_proof(
            snapshot, snapshot_capture_manifest,
            expected_snapshot_manifest_sha256=expected_snapshot_capture_manifest_sha256,
            raw_inventory_path=raw_inventory, expected_raw_inventory_sha256=expected_raw_inventory_sha256,
            proof_output=proof_output, binding_output=binding_output,
        )
        click.echo(json.dumps({"status": "bound_not_frozen", "inventory_count": proof["capture"]["inventory_count"],
                               "object_proof_sha256": binding["object_proof_sha256"]}, sort_keys=True))
    except Exception as error:
        raise click.ClickException("readonly_object_proof_binding_failed") from error


if __name__ == "__main__":
    cli()
