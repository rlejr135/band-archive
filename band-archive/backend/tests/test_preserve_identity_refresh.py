"""Unit contracts for the PostgreSQL-only identity-preserving refresh path."""

import json

import pytest
from flask_migrate import upgrade
from sqlalchemy import create_engine, text

from extensions import db
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from tenant_backfill import apply as apply_tenant_backfill
from tools.legacy_rehearsal.preserve_identity_refresh import (
    PROFILE_VERSION, PROTECTED_TABLES, REFRESHED_TABLES, RehearsalError,
    _assert_profile, _table_digests, load_reviewed_profile, run_preserve_identity_refresh,
)


def _url(path):
    return f"sqlite:///{path.as_posix()}"


def _upgrade(path):
    app = create_migration_app(_url(path))
    try:
        with app.app_context():
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                apply_tenant_backfill(connection)
            upgrade(revision='head', directory=str(MIGRATIONS_DIRECTORY))
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _claimed_target(path):
    _upgrade(path)
    engine = create_engine(_url(path))
    try:
        with engine.begin() as connection:
            user_id = connection.execute(text(
                "INSERT INTO app_user (supabase_user_id, email, status, email_verified, is_platform_admin, "
                "created_at, updated_at) VALUES ('subject-1', 'owner@example.test', 'active', 1, 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id"
            )).scalar_one()
            connection.execute(text(
                "INSERT INTO auth_bootstrap_state (key, claimed_by_user_id, claimed_at) "
                "VALUES ('initial_platform_admin', :user_id, CURRENT_TIMESTAMP)"
            ), {'user_id': user_id})
            band_id = connection.execute(text("SELECT id FROM band WHERE is_legacy_import=1")).scalar_one()
            connection.execute(text("UPDATE band SET created_by_user_id=:user_id WHERE id=:band_id"), {
                'user_id': user_id, 'band_id': band_id,
            })
            connection.execute(text(
                "INSERT INTO band_membership (band_id,user_id,role,status,requested_at,approved_at) "
                "VALUES (:band_id,:user_id,'owner','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ), {'band_id': band_id, 'user_id': user_id})
            connection.execute(text(
                "INSERT INTO legacy_band_claim_token (band_id,purpose,token_hash,issued_by_user_id,created_at,expires_at,"
                "redeemed_at,redeemed_by_user_id) VALUES (:band_id,'initial_owner',:token_hash,:user_id,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,:user_id)"
            ), {'band_id': band_id, 'user_id': user_id, 'token_hash': 'b' * 64})
            for event_type in ('legacy_claim_issued', 'legacy_claim_redeemed', 'legacy_owner_assigned'):
                connection.execute(text(
                    "INSERT INTO audit_event (band_id,actor_user_id,event_type,created_at) "
                    "VALUES (:band_id,:user_id,:event_type,CURRENT_TIMESTAMP)"
                ), {'band_id': band_id, 'user_id': user_id, 'event_type': event_type})
        return engine
    except Exception:
        engine.dispose()
        raise


def _profile(connection):
    band_id = connection.execute(text("SELECT id FROM band WHERE is_legacy_import=1")).scalar_one()
    return {
        'version': PROFILE_VERSION,
        'target_writes_frozen': True,
        'provenance': {
            'reference_snapshot_sha256': '0' * 64,
            'source_import_report_sha256': '1' * 64,
            'post_claim_review_sha256': '2' * 64,
        },
        'protected': {'table_digests': _table_digests(connection, PROTECTED_TABLES)},
        'legacy_domain': {'table_digests': _table_digests(connection, REFRESHED_TABLES, band_id=band_id)},
    }


def test_profile_proves_claimed_identity_and_rejects_one_extra_audit(tmp_path):
    engine = _claimed_target(tmp_path / 'target.sqlite')
    try:
        with engine.connect() as connection:
            profile = _profile(connection)
            assert _assert_profile(connection, profile) > 0
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO audit_event (event_type,created_at) VALUES ('unexpected',CURRENT_TIMESTAMP)"))
        with engine.connect() as connection, pytest.raises(RehearsalError, match='reviewed three-event'):
            _assert_profile(connection, profile)
    finally:
        engine.dispose()


def test_profile_rejects_selected_logo_before_refresh_can_cascade_delete_it(tmp_path):
    engine = _claimed_target(tmp_path / 'target-with-logo.sqlite')
    try:
        with engine.connect() as connection:
            profile = _profile(connection)
            band_id = connection.execute(text(
                "SELECT id FROM band WHERE is_legacy_import=1"
            )).scalar_one()
        with engine.begin() as connection:
            image_id = connection.execute(text(
                "INSERT INTO gallery_image (band_id,visibility,filename,is_featured,created_at) "
                "VALUES (:band,'PUBLIC','claimed-logo.jpg',0,CURRENT_TIMESTAMP) RETURNING id"
            ), {'band': band_id}).scalar_one()
            connection.execute(text(
                "INSERT INTO band_logo (band_id,gallery_image_id) VALUES (:band,:image)"
            ), {'band': band_id, 'image': image_id})
        with engine.connect() as connection, pytest.raises(RehearsalError, match='unsupported new content'):
            _assert_profile(connection, profile)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM band_logo")).scalar_one() == 1
    finally:
        engine.dispose()


def test_reviewed_profile_rejects_missing_provenance(tmp_path):
    profile = {'version': PROFILE_VERSION, 'target_writes_frozen': True}
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile), encoding='utf-8')
    with pytest.raises(RehearsalError, match='invalid shape'):
        load_reviewed_profile(path)


def test_refresh_rejects_sqlite_before_source_or_target_mutation(tmp_path):
    source, target = tmp_path / 'source.sqlite', tmp_path / 'target.sqlite'
    source.touch()
    with pytest.raises(RehearsalError, match='unsafe connection identity override'):
        run_preserve_identity_refresh(
            str(source), _url(target), expected_target_host='example.test', expected_target_database='db',
            expected_target_schema='public', source_write_freeze_attestation={}, reviewed_profile={}, object_proof={}, dry_run=True,
        )
