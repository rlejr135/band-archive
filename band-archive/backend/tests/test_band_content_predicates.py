from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Integer, MetaData, Table, insert, select

from band_content_predicates import _creator_is_scoped_sql, _not_pending_delete
from extensions import db
from models import AppUser, Band, BandMembership, R2DeletionTask


def test_creator_scope_allows_null_and_same_band_only(app):
    from band_access import BandReadScope, RequestAudience

    with app.app_context():
        band_a = Band(name='Band A', slug='predicate-band-a')
        band_b = Band(name='Band B', slug='predicate-band-b')
        user_a = AppUser(supabase_user_id='predicate-user-a', email='predicate-a@example.com')
        user_b = AppUser(supabase_user_id='predicate-user-b', email='predicate-b@example.com')
        db.session.add_all([band_a, band_b, user_a, user_b])
        db.session.flush()
        membership_a = BandMembership(band_id=band_a.id, user_id=user_a.id, role='member', status='active')
        membership_b = BandMembership(band_id=band_b.id, user_id=user_b.id, role='member', status='active')
        db.session.add_all([membership_a, membership_b])
        db.session.flush()

        metadata = MetaData()
        targets = Table(
            'predicate_creator_targets', metadata,
            Column('id', Integer, primary_key=True),
            Column('band_id', Integer, nullable=False),
            Column('created_by_membership_id', Integer, nullable=True),
        )
        connection = db.session.connection()
        targets.create(connection)
        connection.execute(insert(targets), [
            {'id': 1, 'band_id': band_a.id, 'created_by_membership_id': None},
            {'id': 2, 'band_id': band_a.id, 'created_by_membership_id': membership_a.id},
            {'id': 3, 'band_id': band_a.id, 'created_by_membership_id': membership_b.id},
            {'id': 4, 'band_id': band_a.id, 'created_by_membership_id': 999999},
        ])

        scope = BandReadScope(
            band_id=band_a.id, band_slug=band_a.slug,
            audience=RequestAudience(user_id=None, subject=None), membership=None,
        )
        predicate = _creator_is_scoped_sql(targets.c, scope)
        allowed_by_target_id = {
            target_id: db.session.execute(
                select(predicate).select_from(targets).where(targets.c.id == target_id)
            ).scalar_one()
            for target_id in (1, 2, 3, 4)
        }

        assert allowed_by_target_id == {1: True, 2: True, 3: False, 4: False}
        targets.drop(connection)


def test_pending_delete_predicate_is_scoped_by_band_type_and_unfinished_status(app):
    with app.app_context():
        band_a = Band(name='Band A', slug='delete-band-a')
        band_b = Band(name='Band B', slug='delete-band-b')
        db.session.add_all([band_a, band_b])
        db.session.flush()

        metadata = MetaData()
        targets = Table(
            'predicate_delete_targets', metadata,
            Column('id', Integer, primary_key=True),
            Column('band_id', Integer, nullable=False),
        )
        connection = db.session.connection()
        targets.create(connection)
        connection.execute(insert(targets), [
            {'id': 201, 'band_id': band_a.id},
            {'id': 202, 'band_id': band_a.id},
            {'id': 203, 'band_id': band_a.id},
            {'id': 204, 'band_id': band_a.id},
            {'id': 205, 'band_id': band_a.id},
        ])

        statuses = ('queued', 'processing', 'retry_wait', 'blocked')
        for resource_id, status in zip((201, 202, 203, 204), statuses):
            db.session.add(R2DeletionTask(
                band_id=band_a.id, resource_type='gallery', resource_id=resource_id,
                status=status, object_keys=['gallery/object'],
                lease_token='predicate-lease' if status == 'processing' else None,
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=1))
                if status == 'processing' else None,
            ))
        db.session.add(R2DeletionTask(
            band_id=band_a.id, resource_type='gallery', resource_id=205,
            status='completed', object_keys=None,
        ))
        db.session.add_all([
            R2DeletionTask(
                band_id=band_a.id, resource_type='media', resource_id=206,
                status='queued', object_keys=['media/object'],
            ),
            R2DeletionTask(
                band_id=band_b.id, resource_type='gallery', resource_id=207,
                status='queued', object_keys=['other-band/object'],
            ),
        ])
        db.session.flush()
        connection.execute(insert(targets), [
            {'id': 206, 'band_id': band_a.id},
            {'id': 207, 'band_id': band_a.id},
        ])

        predicate = _not_pending_delete(targets.c, 'gallery')
        allowed_by_target_id = {
            target_id: db.session.execute(
                select(predicate).select_from(targets).where(targets.c.id == target_id)
            ).scalar_one()
            for target_id in range(201, 208)
        }

        assert allowed_by_target_id == {
            201: False, 202: False, 203: False, 204: False,
            205: True, 206: True, 207: True,
        }
        targets.drop(connection)


def test_predicate_helpers_do_not_query_or_commit(app, monkeypatch):
    from band_access import BandReadScope, RequestAudience
    from models import GalleryImage

    with app.app_context():
        scope = BandReadScope(
            band_id=71, band_slug='predicates',
            audience=RequestAudience(user_id=None, subject=None), membership=None,
        )

        def unexpected(*args, **kwargs):
            raise AssertionError('predicate helpers must not execute database operations')

        monkeypatch.setattr(db.session, 'query', unexpected)
        monkeypatch.setattr(db.session, 'execute', unexpected)
        monkeypatch.setattr(db.session, 'commit', unexpected)

        creator_predicate = _creator_is_scoped_sql(GalleryImage, scope)
        deletion_predicate = _not_pending_delete(GalleryImage, 'gallery')

        assert creator_predicate is not None
        assert deletion_predicate is not None
