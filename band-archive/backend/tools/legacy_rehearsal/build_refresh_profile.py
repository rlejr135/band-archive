"""Assemble a refresh sidecar only from independently retained proofs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import click

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.legacy_rehearsal.preserve_identity_refresh import PROFILE_VERSION, load_reviewed_profile
from tools.legacy_rehearsal.rehearsal import RehearsalError, _ReservedReportOutput


def _load(path: Path, expected_sha256: str) -> tuple[dict, str]:
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
        raise RehearsalError("expected proof SHA-256 is invalid")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RehearsalError("proof is not readable JSON") from error
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise RehearsalError("proof SHA-256 does not match the approved expected value")
    return value, actual


def _relative(base: Path, child: Path) -> str:
    try:
        return child.resolve().relative_to(base.resolve()).as_posix()
    except ValueError as error:
        raise RehearsalError("proof files must be retained beneath the profile output directory") from error


@click.command()
@click.option("--baseline-proof", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-baseline-proof-sha256", required=True)
@click.option("--postclaim-proof", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expected-postclaim-proof-sha256", required=True)
@click.option("--report-output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def cli(baseline_proof: Path, expected_baseline_proof_sha256: str, postclaim_proof: Path,
        expected_postclaim_proof_sha256: str, report_output: Path) -> None:
    """Publish a no-overwrite profile whose proof hashes are externally pinned."""
    reservation = None
    try:
        baseline, baseline_sha = _load(baseline_proof, expected_baseline_proof_sha256)
        postclaim, postclaim_sha = _load(postclaim_proof, expected_postclaim_proof_sha256)
        if (baseline.get("version") != "phase9-offline-v11-refresh-baseline-v1"
                or baseline.get("status") != "offline_v11_baseline_verified"
                or postclaim.get("status") != "postclaim_verified"):
            raise RehearsalError("baseline or post-claim proof is not verified")
        embedded = postclaim.get("preserve_identity_refresh_profile")
        if (not isinstance(embedded, dict) or embedded.get("legacy_domain") != baseline.get("legacy_domain")
                or not isinstance(embedded.get("protected"), dict)):
            raise RehearsalError("post-claim proof does not match the independently derived baseline domain")
        output_parent = report_output.expanduser().resolve().parent
        profile = {
            "version": PROFILE_VERSION, "target_writes_frozen": True,
            "provenance": {
                "baseline_proof": {"file": _relative(output_parent, baseline_proof), "sha256": baseline_sha},
                "post_claim": {"file": _relative(output_parent, postclaim_proof), "sha256": postclaim_sha},
            },
            "protected": embedded["protected"], "legacy_domain": baseline["legacy_domain"],
        }
        reservation = _ReservedReportOutput(report_output)
        reservation.publish(profile)
        # Parse exactly the bytes/relative paths that later refresh will use.
        load_reviewed_profile(report_output)
        click.echo(f"report_written sha256={reservation.retain()}")
    except Exception as error:
        if reservation is not None:
            reservation.abort()
        raise click.ClickException("refresh_profile_build_failed") from error


if __name__ == "__main__":
    cli()
