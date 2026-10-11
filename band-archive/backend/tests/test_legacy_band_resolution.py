"""The validated v1 import tenant survives PostgreSQL and owner claiming."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.exc import MultipleResultsFound

from extensions import db
from models import AppUser, Band, Member, TenantScopeUnavailable, validated_legacy_band_id


def test_runtime_import_predicate_compiles_as_postgres_boolean_not_integer():
    statements=[]
    def execute(statement):
        statements.append(statement)
        return SimpleNamespace(scalar_one_or_none=lambda:'validated' if len(statements)==1 else 7)
    assert validated_legacy_band_id(SimpleNamespace(execute=execute))==7
    sql=str(statements[1].compile(dialect=dialect(),compile_kwargs={'literal_binds':True}))
    assert 'band.is_legacy_import IS true' in sql
    assert 'is_legacy_import = 1' not in sql and 'created_by_user_id' not in sql


@pytest.mark.parametrize('claimed',[False,True])
def test_sqlite_v1_members_resolves_only_import_tenant_before_and_after_claim(app,client,claimed):
    with app.app_context():
        band=Band.query.filter(Band.is_legacy_import.is_(True)).one()
        foreign=Band(name='foreign',slug='foreign-lookup',default_visibility='PUBLIC',storage_quota_bytes=1,is_legacy_import=False)
        db.session.add(foreign)
        if claimed:
            user=AppUser(supabase_user_id='lookup-owner',email='owner@example.test',email_verified=True)
            db.session.add(user); db.session.flush(); band.created_by_user_id=user.id
        db.session.add(Member(band_id=band.id,name='legacy',instrument='bass'))
        db.session.flush()
        db.session.execute(text('INSERT INTO member (band_id,name,instrument) VALUES (:band,:name,:instrument)'),{'band':foreign.id,'name':'foreign','instrument':'drums'})
        db.session.commit()
        assert validated_legacy_band_id(db.session.connection())==band.id
    response=client.get('/members')
    assert response.status_code==200
    assert [item['name'] for item in response.get_json()]==['legacy']


def test_sqlite_import_resolver_marker_and_uniqueness_stay_fail_closed(app,client,monkeypatch):
    # Production marker failures raise; isolated TESTING fixtures deliberately
    # retain their preexisting missing-marker fallback, which is not this gate.
    monkeypatch.setitem(app.config,'TESTING',False)
    with app.app_context():
        db.session.execute(text("UPDATE tenant_backfill_state SET status='failed'")); db.session.commit()
        with pytest.raises(TenantScopeUnavailable): validated_legacy_band_id(db.session.connection())
    assert client.get('/members').status_code==503
    with app.app_context():
        db.session.execute(text("UPDATE tenant_backfill_state SET status='validated'"))
        db.session.add(Band(name='ambiguous',slug='ambiguous-lookup',default_visibility='PUBLIC',storage_quota_bytes=1,is_legacy_import=True))
        db.session.commit()
        with pytest.raises(MultipleResultsFound): validated_legacy_band_id(db.session.connection())
    assert client.get('/members').status_code==503


def test_sqlite_nonlegacy_only_is_not_a_v1_tenant(app,client):
    with app.app_context():
        db.session.execute(text('UPDATE band SET is_legacy_import=FALSE')); db.session.commit()
        with pytest.raises(TenantScopeUnavailable): validated_legacy_band_id(db.session.connection())
    assert client.get('/members').status_code==503


# Reuse the explicitly provisioned, owned localhost fixture. No caller target
# URL or remote PostgreSQL is read, and every fixture mutation stays local.
_spec=importlib.util.spec_from_file_location('owned_pg_legacy_resolver_fixture',Path(__file__).with_name('test_preserve_identity_refresh_postgres.py'))
_fixture_module=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_fixture_module)
private_pg17=_fixture_module.private_pg17


@pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION')!='1',reason='explicitly opt into owned localhost PG17')
def test_owned_postgres_v1_members_unclaimed_claimed_and_fail_closed(private_pg17,monkeypatch):
    import app as factory
    from config import TestingConfig
    _fixture_module._upgrade_postgres(private_pg17)
    monkeypatch.setattr(factory.storage,'init_app',lambda app:None)
    class LocalConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI=private_pg17
        PG_AUDIO_WORKER_ENABLED=False
        PG_MULTIPART_RECOVERY_ENABLED=False
    application=factory.create_app(LocalConfig,start_worker=False)
    engine=create_engine(private_pg17)
    try:
        with engine.begin() as connection:
            band=validated_legacy_band_id(connection)
            connection.execute(text("INSERT INTO member (band_id,name,instrument) VALUES (:band,'legacy','bass')"),{'band':band})
            foreign=connection.execute(text("INSERT INTO band (name,slug,default_visibility,storage_quota_bytes,is_legacy_import,created_at,updated_at) VALUES ('foreign','foreign-pg-lookup','PUBLIC',1,FALSE,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) RETURNING id")).scalar_one()
            connection.execute(text("INSERT INTO member (band_id,name,instrument) VALUES (:band,'foreign','drums')"),{'band':foreign})
        client=application.test_client()
        assert client.get('/members').status_code==200
        assert [item['name'] for item in client.get('/members').get_json()]==['legacy']
        with engine.begin() as connection:
            user=connection.execute(text("INSERT INTO app_user (supabase_user_id,email,status,email_verified,is_platform_admin,created_at,updated_at) VALUES ('pg-lookup-owner','owner@example.test','active',TRUE,FALSE,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) RETURNING id")).scalar_one()
            connection.execute(text('UPDATE band SET created_by_user_id=:user WHERE id=:band'),{'user':user,'band':band})
            assert validated_legacy_band_id(connection)==band
        assert client.get('/members').status_code==200
        assert [item['name'] for item in client.get('/members').get_json()]==['legacy']
        with engine.begin() as connection:
            connection.execute(text('UPDATE band SET is_legacy_import=FALSE WHERE id=:band'),{'band':band})
            with pytest.raises(TenantScopeUnavailable): validated_legacy_band_id(connection)
        assert client.get('/members').status_code==503
        with engine.begin() as connection:
            connection.execute(text('UPDATE band SET is_legacy_import=TRUE'))
            with pytest.raises(MultipleResultsFound): validated_legacy_band_id(connection)
        assert client.get('/members').status_code==503
        with engine.begin() as connection:
            connection.execute(text('UPDATE band SET is_legacy_import=FALSE WHERE id=:foreign'),{'foreign':foreign})
            connection.execute(text("UPDATE tenant_backfill_state SET status='failed'"))
            with pytest.raises(TenantScopeUnavailable): validated_legacy_band_id(connection)
        assert client.get('/members').status_code==503
    finally:
        with application.app_context(): db.session.remove(); db.engine.dispose()
        engine.dispose()
