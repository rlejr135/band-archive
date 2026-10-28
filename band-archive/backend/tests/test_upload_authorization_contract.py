"""Upload authorization ordering contracts before service extraction.

Query doubles expose evaluation, refresh and lock calls, not real PostgreSQL
locking. Existing upload, leave, removal and restriction integration tests
remain responsible for actual persistence and concurrency coverage.
"""

from types import SimpleNamespace

import pytest

import content_restrictions
from errors import NotFoundError, ValidationError
import routes.uploads as authorization


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, '=', value

    def is_(self, value):
        return self.name, 'is', value


class Query:
    def __init__(self, name, row, events, filters):
        self.name, self.row = name, row
        self.events, self.filters = events, filters

    def filter(self, *expressions):
        self.events.append(self.name + '.filter')
        self.filters[self.name] = expressions
        return self

    def filter_by(self, **values):
        self.events.append(self.name + '.filter_by')
        self.filters[self.name] = values
        return self

    def with_for_update(self):
        self.events.append(self.name + '.lock')
        return self

    def populate_existing(self):
        self.events.append(self.name + '.refresh')
        return self

    def one_or_none(self):
        self.events.append(self.name + '.read')
        return self.row


def _models(monkeypatch, events, rows, *, dialect='postgresql'):
    filters = {}
    for name, row in rows.items():
        monkeypatch.setattr(authorization, name, SimpleNamespace(
            id=Column(name + '.id'), deleted_at=Column(name + '.deleted_at'),
            query=Query(name, row, events, filters)))
    monkeypatch.setattr(authorization, 'db', SimpleNamespace(
        engine=SimpleNamespace(dialect=SimpleNamespace(name=dialect))))
    return filters


def _actor(monkeypatch, events):
    actor = SimpleNamespace(id=12)

    def current(*, required):
        assert required is True
        events.append('actor')
        return actor

    monkeypatch.setattr(authorization, 'current_app_user', current)
    return actor


@pytest.mark.parametrize('dialect,lock', [
    ('postgresql', True), ('postgresql', False), ('sqlite', True), ('sqlite', False),
])
def test_active_upload_membership_keeps_actor_queries_and_lock_order(monkeypatch, dialect, lock):
    events = []
    band, membership = object(), SimpleNamespace(id=13, user_id=12)
    filters = _models(monkeypatch, events, {'Band': band, 'BandMembership': membership},
                      dialect=dialect)
    _actor(monkeypatch, events)

    assert authorization._v2_active_membership(7, lock=lock) == (band, membership)
    assert filters == {
        'Band': (('Band.id', '=', 7), ('Band.deleted_at', 'is', None)),
        'BandMembership': {'band_id': 7, 'user_id': 12, 'status': 'active'},
    }
    locks = ['Band.lock', 'BandMembership.lock'] if lock and dialect != 'sqlite' else []
    assert events == ['actor', 'Band.filter', 'BandMembership.filter_by'] + locks + [
        'Band.read', 'BandMembership.read']


@pytest.mark.parametrize('missing,exception,message,status', [
    ('band', NotFoundError, 'Band not found', 404),
    ('membership', ValidationError, 'An active band membership is required.', 403),
])
def test_active_upload_membership_preserves_missing_band_before_membership_error(
        monkeypatch, missing, exception, message, status):
    events = []
    _models(monkeypatch, events, {
        'Band': None if missing == 'band' else object(), 'BandMembership': None,
    })
    _actor(monkeypatch, events)
    with pytest.raises(exception) as caught:
        authorization._v2_active_membership(7, lock=True)
    assert caught.value.message == message and caught.value.status_code == status
    assert events == ['actor', 'Band.filter', 'BandMembership.filter_by',
                      'Band.lock', 'BandMembership.lock', 'Band.read'] + (
        ['BandMembership.read'] if missing == 'membership' else [])


@pytest.mark.parametrize('band_id,creator_id', [(8, 13), (7, 99)])
def test_upload_owner_identity_mismatch_stops_before_owner_query(monkeypatch, band_id, creator_id):
    events = []
    _models(monkeypatch, events, {'AppUser': None})
    session = SimpleNamespace(band_id=band_id, created_by_membership_id=creator_id)
    with pytest.raises(NotFoundError, match='Upload session not found'):
        authorization._v2_require_upload_session_owner(
            7, session, SimpleNamespace(id=13, user_id=12))
    assert events == []


@pytest.mark.parametrize('owner', [
    None, SimpleNamespace(status='inactive', email_verified=True),
    SimpleNamespace(status='active', email_verified=False),
    SimpleNamespace(status='active', email_verified=1),
])
def test_upload_owner_requires_refreshed_active_account_and_exact_verified_true(monkeypatch, owner):
    events = []
    filters = _models(monkeypatch, events, {'AppUser': owner})
    with pytest.raises(ValidationError) as caught:
        authorization._v2_require_upload_session_owner(
            7, SimpleNamespace(band_id=7, created_by_membership_id=13),
            SimpleNamespace(id=13, user_id=12))
    assert caught.value.message == 'An active, verified upload owner is required.'
    assert caught.value.status_code == 403
    assert filters == {'AppUser': {'id': 12}}
    assert events == ['AppUser.filter_by', 'AppUser.lock', 'AppUser.refresh', 'AppUser.read']


@pytest.mark.parametrize('target,expected', [
    ({'member_id': 23, 'song_id': 21, 'rehearsal_id': 22}, [('personal', 7, 23)]),
    ({'member_id': None, 'song_id': 21, 'rehearsal_id': 22},
     [('media', 7, {'song_id': 21, 'rehearsal_id': 22})]),
    ({}, []),
])
def test_upload_owner_preserves_personal_target_priority_and_gallery_no_parent_check(
        monkeypatch, target, expected):
    events, targets = [], []
    _models(monkeypatch, events, {'AppUser': SimpleNamespace(status='active', email_verified=True)})
    monkeypatch.setattr(authorization, '_v2_personal_log_target',
                        lambda *args: targets.append(('personal', *args)))
    monkeypatch.setattr(authorization, '_v2_media_target',
                        lambda *args: targets.append(('media', *args)))
    membership = SimpleNamespace(id=13, user_id=12)
    assert authorization._v2_require_upload_session_owner(
        7, SimpleNamespace(band_id=7, created_by_membership_id=13, **target), membership) is membership
    assert targets == expected
    assert events == ['AppUser.filter_by', 'AppUser.lock', 'AppUser.refresh', 'AppUser.read']


def test_upload_reauthorization_clears_restriction_cache_before_actor_and_parent_locks(monkeypatch):
    events = []
    membership = SimpleNamespace(id=13, user_id=12)
    _models(monkeypatch, events, {
        'Band': object(), 'BandMembership': membership,
        'AppUser': SimpleNamespace(status='active', email_verified=True),
    })
    _actor(monkeypatch, events)
    monkeypatch.setattr(content_restrictions, 'clear_restriction_cache', lambda: events.append('clear'))
    assert authorization._v2_reauthorize_upload_session_owner(
        7, SimpleNamespace(band_id=7, created_by_membership_id=13)) is membership
    assert events == ['clear', 'actor', 'Band.filter', 'BandMembership.filter_by',
                      'Band.lock', 'BandMembership.lock', 'Band.read', 'BandMembership.read',
                      'AppUser.filter_by', 'AppUser.lock', 'AppUser.refresh', 'AppUser.read']


@pytest.mark.parametrize('value,message', [
    (None, 'field must be an integer'), (True, 'field must be an integer'),
    (False, 'field must be an integer'), ('1', 'field must be an integer'),
    (1.0, 'field must be an integer'), ([], 'field must be an integer'),
    (0, 'field must be at least 1'), (11, 'field must be at most 10'),
])
def test_upload_integer_validation_preserves_types_bounds_and_error_text(value, message):
    with pytest.raises(ValidationError) as caught:
        authorization._require_int(value, 'field', minimum=1, maximum=10)
    assert caught.value.message == message and caught.value.status_code == 400


@pytest.mark.parametrize('value,minimum,maximum', [(1, 1, 10), (10, 1, 10), (0, 0, None), (-1, None, None)])
def test_upload_integer_validation_keeps_inclusive_and_unbounded_values(value, minimum, maximum):
    assert authorization._require_int(value, 'field', minimum, maximum) == value


def _restrictions(monkeypatch, events, blocked):
    def require(item):
        events.append('restriction:' + item.label)
        if item.label == blocked:
            raise NotFoundError('Resource not found')
    monkeypatch.setattr(content_restrictions, 'require_unrestricted', require)


@pytest.mark.parametrize('case', ['song_missing', 'song_blocked', 'foreign_link',
                                  'linked_blocked', 'rehearsal_missing', 'rehearsal_blocked'])
def test_media_target_preserves_graph_restriction_order_and_not_found_errors(monkeypatch, case):
    events = []
    linked = SimpleNamespace(label='linked', band_id=8 if case == 'foreign_link' else 7)
    song = SimpleNamespace(label='song', rehearsals=[linked])
    rehearsal = SimpleNamespace(label='rehearsal')
    _models(monkeypatch, events, {
        'Song': None if case == 'song_missing' else song,
        'Rehearsal': None if case == 'rehearsal_missing' else rehearsal,
    })
    _restrictions(monkeypatch, events, {
        'song_blocked': 'song', 'linked_blocked': 'linked', 'rehearsal_blocked': 'rehearsal',
    }.get(case))
    with pytest.raises(NotFoundError) as caught:
        authorization._v2_media_target(7, {'song_id': 21, 'rehearsal_id': 22})
    expected = ['Song.filter_by', 'Song.read']
    if case != 'song_missing':
        expected += ['restriction:song']
    if case in ('linked_blocked', 'rehearsal_missing', 'rehearsal_blocked'):
        expected += ['restriction:linked']
    if case in ('rehearsal_missing', 'rehearsal_blocked'):
        expected += ['Rehearsal.filter_by', 'Rehearsal.read']
    if case == 'rehearsal_blocked':
        expected += ['restriction:rehearsal']
    assert events == expected
    expected_message = ('Song not found' if case in ('song_missing', 'foreign_link') else
                        'Rehearsal not found' if case == 'rehearsal_missing' else 'Resource not found')
    assert caught.value.message == expected_message and caught.value.status_code == 404


@pytest.mark.parametrize('rehearsal_id', [None, 22])
def test_media_target_returns_existing_ids_with_band_scoped_queries(monkeypatch, rehearsal_id):
    events = []
    filters = _models(monkeypatch, events, {
        'Song': SimpleNamespace(label='song', rehearsals=[SimpleNamespace(label='linked', band_id=7)]),
        'Rehearsal': SimpleNamespace(label='rehearsal'),
    })
    _restrictions(monkeypatch, events, None)
    assert authorization._v2_media_target(7, {'song_id': 21, 'rehearsal_id': rehearsal_id}) == (21, rehearsal_id)
    assert filters['Song'] == {'id': 21, 'band_id': 7}
    expected = ['Song.filter_by', 'Song.read', 'restriction:song', 'restriction:linked']
    if rehearsal_id is not None:
        assert filters['Rehearsal'] == {'id': 22, 'band_id': 7}
        expected += ['Rehearsal.filter_by', 'Rehearsal.read', 'restriction:rehearsal']
    assert events == expected


@pytest.mark.parametrize('case', ['missing', 'blocked', 'valid'])
def test_personal_log_target_keeps_band_scope_and_restriction_guard(monkeypatch, case):
    events = []
    filters = _models(monkeypatch, events, {
        'Member': None if case == 'missing' else SimpleNamespace(label='member'),
    })
    _restrictions(monkeypatch, events, 'member' if case == 'blocked' else None)
    if case == 'valid':
        assert authorization._v2_personal_log_target(7, 23) == 23
    else:
        with pytest.raises(NotFoundError) as caught:
            authorization._v2_personal_log_target(7, 23)
        assert caught.value.message == ('Member not found' if case == 'missing' else 'Resource not found')
        assert caught.value.status_code == 404
    assert filters == {'Member': {'id': 23, 'band_id': 7}}
    assert events == ['Member.filter_by', 'Member.read'] + (
        [] if case == 'missing' else ['restriction:member'])
