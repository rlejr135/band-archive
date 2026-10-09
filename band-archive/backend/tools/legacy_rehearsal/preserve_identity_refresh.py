"""Refresh a reviewed legacy archive without replacing a claimed identity.

This is deliberately separate from :mod:`rehearsal`: its empty-target
one-shot import contract is not relaxed.  This entry point is PostgreSQL-only
and accepts a target only when its complete, reviewed pre-refresh profile is
already pinned in an operator-provided sidecar.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

import click
from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import Connection, make_url

from tenant_backfill import LEGACY_BAND_SLUG, TenantBackfillError, validate_relationships
from tools.legacy_rehearsal.rehearsal import (
    RehearsalError, _ReservedReportOutput, _copy_and_backfill, _file_fingerprint,
    _head_revisions, _ordered_tables, _read_metadata,
    _stable_column_value, _validate_target_url, load_write_freeze_attestation,
    preflight_sqlite_snapshot, reconcile, safe_target_identity,
)


PROFILE_VERSION = "phase9-preserve-identity-refresh-v1"
OBJECT_PROOF_VERSION = "phase9-r2-readonly-manifest-v1"
PROTECTED_TABLES = frozenset({
    "app_user", "auth_bootstrap_state", "band", "band_membership",
    "legacy_band_claim_token", "audit_event", "tenant_backfill_state",
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


def _validate_refresh_url(database_url: str) -> None:
    """Reject libpq query overrides before an engine can interpret them."""
    url = make_url(database_url)
    forbidden = {"host", "hostaddr", "port", "dbname", "database", "service", "servicefile", "options"}
    if url.get_backend_name() != "postgresql" or forbidden & set(url.query):
        raise RehearsalError("refresh target URL has an unsafe connection identity override")


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
        result[name] = _digest_rows(table, rows)
    return result


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_digest_map(value: Any, tables: frozenset[str], label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(tables):
        raise RehearsalError(f"reviewed profile {label} table set is invalid")
    for name, digest in value.items():
        if (not isinstance(digest, dict) or set(digest) != {"count", "sha256"}
                or type(digest["count"]) is not int or digest["count"] < 0
                or not isinstance(digest["sha256"], str) or len(digest["sha256"]) != 64):
            raise RehearsalError(f"reviewed profile {label} digest is invalid")
    return value


def load_reviewed_profile(path: Path) -> dict[str, Any]:
    """Read a pre-reviewed sidecar; this module intentionally cannot create it."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError("reviewed baseline profile is not readable JSON") from error
    required = {"version", "target_writes_frozen", "provenance", "protected", "legacy_domain"}
    if not isinstance(value, dict) or set(value) != required or value.get("version") != PROFILE_VERSION:
        raise RehearsalError("reviewed baseline profile has an invalid shape")
    provenance = value["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"v11_restore", "v12_apply", "post_claim"}:
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
    v11, v12, post_claim = evidence["v11_restore"], evidence["v12_apply"], evidence["post_claim"]
    if (v11.get("status") != "local_restore_verified" or not v11.get("source_before_after_identical")
            or not v11.get("restored_matches_source") or v12.get("status") != "staging_v12_verified"
            or v12.get("expected_source_object_sha256") != v11.get("restored", {}).get("object_sha256")):
        raise RehearsalError("reviewed baseline profile v11/v12 provenance does not form a verified chain")
    embedded = post_claim.get("preserve_identity_refresh_profile") if isinstance(post_claim, dict) else None
    if (post_claim.get("status") != "postclaim_verified" or not isinstance(embedded, dict)
            or embedded.get("protected") != protected or embedded.get("legacy_domain") != domain):
        raise RehearsalError("reviewed baseline profile post-claim evidence does not pin its typed digests")
    return value


def load_readonly_object_proof(path: Path, source_fingerprint: Mapping[str, Any]) -> dict[str, Any]:
    """Load a snapshot-bound list/head result; no storage client is available here."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError("read-only object manifest proof is not readable JSON") from error
    if not isinstance(value, dict) or set(value) != {"version", "source_snapshot", "objects"}:
        raise RehearsalError("read-only object manifest proof has an invalid shape")
    if value["version"] != OBJECT_PROOF_VERSION or value["source_snapshot"] != dict(source_fingerprint):
        raise RehearsalError("read-only object manifest proof is not bound to the frozen source snapshot")
    if not isinstance(value["objects"], list):
        raise RehearsalError("read-only object manifest proof has invalid objects")
    for item in value["objects"]:
        if (not isinstance(item, dict) or set(item) - {"key", "size"} or not isinstance(item.get("key"), str)
                or not item["key"] or ("size" in item and (type(item["size"]) is not int or item["size"] < 0))):
            raise RehearsalError("read-only object manifest proof has invalid objects")
    return value


def _target_identity(connection: Connection) -> tuple[int, int]:
    """Prove the single claimed legacy topology, returning user/band ids privately."""
    user_rows = connection.execute(text(
        "SELECT id FROM app_user WHERE status='active' AND email_verified=1 AND is_platform_admin=1"
    )).scalars().all()
    if len(user_rows) != 1 or connection.execute(text("SELECT count(*) FROM app_user")).scalar_one() != 1:
        raise RehearsalError("refresh target has unexpected users")
    user_id = user_rows[0]
    bootstrap = connection.execute(text("SELECT claimed_by_user_id FROM auth_bootstrap_state")).scalars().all()
    if bootstrap != [user_id]:
        raise RehearsalError("refresh target bootstrap identity is inconsistent")
    bands = connection.execute(text(
        "SELECT id, created_by_user_id FROM band WHERE slug=:slug AND is_legacy_import=1"
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
        "SELECT id, created_by_user_id FROM band WHERE slug=:slug AND is_legacy_import=1"
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
        current = connection.execute(text(
            "SELECT last_value FROM pg_sequences WHERE schemaname=current_schema() "
            "AND sequencename=regexp_replace(:sequence, '^.*\\.', '')"
        ), {"sequence": sequence}).scalar_one_or_none()
        if current is None:
            raise RehearsalError("refresh sequence is outside the expected schema")
        if maximum > current:
            connection.execute(text("SELECT setval(:sequence, :value, true)"), {
                "sequence": sequence, "value": maximum,
            })


def _source_domain_digests(connection: Connection, source_band_id: int, target_band_id: int) -> dict[str, dict[str, Any]]:
    metadata = _read_metadata(connection)
    return {
        name: _digest_rows(metadata.tables[name], _source_rows(connection, metadata.tables[name], source_band_id, target_band_id))
        for name in sorted(REFRESHED_TABLES)
    }


def run_preserve_identity_refresh(source_sqlite: str, target_url: str, *, expected_target_host: str,
                                  expected_target_database: str, expected_target_schema: str,
                                  source_write_freeze_attestation: Mapping[str, Any],
                                  reviewed_profile: Mapping[str, Any], object_proof: Mapping[str, Any], dry_run: bool = False,
                                  report_publisher: Any = None) -> dict[str, Any]:
    """Run one reviewed refresh.  Caller must have loaded strict sidecars first."""
    _validate_refresh_url(target_url)
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
                        result.update({"inserted": inserted, "after_domain": expected_domain,
                                       "protected_identity_unchanged": True,
                                       "reconciliation": reconciliation})
                        result["source_files"]["source_after"] = _file_fingerprint(source_path)
                        if result["source_files"]["source_before"] != result["source_files"]["source_after"]:
                            raise RehearsalError("source SQLite snapshot changed during refresh")
                        if report_publisher:
                            report_publisher(result)
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
@click.option("--write-freeze-attestation", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--reviewed-baseline-profile", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--readonly-object-manifest-proof", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--report-output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True)
def cli(source_sqlite: Path, target_url: str, expected_target_host: str, expected_target_database: str,
        expected_target_schema: str, write_freeze_attestation: Path, reviewed_baseline_profile: Path,
        readonly_object_manifest_proof: Path, report_output: Path, dry_run: bool) -> None:
    """Refresh only an already-reviewed, claimed staging legacy archive."""
    reservation = None
    try:
        reservation = _ReservedReportOutput(report_output)
        profile = load_reviewed_profile(reviewed_baseline_profile)
        source_fingerprint = _file_fingerprint(preflight_sqlite_snapshot(str(source_sqlite)))
        attestation = load_write_freeze_attestation(write_freeze_attestation, source_fingerprint)
        object_proof = load_readonly_object_proof(readonly_object_manifest_proof, source_fingerprint)
        run_preserve_identity_refresh(
            str(source_sqlite), target_url, expected_target_host=expected_target_host,
            expected_target_database=expected_target_database, expected_target_schema=expected_target_schema,
            source_write_freeze_attestation=attestation, reviewed_profile=profile, object_proof=object_proof, dry_run=dry_run,
            report_publisher=reservation.publish,
        )
        click.echo(f"report_written sha256={reservation.retain()}")
    except Exception as error:
        if reservation is not None:
            reservation.abort()
        raise click.ClickException("preserve_identity_refresh_failed") from error


if __name__ == "__main__":
    cli()
