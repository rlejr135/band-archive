"""Capture a fail-closed, read-only R2 inventory for later local binding.

This module deliberately has no application import, database access, or write
operation.  It is intended to run only on the approved Fly machine, where the
existing S3 credentials remain process environment variables.  The resulting
artifact contains object keys and is private operator evidence: stdout carries
only its digest and count.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
import uuid

import click

from tools.legacy_rehearsal.rehearsal import RehearsalError, _ReservedReportOutput


RAW_INVENTORY_VERSION = "phase9-r2-readonly-inventory-capture-v1"
ADAPTER_VERSION = "phase9-r2-readonly-capture-adapter-v1"
DEFAULT_MAX_OBJECTS = 1_000
LIST_PAGE_SIZE = 1_000
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 15
MAX_RETRIES = 2
_R2_HOST_SUFFIX = ".r2.cloudflarestorage.com"
_ACCOUNT_RE = re.compile(r"^[a-z0-9]{8,64}$")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validated_capture_id(value: str | None) -> str:
    candidate = value or str(uuid.uuid4())
    try:
        if str(uuid.UUID(candidate)) != candidate:
            raise ValueError
    except ValueError as error:
        raise RehearsalError("R2 capture identity is invalid") from error
    return candidate


def _account_from_endpoint(endpoint: str) -> str:
    """Derive the R2 account only from Cloudflare's canonical HTTPS host."""
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or not host.endswith(_R2_HOST_SUFFIX)):
        raise RehearsalError("R2 endpoint is not an unambiguous canonical HTTPS account endpoint")
    account = host.removesuffix(_R2_HOST_SUFFIX)
    if not _ACCOUNT_RE.fullmatch(account):
        raise RehearsalError("R2 endpoint account identity is invalid")
    return account


def _normalized_last_modified(value: Any) -> str:
    if not isinstance(value, datetime):
        raise RehearsalError("R2 object metadata lacks a valid LastModified value")
    if value.tzinfo is None:
        raise RehearsalError("R2 object metadata LastModified lacks a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _object_metadata(listed: Mapping[str, Any], headed: Mapping[str, Any]) -> dict[str, Any]:
    key, listed_size = listed.get("Key"), listed.get("Size")
    listed_etag = listed.get("ETag")
    if (not isinstance(key, str) or not key or type(listed_size) is not int or listed_size < 0
            or not isinstance(listed_etag, str) or not listed_etag):
        raise RehearsalError("R2 list response has invalid object metadata")
    size, etag = headed.get("ContentLength"), headed.get("ETag")
    listed_modified = _normalized_last_modified(listed.get("LastModified"))
    headed_modified = _normalized_last_modified(headed.get("LastModified"))
    if (type(size) is not int or size < 0 or size != listed_size or not isinstance(etag, str) or not etag
            or etag != listed_etag or headed_modified != listed_modified):
        raise RehearsalError("R2 list/head object metadata differs")
    return {
        "key": key,
        "size": size,
        "etag": etag,
        "last_modified": headed_modified,
    }


def capture_inventory_pass(client: Any, bucket: str, *, max_objects: int) -> tuple[list[dict[str, Any]], dict[str, int | None]]:
    """Enumerate every object once, requiring a valid terminal cursor."""
    if not isinstance(bucket, str) or not bucket:
        raise RehearsalError("R2 bucket identity is invalid")
    objects: list[dict[str, Any]] = []
    seen_keys, seen_tokens = set(), set()
    continuation: str | None = None
    previous_key: str | None = None
    pages = 0
    max_pages = max_objects + 1  # permits one final empty terminal response only.
    while True:
        request = {"Bucket": bucket, "MaxKeys": LIST_PAGE_SIZE}
        if continuation is not None:
            request["ContinuationToken"] = continuation
        response = client.list_objects_v2(**request)
        pages += 1
        if pages > max_pages:
            raise RehearsalError("R2 list request budget reached before a terminal cursor")
        if not isinstance(response, Mapping) or type(response.get("IsTruncated")) is not bool:
            raise RehearsalError("R2 list response has no explicit completeness state")
        contents = response.get("Contents", [])
        if not isinstance(contents, list):
            raise RehearsalError("R2 list response contents are invalid")
        if response["IsTruncated"] and not contents:
            raise RehearsalError("R2 list continuation made no object progress")
        for listed in contents:
            if not isinstance(listed, Mapping):
                raise RehearsalError("R2 list response object is invalid")
            key = listed.get("Key")
            if not isinstance(key, str) or key in seen_keys or (previous_key is not None and key <= previous_key):
                raise RehearsalError("R2 list response key ordering is incomplete or duplicated")
            if len(objects) >= max_objects:
                raise RehearsalError("R2 object limit reached; capture is intentionally incomplete")
            headed = client.head_object(Bucket=bucket, Key=key)
            if not isinstance(headed, Mapping):
                raise RehearsalError("R2 head response is invalid")
            objects.append(_object_metadata(listed, headed))
            seen_keys.add(key)
            previous_key = key
        next_token = response.get("NextContinuationToken")
        if response["IsTruncated"] is False:
            if next_token is not None:
                raise RehearsalError("R2 list terminal cursor is ambiguous")
            return objects, {"page_count": pages, "terminal_cursor": None}
        if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
            raise RehearsalError("R2 list continuation cursor is missing or non-progressing")
        seen_tokens.add(next_token)
        continuation = next_token


def capture_two_pass_inventory(client: Any, *, bucket: str, endpoint: str, max_objects: int,
                               capture_id: str | None = None, captured_at: str | None = None) -> dict[str, Any]:
    """Require two whole list/head passes to match before retaining evidence."""
    account = _account_from_endpoint(endpoint)
    first, first_terminal = capture_inventory_pass(client, bucket, max_objects=max_objects)
    second, second_terminal = capture_inventory_pass(client, bucket, max_objects=max_objects)
    if first != second or first_terminal != second_terminal:
        raise RehearsalError("R2 changed between complete list/head passes")
    inventory_metadata_sha256 = _canonical_sha256(first)
    observed_list_requests = int(first_terminal["page_count"]) + int(second_terminal["page_count"])
    identity = _validated_capture_id(capture_id)
    return {
        "version": RAW_INVENTORY_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "capture_id": identity,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": {
            "account_sha256": _sha256_text(account),
            "bucket_sha256": _sha256_text(bucket),
            "endpoint_sha256": _sha256_text(endpoint),
        },
        "inventory_complete": True,
        "inventory_count": len(first),
        "inventory_metadata_sha256": inventory_metadata_sha256,
        "request_budget": {
            "max_objects": max_objects,
            # S3 is allowed to return a short truncated page.  Budget one
            # list request per allowed object per pass instead of assuming it
            # always fills MaxKeys.
            "max_list_requests": 2 * (max_objects + 1),
            "max_head_requests": 2 * max_objects,
            "observed_list_requests": observed_list_requests,
            "observed_head_requests": 2 * len(first),
        },
        "passes": [first_terminal, second_terminal],
        "objects": first,
    }


def _environment_client() -> tuple[Any, str, str]:
    """Build the one narrow S3 client after all non-network config checks."""
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    account = _account_from_endpoint(endpoint)
    bucket = os.environ.get("S3_BUCKET_NAME", "")
    access_key, secret_key = os.environ.get("S3_ACCESS_KEY", ""), os.environ.get("S3_SECRET_KEY", "")
    if not bucket or not access_key or not secret_key:
        raise RehearsalError("R2 read-only capture configuration is incomplete")
    # Do not print, return, persist, or place credentials in an argument list.
    import boto3
    from botocore.config import Config as BotoConfig
    client = boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        config=BotoConfig(signature_version="s3v4", connect_timeout=CONNECT_TIMEOUT_SECONDS,
                          read_timeout=READ_TIMEOUT_SECONDS,
                          retries={"max_attempts": MAX_RETRIES, "mode": "standard"}),
    )
    return client, bucket, endpoint


def _private_payload(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


@click.command()
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--max-objects", default=DEFAULT_MAX_OBJECTS, show_default=True,
              type=click.IntRange(min=1, max=1_000_000))
@click.option("--plan", "plan_only", is_flag=True, help="Print the bounded request budget without contacting R2.")
def cli(output: Path | None, max_objects: int, plan_only: bool) -> None:
    """Create private raw R2 evidence; stdout never contains object keys."""
    reservation = None
    try:
        if plan_only:
            click.echo(json.dumps({"status": "plan", "max_objects": max_objects,
                                   "max_list_requests": 2 * (max_objects + 1),
                                   "max_head_requests": 2 * max_objects}, sort_keys=True))
            return
        if output is None:
            raise RehearsalError("R2 raw inventory output is required")
        try:
            output.expanduser().resolve().relative_to(Path("/tmp"))
        except ValueError as error:
            raise RehearsalError("R2 raw inventory output must stay in an owned /tmp workspace") from error
        # Configuration refusal happens before a client exists or any request can occur.
        client, bucket, endpoint = _environment_client()
        raw = capture_two_pass_inventory(client, bucket=bucket, endpoint=endpoint, max_objects=max_objects)
        reservation = _ReservedReportOutput(output)
        digest = reservation.publish_bytes(_private_payload(raw))
        reservation.retain()
        click.echo(json.dumps({"status": "captured", "inventory_count": raw["inventory_count"],
                               "list_requests": raw["request_budget"]["observed_list_requests"],
                               "head_requests": raw["request_budget"]["observed_head_requests"],
                               "raw_inventory_sha256": digest}, sort_keys=True))
    except Exception as error:
        if reservation is not None:
            reservation.abort()
        # Provider exceptions may contain endpoint/request material.  Do not
        # reflect them to terminal output.
        raise click.ClickException("readonly_r2_inventory_capture_failed") from error


if __name__ == "__main__":
    cli()
