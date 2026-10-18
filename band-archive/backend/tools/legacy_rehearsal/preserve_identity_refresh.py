"""Refresh a reviewed legacy archive without replacing a claimed identity.

This is deliberately separate from :mod:`rehearsal`: its empty-target
one-shot import contract is not relaxed.  This entry point is PostgreSQL-only
and accepts a target only when its complete, reviewed pre-refresh profile is
already pinned in an operator-provided sidecar.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import click
from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import Connection, make_url

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tenant_backfill import LEGACY_BAND_SLUG, TenantBackfillError, validate_relationships
from tools.legacy_rehearsal.rehearsal import (
    RehearsalError, _ReservedReportOutput, _copy_and_backfill, _file_fingerprint,
    _head_revisions, _ordered_tables, _read_metadata,
    _stable_column_value, _validate_target_url, load_write_freeze_attestation,
    preflight_sqlite_snapshot, reconcile, safe_target_identity,
)


PROFILE_VERSION = "phase9-preserve-identity-refresh-v2"
OBJECT_PROOF_VERSION = "phase9-r2-readonly-manifest-v2"
OBJECT_DISPOSITION_APPROVAL_VERSION = "phase9-r2-readonly-disposition-approval-v1"
PROTECTED_TABLES = frozenset({
    "app_user", "auth_bootstrap_state", "band", "band_membership",
    "legacy_band_claim_token", "audit_event", "tenant_backfill_state",
    "band_request_email_outbox",
    # New-account policy receipts are immutable account security records,
    # never copied from an archive snapshot or cleared during refresh.
    "policy_acceptance_receipt",
    "band_availability_state", "band_availability_day", "band_personal_schedule_note",
    # Deletion tasks are operational history and must never be refreshed from
    # a source snapshot or emptied as part of identity-preserving refresh.
    "r2_deletion_task",
})
REFRESHED_TABLES = frozenset({
    "announcement", "gallery_image", "member", "rehearsal", "song",
    "song_suggestion", "media", "personal_log", "rehearsal_songs",
    "song_vote", "comment", "media_vote",
})
QUARANTINED_TABLES = frozenset({
    "multipart_upload_session", "multipart_upload_part",
    "personal_log_multipart_upload_session", "personal_log_multipart_upload_part",
})
MUST_BE_EMPTY_TABLES = frozenset({
    "band_creation_request", "band_invite_code", "song_suggestion_vote", "direct_upload_session",
    # Refresh replaces the legacy gallery rows.  Until the rehearsal workflow
    # has an explicit logo-preservation contract, accepting a selected logo
    # could cascade-delete that selection when its image is refreshed.
    "band_logo",
})
MANAGED_TABLES = PROTECTED_TABLES | REFRESHED_TABLES | QUARANTINED_TABLES | MUST_BE_EMPTY_TABLES


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _digest_rows(table: Any, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Hash complete typed rows without retaining their values in evidence."""
    columns = sorted(table.columns, key=lambda item: item.name)
    encoded_rows = []
    for row in rows:
        encoded = b"".join(
            column.name.encode("utf-8") + b"\0" + str(len(value)).encode("ascii") + b":" + value
            for column in columns
            for value in (_stable_column_value(row[column.name], column),)
        )
        encoded_rows.append(encoded)
    digest = hashlib.sha256()
    for encoded in sorted(encoded_rows):
        digest.update(b"table\0" + table.name.encode("utf-8") + b"\0" + encoded + b"\n")
    return {"count": len(encoded_rows), "sha256": digest.hexdigest()}


def _assert_schema(connection: Connection) -> MetaData:
    names = set(inspect(connection).get_table_names()) - {"alembic_version"}
    if names != MANAGED_TABLES:
        raise RehearsalError("refresh target has unsupported or unknown managed tables")
    revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if revisions != _head_revisions():
        raise RehearsalError("refresh target is not at the current Alembic head")
    return _read_metadata(connection)


def _validate_refresh_url(database_url: str, *, approved_tls_ca_sha256: str) -> None:
    """Reject connection indirection and require a pinned verify-full CA."""
    url = make_url(database_url)
    forbidden = {"host", "hostaddr", "port", "dbname", "database", "service", "servicefile", "options"}
    if url.get_backend_name() != "postgresql" or forbidden & set(url.query):
        raise RehearsalError("refresh target URL has an unsafe connection identity override")
    if (not isinstance(approved_tls_ca_sha256, str) or len(approved_tls_ca_sha256) != 64
            or any(character not in "0123456789abcdef" for character in approved_tls_ca_sha256)):
        raise RehearsalError("refresh target TLS CA approval is invalid")
    if url.query.get("sslmode") != "verify-full" or not isinstance(url.query.get("sslrootcert"), str):
        raise RehearsalError("refresh target must use sslmode=verify-full with an approved TLS CA")
    try:
        ca_path = Path(url.query["sslrootcert"])
        ca_digest = hashlib.sha256(ca_path.read_bytes()).hexdigest()
    except OSError as error:
        raise RehearsalError("refresh target approved TLS CA is unavailable") from error
    if ca_digest != approved_tls_ca_sha256:
        raise RehearsalError("refresh target TLS CA differs from the approved fingerprint")


def _all_rows(connection: Connection, table: Any) -> list[Mapping[str, Any]]:
    order = list(table.primary_key.columns) or list(table.columns)
    return list(connection.execute(select(*table.columns).order_by(*order)).mappings())


def _scoped_rows(connection: Connection, table: Any, band_id: int) -> list[Mapping[str, Any]]:
    if table.name == "rehearsal_songs":
        return list(connection.execute(text(
            "SELECT rs.* FROM rehearsal_songs rs JOIN rehearsal r ON r.id=rs.rehearsal_id "
            "JOIN song s ON s.id=rs.song_id WHERE r.band_id=:band_id AND s.band_id=:band_id "
            "ORDER BY rs.rehearsal_id, rs.song_id"
        ), {"band_id": band_id}).mappings())
    if "band_id" not in table.c:
        raise RehearsalError("refresh table scope is not explicit")
    order = list(table.primary_key.columns) or list(table.columns)
    return list(connection.execute(
        select(*table.columns).where(table.c.band_id == band_id).order_by(*order)
    ).mappings())


def _table_digests(connection: Connection, tables: frozenset[str], *, band_id: int | None = None) -> dict[str, dict[str, Any]]:
    metadata = _read_metadata(connection)
    if set(metadata.tables) != MANAGED_TABLES:
        raise RehearsalError("refresh table set changed while collecting evidence")
    result = {}
    for name in sorted(tables):
        table = metadata.tables[name]
        rows = _all_rows(connection, table) if band_id is None else _scoped_rows(connection, table, band_id)
        # The claim preserves the existing band row but an independently
        # restored v11 archive cannot assume its surrogate id. Scope itself is
        # proven separately; use a typed sentinel only inside the digest.
        if band_id is not None:
            rows = _canonical_band_rows(table, rows)
        result[name] = _digest_rows(table, rows)
    return result


def _canonical_band_rows(table: Any, rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Replace only the proven legacy band surrogate in digest material."""
    if "band_id" not in table.c:
        return rows
    return [{**dict(row), "band_id": 0} for row in rows]


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _require_pinned_file_digest(raw: bytes, expected_sha256: str, label: str) -> None:
    """Require an out-of-band pin before trusting a local operator sidecar."""
    if not _is_sha256(expected_sha256):
        raise RehearsalError(f"{label} expected SHA-256 is invalid")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RehearsalError(f"{label} differs from its externally approved SHA-256")


def _inventory_sha256(objects: list[Mapping[str, Any]]) -> str:
    """Hash a complete, key-sorted list/head inventory with no implied R2 access."""
    return _sha256(sorted(({"key": item["key"], "size": item["size"]} for item in objects),
                          key=lambda item: item["key"]))


def _validate_digest_map(value: Any, tables: frozenset[str], label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(tables):
        raise RehearsalError(f"reviewed profile {label} table set is invalid")
    for name, digest in value.items():
        if (not isinstance(digest, dict) or set(digest) != {"count", "sha256"}
                or type(digest["count"]) is not int or digest["count"] < 0
                or not isinstance(digest["sha256"], str) or len(digest["sha256"]) != 64):
            raise RehearsalError(f"reviewed profile {label} digest is invalid")
    return value


def load_reviewed_profile(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    """Read a pre-reviewed sidecar; this module intentionally cannot create it."""
    try:
        raw = path.read_bytes()
        if expected_sha256 is not None:
            _require_pinned_file_digest(raw, expected_sha256, "reviewed baseline profile")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError("reviewed baseline profile is not readable JSON") from error
    required = {"version", "target_writes_frozen", "provenance", "protected", "legacy_domain"}
    if not isinstance(value, dict) or set(value) != required or value.get("version") != PROFILE_VERSION:
        raise RehearsalError("reviewed baseline profile has an invalid shape")
    provenance = value["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"baseline_proof", "post_claim"}:
        raise RehearsalError("reviewed baseline profile provenance is invalid")
    evidence = {}
    for label, item in provenance.items():
        if (not isinstance(item, dict) or set(item) != {"file", "sha256"} or not isinstance(item["file"], str)
                or not item["file"] or Path(item["file"]).is_absolute() or ".." in Path(item["file"]).parts
                or not isinstance(item["sha256"], str) or len(item["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in item["sha256"])):
            raise RehearsalError("reviewed baseline profile provenance is invalid")
        try:
            raw = (path.parent / item["file"]).read_bytes()
            evidence[label] = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RehearsalError("reviewed baseline profile evidence is unavailable") from error
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise RehearsalError("reviewed baseline profile evidence digest differs")
    if value["target_writes_frozen"] is not True:
        raise RehearsalError("reviewed baseline profile does not attest target writers are frozen")
    protected, domain = value["protected"], value["legacy_domain"]
    if not isinstance(protected, dict) or set(protected) != {"table_digests"}:
        raise RehearsalError("reviewed baseline profile protected proof is invalid")
    if not isinstance(domain, dict) or set(domain) != {"table_digests"}:
        raise RehearsalError("reviewed baseline profile legacy-domain proof is invalid")
    _validate_digest_map(protected["table_digests"], PROTECTED_TABLES, "protected")
    _validate_digest_map(domain["table_digests"], REFRESHED_TABLES, "legacy-domain")
    baseline, post_claim = evidence["baseline_proof"], evidence["post_claim"]
    if (baseline.get("version") != "phase9-offline-v11-refresh-baseline-v1"
            or baseline.get("status") != "offline_v11_baseline_verified"
            or not isinstance(baseline.get("dump_sha256"), str)):
        raise RehearsalError("reviewed baseline profile offline v11 proof is invalid")
    embedded = post_claim.get("preserve_identity_refresh_profile") if isinstance(post_claim, dict) else None
    if (post_claim.get("status") != "postclaim_verified" or not isinstance(embedded, dict)
            or embedded.get("protected") != protected or embedded.get("legacy_domain") != domain
            or baseline.get("legacy_domain") != domain):
        raise RehearsalError("reviewed baseline profile post-claim evidence does not pin its typed digests")
    return value


def load_readonly_object_proof(path: Path, source_fingerprint: Mapping[str, Any], *, expected_sha256: str,
                               expected_account_sha256: str, expected_bucket_sha256: str) -> dict[str, Any]:
    """Load a complete, snapshot-bound list/head assertion; no storage client is available here."""
    try:
        raw = path.read_bytes()
        _require_pinned_file_digest(raw, expected_sha256, "read-only object manifest proof")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError("read-only object manifest proof is not readable JSON") from error
    if not isinstance(value, dict) or set(value) != {"version", "source_snapshot", "capture", "objects"}:
        raise RehearsalError("read-only object manifest proof has an invalid shape")
    if value["version"] != OBJECT_PROOF_VERSION or value["source_snapshot"] != dict(source_fingerprint):
        raise RehearsalError("read-only object manifest proof is not bound to the frozen source snapshot")
    capture = value["capture"]
    capture_keys = {"method", "capture_id", "inventory_complete", "inventory_count", "inventory_sha256",
                    "account_sha256", "bucket_sha256"}
    if (not isinstance(capture, dict) or set(capture) != capture_keys
            or capture["method"] != "operator-readonly-list-head"
            or not isinstance(capture["capture_id"], str) or not capture["capture_id"]
            or capture["inventory_complete"] is not True or type(capture["inventory_count"]) is not int
            or capture["inventory_count"] < 0 or not _is_sha256(capture["inventory_sha256"])
            or not _is_sha256(capture["account_sha256"]) or not _is_sha256(capture["bucket_sha256"])):
        raise RehearsalError("read-only object manifest proof capture identity is invalid")
    if (not _is_sha256(expected_account_sha256) or not _is_sha256(expected_bucket_sha256)
            or capture["account_sha256"] != expected_account_sha256
            or capture["bucket_sha256"] != expected_bucket_sha256):
        raise RehearsalError("read-only object manifest proof account or bucket differs from the approved identity")
    if not isinstance(value["objects"], list):
        raise RehearsalError("read-only object manifest proof has invalid objects")
    keys = set()
    for item in value["objects"]:
        if (not isinstance(item, dict) or set(item) != {"key", "size"} or not isinstance(item.get("key"), str)
                or not item["key"] or type(item["size"]) is not int or item["size"] < 0 or item["key"] in keys):
            raise RehearsalError("read-only object manifest proof has invalid objects")
        keys.add(item["key"])
    if (capture["inventory_count"] != len(value["objects"])
            or capture["inventory_sha256"] != _inventory_sha256(value["objects"])):
        raise RehearsalError("read-only object manifest proof inventory is incomplete or differs from its capture identity")
    return value


def load_approved_object_dispositions(path: Path, *, expected_sha256: str,
                                      source_fingerprint: Mapping[str, Any],
                                      object_proof: Mapping[str, Any]) -> dict[str, Any]:
    """Load reviewed, retain-only exceptions bound to one immutable inventory."""
    try:
        raw = path.read_bytes()
        _require_pinned_file_digest(raw, expected_sha256, "object disposition approval")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError("object disposition approval is not readable JSON") from error
    if (not isinstance(value, dict) or set(value) != {"version", "dispositions"}
            or value["version"] != OBJECT_DISPOSITION_APPROVAL_VERSION or not isinstance(value["dispositions"], list)):
        raise RehearsalError("object disposition approval has an invalid shape")
    capture = object_proof.get("capture") if isinstance(object_proof, Mapping) else None
    if not isinstance(capture, Mapping):
        raise RehearsalError("object disposition approval lacks a validated object-proof capture")
    required = {"key", "kind", "action", "source_snapshot", "account_sha256", "bucket_sha256",
                "inventory_sha256", "capture_id", "approval_provenance"}
    seen = set()
    for item in value["dispositions"]:
        if (not isinstance(item, dict) or set(item) != required or not isinstance(item["key"], str) or not item["key"]
                or item["key"] in seen or item["kind"] not in {"orphan", "retired"}
                or item["action"] != "retain_no_mutation" or item["source_snapshot"] != dict(source_fingerprint)
                or item["account_sha256"] != capture["account_sha256"]
                or item["bucket_sha256"] != capture["bucket_sha256"]
                or item["inventory_sha256"] != capture["inventory_sha256"]
                or item["capture_id"] != capture["capture_id"]
                or item["approval_provenance"] != "operator-reviewed"):
            raise RehearsalError("object disposition approval is not bound to the reviewed source and inventory")
        seen.add(item["key"])
    return value


def _assert_object_inventory_dispositions(reconciliation: Mapping[str, Any], object_proof: Mapping[str, Any],
                                          approved_dispositions: Mapping[str, Any] | None) -> None:
    """Require every surplus/retired object to have one retain-only reviewed disposition."""
    comparison = reconciliation.get("object_comparison", {})
    manifest = reconciliation.get("object_manifest", [])
    if not isinstance(comparison, Mapping) or not isinstance(manifest, list):
        raise RehearsalError("refresh reconciliation has no object lifecycle evidence")
    expected = {item["key"]: item for item in manifest if isinstance(item, Mapping) and isinstance(item.get("key"), str)}
    actual_keys = {item["key"] for item in object_proof.get("objects", []) if isinstance(item, Mapping)}
    retired_present = set(comparison.get("retired_still_present", []))
    orphaned = actual_keys - set(expected)
    required_dispositions = {
        **{key: "retired" for key in retired_present},
        **{key: "orphan" for key in orphaned},
    }
    approved = approved_dispositions.get("dispositions", []) if isinstance(approved_dispositions, Mapping) else []
    approved_by_key = {item["key"]: item for item in approved if isinstance(item, Mapping) and isinstance(item.get("key"), str)}
    if set(approved_by_key) != set(required_dispositions):
        raise RehearsalError("read-only object inventory has unreviewed or stale object dispositions")
    if any(approved_by_key[key].get("kind") != kind for key, kind in required_dispositions.items()):
        raise RehearsalError("read-only object inventory disposition kind differs from reconciliation")


def _target_identity(connection: Connection) -> tuple[int, int]:
    """Prove the single claimed legacy topology, returning user/band ids privately."""
    user_rows = connection.execute(text(
        "SELECT id FROM app_user WHERE status='active' AND email_verified IS TRUE AND is_platform_admin IS TRUE"
    )).scalars().all()
    if len(user_rows) != 1 or connection.execute(text("SELECT count(*) FROM app_user")).scalar_one() != 1:
        raise RehearsalError("refresh target has unexpected users")
    user_id = user_rows[0]
    bootstrap = connection.execute(text("SELECT claimed_by_user_id FROM auth_bootstrap_state")).scalars().all()
    if bootstrap != [user_id]:
        raise RehearsalError("refresh target bootstrap identity is inconsistent")
    bands = connection.execute(text(
        "SELECT id, created_by_user_id FROM band WHERE slug=:slug AND is_legacy_import IS TRUE"
    ), {"slug": LEGACY_BAND_SLUG}).mappings().all()
    if len(bands) != 1 or connection.execute(text("SELECT count(*) FROM band")).scalar_one() != 1:
        raise RehearsalError("refresh target has unexpected or ambiguous bands")
    band_id = bands[0]["id"]
    if bands[0]["created_by_user_id"] != user_id:
        raise RehearsalError("refresh target legacy-band ownership is inconsistent")
    owners = connection.execute(text(
        "SELECT user_id FROM band_membership WHERE band_id=:band_id AND role='owner' AND status='active'"
    ), {"band_id": band_id}).scalars().all()
    if owners != [user_id] or connection.execute(text("SELECT count(*) FROM band_membership")).scalar_one() != 1:
        raise RehearsalError("refresh target owner membership is inconsistent")
    claims = connection.execute(text(
        "SELECT redeemed_by_user_id FROM legacy_band_claim_token WHERE band_id=:band_id "
        "AND purpose='initial_owner' AND redeemed_at IS NOT NULL AND invalidated_at IS NULL"
    ), {"band_id": band_id}).scalars().all()
    if claims != [user_id] or connection.execute(text("SELECT count(*) FROM legacy_band_claim_token")).scalar_one() != 1:
        raise RehearsalError("refresh target ownership claim is inconsistent")
    if connection.execute(text("SELECT count(*) FROM audit_event")).scalar_one() != 3:
        raise RehearsalError("refresh target audit history is not the reviewed three-event history")
    marker = connection.execute(text(
        "SELECT key, status FROM tenant_backfill_state"
    )).all()
    if marker != [("phase4_tenant_scope", "validated")]:
        raise RehearsalError("refresh target tenant marker is inconsistent")
    return user_id, band_id


def _assert_empty_tables(connection: Connection, tables: frozenset[str], label: str) -> None:
    for name in tables:
        if connection.execute(text(f"SELECT count(*) FROM {_quote(name)}")).scalar_one() != 0:
            raise RehearsalError(f"{label} contains unsupported new content")


def _assert_profile(connection: Connection, profile: Mapping[str, Any]) -> int:
    _assert_schema(connection)
    _, band_id = _target_identity(connection)
    _assert_empty_tables(connection, MUST_BE_EMPTY_TABLES | QUARANTINED_TABLES, "refresh target")
    actual_protected = _table_digests(connection, PROTECTED_TABLES)
    actual_domain = _table_digests(connection, REFRESHED_TABLES, band_id=band_id)
    if actual_protected != profile["protected"]["table_digests"]:
        raise RehearsalError("refresh target protected identity digest differs from reviewed baseline")
    if actual_domain != profile["legacy_domain"]["table_digests"]:
        raise RehearsalError("refresh target legacy-domain digest differs from reviewed baseline")
    return band_id


def _source_band_id(connection: Connection) -> int:
    rows = connection.execute(text(
        "SELECT id, created_by_user_id FROM band WHERE slug=:slug AND is_legacy_import IS TRUE"
    ), {"slug": LEGACY_BAND_SLUG}).mappings().all()
    if len(rows) != 1 or rows[0]["created_by_user_id"] is not None:
        raise RehearsalError("source backfill did not create one ownerless legacy band")
    if connection.execute(text("SELECT count(*) FROM band")).scalar_one() != 1:
        raise RehearsalError("source contains unsupported additional bands")
    _assert_empty_tables(connection, MUST_BE_EMPTY_TABLES, "source")
    for table in PROTECTED_TABLES - {"band", "tenant_backfill_state"}:
        if connection.execute(text(f"SELECT count(*) FROM {_quote(table)}")).scalar_one() != 0:
            raise RehearsalError("source contains unsupported identity state")
    if connection.execute(text("SELECT count(*) FROM tenant_backfill_state WHERE key='phase4_tenant_scope' "
                               "AND status='validated'")).scalar_one() != 1:
        raise RehearsalError("source tenant backfill marker is inconsistent")
    for table in REFRESHED_TABLES - {"rehearsal_songs"}:
        if "created_by_membership_id" in _read_metadata(connection).tables[table].c:
            if connection.execute(text(
                f"SELECT count(*) FROM {_quote(table)} WHERE created_by_membership_id IS NOT NULL"
            )).scalar_one():
                raise RehearsalError("source content requires an unsupported membership identity mapping")
    return rows[0]["id"]


def _assert_source_has_no_external_identity_references(connection: Connection) -> None:
    """Only the generated legacy band relationship may cross into identity."""
    metadata = _read_metadata(connection)
    for table in metadata.sorted_tables:
        if table.name not in REFRESHED_TABLES:
            continue
        for foreign_key in table.foreign_key_constraints:
            referred = foreign_key.referred_table.name
            columns = [column.name for column in foreign_key.columns]
            if referred not in PROTECTED_TABLES:
                continue
            if referred == "band" and columns == ["band_id"]:
                continue
            predicate = " OR ".join(f"{_quote(column)} IS NOT NULL" for column in columns)
            if connection.execute(text(f"SELECT count(*) FROM {_quote(table.name)} WHERE {predicate}")).scalar_one():
                raise RehearsalError("source content requires an unsupported external identity mapping")


def _source_rows(connection: Connection, table: Any, source_band_id: int, target_band_id: int) -> list[dict[str, Any]]:
    rows = _scoped_rows(connection, table, source_band_id)
    converted = []
    for row in rows:
        item = dict(row)
        if "band_id" in item:
            item["band_id"] = target_band_id
        converted.append(item)
    return converted


def _delete_legacy_domain(connection: Connection, metadata: MetaData, band_id: int) -> None:
    # Delete only rows proven to be in the single legacy graph, children first.
    for table in reversed(_ordered_tables(metadata)):
        if table.name not in REFRESHED_TABLES:
            continue
        if table.name == "rehearsal_songs":
            connection.execute(text(
                "DELETE FROM rehearsal_songs WHERE rehearsal_id IN "
                "(SELECT id FROM rehearsal WHERE band_id=:band_id) OR song_id IN "
                "(SELECT id FROM song WHERE band_id=:band_id)"
            ), {"band_id": band_id})
        else:
            connection.execute(table.delete().where(table.c.band_id == band_id))


def _insert_source_domain(source: Connection, target: Connection, source_band_id: int, target_band_id: int) -> dict[str, int]:
    source_metadata, target_metadata = _read_metadata(source), _read_metadata(target)
    inserted = {}
    for source_table in _ordered_tables(source_metadata):
        if source_table.name not in REFRESHED_TABLES:
            continue
        target_table = target_metadata.tables[source_table.name]
        if {column.name for column in source_table.columns} != {column.name for column in target_table.columns}:
            raise RehearsalError("refresh source and target column contract differs")
        rows = _source_rows(source, source_table, source_band_id, target_band_id)
        if rows:
            target.execute(target_table.insert(), rows)
        inserted[source_table.name] = len(rows)
    return inserted


def _advance_postgres_sequences_monotonic(connection: Connection, metadata: MetaData) -> None:
    """Advance only: PostgreSQL ``setval`` survives rollback, so never lower it.

    A later failure can leave a harmless gap but must not reuse a reserved or
    previously issued id. This intentionally replaces the empty-import helper,
    whose exact-reset behaviour is appropriate only for a disposable target.
    """
    preparer = connection.dialect.identifier_preparer
    for table in metadata.sorted_tables:
        if table.name not in REFRESHED_TABLES:
            continue
        primary_key = list(table.primary_key.columns)
        if len(primary_key) != 1:
            continue
        column = primary_key[0]
        sequence = connection.execute(text("SELECT pg_get_serial_sequence(:table_name, :column_name)"), {
            "table_name": table.name, "column_name": column.name,
        }).scalar_one()
        if not sequence:
            continue
        maximum = connection.execute(text(
            f"SELECT max({preparer.quote(column.name)}) FROM {preparer.quote(table.name)}"
        )).scalar_one()
        if maximum is None:
            continue
        sequence_state = connection.execute(text(
            "SELECT s.last_value, s.schemaname FROM pg_sequences s "
            "JOIN pg_class c ON c.relname=s.sequencename "
            "JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname=s.schemaname "
            "WHERE c.oid=to_regclass(:sequence)"
        ), {"sequence": sequence}).one_or_none()
        if sequence_state is None or sequence_state.schemaname != connection.execute(text("SELECT current_schema()")).scalar_one():
            raise RehearsalError("refresh sequence is outside the expected schema")
        current = sequence_state.last_value
        if current is None or maximum > current:
            connection.execute(text("SELECT setval(:sequence, :value, true)"), {
                "sequence": sequence, "value": maximum,
            })


def _source_domain_digests(connection: Connection, source_band_id: int, target_band_id: int) -> dict[str, dict[str, Any]]:
    metadata = _read_metadata(connection)
    return {
        name: _digest_rows(metadata.tables[name], _canonical_band_rows(
            metadata.tables[name], _source_rows(connection, metadata.tables[name], source_band_id, target_band_id)))
        for name in sorted(REFRESHED_TABLES)
    }


def run_preserve_identity_refresh(source_sqlite: str, target_url: str, *, expected_target_host: str,
                                  expected_target_database: str, expected_target_schema: str,
                                  source_write_freeze_attestation: Mapping[str, Any],
                                  reviewed_profile: Mapping[str, Any], object_proof: Mapping[str, Any],
                                  approved_object_dispositions: Mapping[str, Any] | None = None,
                                  approved_tls_ca_sha256: str = "",
                                  dry_run: bool = False,
                                  report_publisher: Any = None, committed_marker_publisher: Any = None) -> dict[str, Any]:
    """Run one reviewed refresh.  Caller must have loaded strict sidecars first."""
    _validate_refresh_url(target_url, approved_tls_ca_sha256=approved_tls_ca_sha256)
    identity = _validate_target_url(target_url, expected_target_host=expected_target_host,
                                    expected_target_database=expected_target_database,
                                    expected_target_schema=expected_target_schema,
                                    allow_sqlite_target=False, sqlite_test_target_path=None)
    if identity["dialect"] != "postgresql" or make_url(target_url).get_backend_name() != "postgresql":
        raise RehearsalError("preserve-identity refresh is PostgreSQL-only")
    source_path = preflight_sqlite_snapshot(source_sqlite)
    source_fingerprint = _file_fingerprint(source_path)
    write_freeze = load_write_freeze_attestation(Path(source_write_freeze_attestation), source_fingerprint) \
        if isinstance(source_write_freeze_attestation, (str, Path)) else source_write_freeze_attestation
    # Validate the mapping form too; this does not retain its contents.
    from tools.legacy_rehearsal.rehearsal import validate_write_freeze_attestation
    write_freeze = validate_write_freeze_attestation(write_freeze, source_fingerprint)
    engine = create_engine(target_url, future=True, hide_parameters=True)
    try:
        with tempfile.TemporaryDirectory(prefix="band-archive-preserve-identity-refresh-") as directory:
            source_copy, backfill, source_files = _copy_and_backfill(
                source_sqlite, Path(directory), expected_source_fingerprint=source_fingerprint,
            )
            source_engine = create_engine(f"sqlite:///{source_copy.as_posix()}", future=True)
            try:
                with source_engine.connect() as source:
                    source_band_id = _source_band_id(source)
                    _assert_source_has_no_external_identity_references(source)
                    with engine.connect() as target:
                        if target.execute(text("SELECT current_schema()")).scalar_one() != identity["schema"]:
                            raise RehearsalError("refresh target schema does not match the explicit expected schema")
                        if target.execute(text("SELECT current_database()")).scalar_one() != expected_target_database:
                            raise RehearsalError("refresh target database does not match the explicit expected identity")
                        target_band_id = _assert_profile(target, reviewed_profile)
                    expected_domain = _source_domain_digests(source, source_band_id, target_band_id)
                    result = {
                        "mode": "preserve_identity_refresh", "dry_run": dry_run,
                        "target": safe_target_identity(target_url), "source_files": source_files,
                        "source_write_freeze": write_freeze, "backfill": backfill,
                        "reviewed_profile": {"version": reviewed_profile["version"],
                                             "evidence_sha256": {key: value["sha256"] for key, value in reviewed_profile["provenance"].items()},
                                             "sha256": _sha256(reviewed_profile)},
                        "expected_domain": expected_domain,
                    }
                    if dry_run:
                        result["source_files"]["source_after"] = _file_fingerprint(source_path)
                        if result["source_files"]["source_before"] != result["source_files"]["source_after"]:
                            raise RehearsalError("source SQLite snapshot changed during refresh dry run")
                        if report_publisher:
                            report_publisher(result)
                        return result
                    prepared_report_sha256 = None
                    with engine.begin() as target:
                        target.execute(text("SET LOCAL lock_timeout = '1s'"))
                        target.execute(text("SELECT pg_advisory_xact_lock(hashtext('phase9-preserve-identity-refresh-v1'))"))
                        target.execute(text("LOCK TABLE " + ", ".join(_quote(name) for name in sorted(MANAGED_TABLES)) +
                                            " IN SHARE ROW EXCLUSIVE MODE"))
                        if target.execute(text("SELECT current_database()")).scalar_one() != expected_target_database:
                            raise RehearsalError("refresh target database changed before lock")
                        target_band_id = _assert_profile(target, reviewed_profile)
                        protected_before = _table_digests(target, PROTECTED_TABLES)
                        _delete_legacy_domain(target, _read_metadata(target), target_band_id)
                        inserted = _insert_source_domain(source, target, source_band_id, target_band_id)
                        _advance_postgres_sequences_monotonic(target, _read_metadata(target))
                        if _table_digests(target, PROTECTED_TABLES) != protected_before:
                            raise RehearsalError("refresh changed protected identity state; transaction rolled back")
                        if _table_digests(target, REFRESHED_TABLES, band_id=target_band_id) != expected_domain:
                            raise RehearsalError("refresh domain digest differs from frozen source; transaction rolled back")
                        _assert_empty_tables(target, MUST_BE_EMPTY_TABLES | QUARANTINED_TABLES, "refresh target")
                        reconciliation = reconcile(target, actual_objects=object_proof["objects"])
                        if reconciliation["foreign_key_orphans"] or reconciliation["object_defects"]:
                            raise RehearsalError("refresh reconciliation reported relationship or object defects; transaction rolled back")
                        if (reconciliation["song_votes"]["cached_score_mismatches"]
                                or reconciliation["media_votes"]["cached_score_mismatches"]):
                            raise RehearsalError("refresh reconciliation reported cached vote-score mismatch; transaction rolled back")
                        comments = reconciliation["comments"]
                        if comments["missing_parents"] or comments["root_target_fields"] != comments["roots"]:
                            raise RehearsalError("refresh reconciliation reported invalid comment roots; transaction rolled back")
                        try:
                            relationship_defects, _ = validate_relationships(target, target_band_id)
                        except TenantBackfillError as error:
                            raise RehearsalError("refresh reconciliation reported invalid comment or tenant graph; transaction rolled back") from error
                        if relationship_defects:
                            raise RehearsalError("refresh reconciliation reported cross-scope graph defects; transaction rolled back")
                        comparison = reconciliation.get("object_comparison", {})
                        if comparison.get("missing_required") or comparison.get("size_mismatches"):
                            raise RehearsalError("refresh read-only object proof differs from refreshed manifest; transaction rolled back")
                        _assert_object_inventory_dispositions(reconciliation, object_proof, approved_object_dispositions)
                        result.update({"inserted": inserted, "after_domain": expected_domain,
                                       "protected_identity_unchanged": True,
                                       "reconciliation": reconciliation, "status": "prepared"})
                        result["source_files"]["source_after"] = _file_fingerprint(source_path)
                        if result["source_files"]["source_before"] != result["source_files"]["source_after"]:
                            raise RehearsalError("source SQLite snapshot changed during refresh")
                        prepared_report_sha256 = report_publisher(result) if report_publisher else None
                    # ``engine.begin`` has committed successfully at this point.
                    # Reconnect to prove the durable state before issuing the
                    # separate committed marker; the immutable prepared report
                    # must never claim success by itself.
                    with engine.connect() as target:
                        if (target.execute(text("SELECT current_schema()")).scalar_one() != identity["schema"]
                                or target.execute(text("SELECT current_database()")).scalar_one() != expected_target_database):
                            raise RehearsalError("refresh target identity changed after commit")
                        if _table_digests(target, PROTECTED_TABLES) != protected_before:
                            raise RehearsalError("refresh protected identity differs after commit")
                        if _table_digests(target, REFRESHED_TABLES, band_id=target_band_id) != expected_domain:
                            raise RehearsalError("refresh domain differs after commit")
                    if committed_marker_publisher:
                        committed_marker_publisher({
                            "version": "phase9-preserve-identity-refresh-commit-v1",
                            "status": "committed", "prepared_report_sha256": prepared_report_sha256,
                            "target": safe_target_identity(target_url),
                            "protected_identity_unchanged": True, "after_domain": expected_domain,
                        })
                    return result
            finally:
                source_engine.dispose()
    except RehearsalError:
        raise
    except Exception as error:
        # Driver errors can contain a DSN.  The CLI intentionally exposes only
        # the fixed failure class while retaining the causal chain locally.
        raise RehearsalError("preserve-identity refresh failed") from error
    finally:
        engine.dispose()


@click.command()
@click.option("--source-sqlite", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--target-url", required=True, envvar="REHEARSAL_TARGET_URL")
@click.option("--expected-target-host", required=True)
@click.option("--expected-target-database", required=True)
@click.option("--expected-target-schema", required=True)
@click.option("--approved-tls-ca-sha256", required=True)
@click.option("--write-freeze-attestation", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--reviewed-baseline-profile", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-reviewed-baseline-profile-sha256", required=True)
@click.option("--readonly-object-manifest-proof", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-readonly-object-manifest-proof-sha256", required=True)
@click.option("--expected-object-account-sha256", required=True)
@click.option("--expected-object-bucket-sha256", required=True)
@click.option("--approved-object-disposition-manifest", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-approved-object-disposition-manifest-sha256", required=True)
@click.option("--report-output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True)
def cli(source_sqlite: Path, target_url: str, expected_target_host: str, expected_target_database: str,
        expected_target_schema: str, approved_tls_ca_sha256: str, write_freeze_attestation: Path, reviewed_baseline_profile: Path,
        expected_reviewed_baseline_profile_sha256: str, readonly_object_manifest_proof: Path,
        expected_readonly_object_manifest_proof_sha256: str, expected_object_account_sha256: str,
        expected_object_bucket_sha256: str, approved_object_disposition_manifest: Path,
        expected_approved_object_disposition_manifest_sha256: str, report_output: Path, dry_run: bool) -> None:
    """Refresh only an already-reviewed, claimed staging legacy archive."""
    reservation = None
    committed_reservation = None
    try:
        reservation = _ReservedReportOutput(report_output)
        if not dry_run:
            committed_reservation = _ReservedReportOutput(Path(f"{report_output}.committed"))
        # Pin every operator artifact before the target engine is opened.  The
        # pins attest local review, not authenticity of a remote R2 response.
        profile = load_reviewed_profile(reviewed_baseline_profile, expected_reviewed_baseline_profile_sha256)
        source_fingerprint = _file_fingerprint(preflight_sqlite_snapshot(str(source_sqlite)))
        attestation = load_write_freeze_attestation(write_freeze_attestation, source_fingerprint)
        object_proof = load_readonly_object_proof(
            readonly_object_manifest_proof, source_fingerprint,
            expected_sha256=expected_readonly_object_manifest_proof_sha256,
            expected_account_sha256=expected_object_account_sha256,
            expected_bucket_sha256=expected_object_bucket_sha256,
        )
        object_dispositions = load_approved_object_dispositions(
            approved_object_disposition_manifest,
            expected_sha256=expected_approved_object_disposition_manifest_sha256,
            source_fingerprint=source_fingerprint, object_proof=object_proof,
        )
        run_preserve_identity_refresh(
            str(source_sqlite), target_url, expected_target_host=expected_target_host,
            expected_target_database=expected_target_database, expected_target_schema=expected_target_schema,
            source_write_freeze_attestation=attestation, reviewed_profile=profile, object_proof=object_proof,
            approved_object_dispositions=object_dispositions,
            approved_tls_ca_sha256=approved_tls_ca_sha256, dry_run=dry_run,
            report_publisher=reservation.publish,
            committed_marker_publisher=committed_reservation.publish if committed_reservation else None,
        )
        prepared_sha = reservation.retain()
        if committed_reservation is not None:
            click.echo(f"prepared_report_sha256={prepared_sha} committed_marker_sha256={committed_reservation.retain()}")
        else:
            click.echo(f"report_written sha256={prepared_sha}")
    except Exception as error:
        if committed_reservation is not None:
            committed_reservation.abort()
        if reservation is not None:
            # Once the prepared report is durable, a commit or committed-marker
            # failure must leave it behind as evidence.  Its status is still
            # prepared, so it cannot be mistaken for a completed refresh.
            if reservation.published:
                reservation.retain()
            reservation.abort()
        raise click.ClickException("preserve_identity_refresh_failed") from error


if __name__ == "__main__":
    cli()
