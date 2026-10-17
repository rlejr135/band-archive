"""Atomic, private rehearsal coordination; no external calendar or media work."""
import re
import unicodedata
from datetime import date, datetime, timezone

from sqlalchemy import or_

from band_service import _begin_privileged_mutation, audit, require_band_role
from errors import ValidationError
from extensions import db
from models import AppUser, BandAvailabilityDay as Day, BandAvailabilityState as State, BandMembership, BandPersonalScheduleNote as Note

PERIODS = ('morning', 'afternoon', 'evening')
STATUSES = {'AVAILABLE', 'UNAVAILABLE', 'UNKNOWN'}
ROLES = {'owner', 'admin', 'member'}
MAX_DAYS = 42


def strict_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValidationError('Invalid availability request fields.')


def integer(value, key):
    if type(value) is not int or not 0 <= value <= 2147483646:
        raise ValidationError(f'{key} must be a non-negative integer.')
    return value


def local_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValidationError('Use a YYYY-MM-DD date in Asia/Seoul.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError('Invalid calendar date.')


def period(value):
    if value not in PERIODS:
        raise ValidationError('Invalid calendar period.')
    return value


def range_dates(start, end):
    start, end = local_date(start), local_date(end)
    if not 0 <= (end - start).days < MAX_DAYS:
        raise ValidationError('Calendar range must be between 1 and 42 days.')
    return start, end


def conflict(generation=False):
    raise ValidationError('Calendar changed. Reload before saving.', 409,
                          'availability_generation_conflict' if generation else 'availability_conflict')


def _authorize(user, band_id, write=False):
    if user.status != 'active' or not user.email_verified:
        raise ValidationError('An active verified account is required.', 403)
    if write:
        _begin_privileged_mutation()
        return require_band_role(user, band_id, roles=ROLES, lock=True)
    # Holding the band's shared row lock throughout serialization prevents a
    # concurrent reset/save/leave from mixing generations with old rows.
    from models import Band
    band = Band.query.filter_by(id=band_id, deleted_at=None).with_for_update(read=True).populate_existing().one_or_none()
    from errors import NotFoundError
    if band is None:
        raise NotFoundError('Band not found')
    membership = BandMembership.query.filter_by(band_id=band_id, user_id=user.id, status='active').populate_existing().one_or_none()
    if membership is None:
        raise NotFoundError('Band not found')
    return band, membership


def _locked_state(band_id, expected):
    integer(expected, 'generation')
    state = State.query.filter_by(band_id=band_id).with_for_update().populate_existing().one_or_none()
    if expected != (state.generation if state else 0):
        conflict(True)
    if state is None:
        state = State(band_id=band_id, generation=0)
        db.session.add(state)
        db.session.flush()
    return state


def _finish(state, changed):
    generation = state.generation
    db.session.commit()
    return {'changed': changed, 'generation': generation}


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def snapshot(user, band_id, start, end):
    start, end = range_dates(start, end)
    band, membership = _authorize(user, band_id)
    state = db.session.get(State, band.id)
    members = db.session.query(BandMembership.id, AppUser.display_name).join(
        AppUser, AppUser.id == BandMembership.user_id).filter(
        BandMembership.band_id == band.id, BandMembership.status == 'active',
        AppUser.status == 'active', AppUser.email_verified.is_(True),
    ).order_by(BandMembership.id).all()
    member_ids = [item.id for item in members]
    days = Day.query.filter(Day.band_id == band.id, Day.membership_id.in_(member_ids),
                            Day.date.between(start, end)).order_by(Day.date, Day.membership_id).all()
    # Filter privacy in SQL; private text never enters another member's DTO.
    notes = Note.query.filter(
        Note.band_id == band.id, Note.membership_id.in_(member_ids), Note.date.between(start, end),
        or_(Note.membership_id == membership.id, (Note.visibility == 'BAND') & Note.body.is_not(None)),
    ).order_by(Note.date, Note.period, Note.membership_id).all()
    result = {
        'band_id': band.id, 'generation': state.generation if state else 0,
        'last_reset_at': _iso(state.last_reset_at) if state else None,
        'can_reset': membership.role == 'owner', 'my_membership_id': membership.id,
        'members': [{'membership_id': item.id, 'display_name': item.display_name or f'멤버 #{item.id}'} for item in members],
        'days': [{'membership_id': item.membership_id, 'date': item.date.isoformat(),
                  **{p: getattr(item, p) for p in PERIODS}, 'revision': item.revision} for item in days],
        'notes': [{'id': item.id, 'membership_id': item.membership_id, 'date': item.date.isoformat(),
                   'period': item.period, 'body': item.body, 'visibility': item.visibility,
                   'revision': item.revision} for item in notes],
    }
    if membership.role == 'owner':
        result['reset_summary'] = {
            'days': Day.query.filter_by(band_id=band.id).count(),
            'notes': Note.query.filter(Note.band_id == band.id, Note.body.is_not(None)).count(),
        }
    # Release snapshot lock after all values have been serialized.
    db.session.rollback()
    return result


def update_days(user, band_id, payload):
    strict_keys(payload, {'generation', 'days'})
    integer(payload['generation'], 'generation')
    rows = payload['days']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_DAYS:
        raise ValidationError('Supply between 1 and 42 days.')
    parsed = []
    for item in rows:
        strict_keys(item, {'date', 'revision', *PERIODS})
        target_date = local_date(item['date'])
        revision = integer(item['revision'], 'revision')
        if any(not isinstance(item[p], str) or item[p] not in STATUSES for p in PERIODS):
            raise ValidationError('Invalid availability status.')
        parsed.append((target_date, revision, {p: item[p] for p in PERIODS}))
    dates = [row[0] for row in parsed]
    if len(set(dates)) != len(dates) or (max(dates) - min(dates)).days >= MAX_DAYS:
        raise ValidationError('Days must be unique and fit a 42-day range.')
    band, membership = _authorize(user, band_id, True)
    state = _locked_state(band.id, payload['generation'])
    current = {item.date: item for item in Day.query.filter(
        Day.band_id == band.id, Day.membership_id == membership.id, Day.date.in_(dates),
    ).order_by(Day.date).with_for_update().populate_existing().all()}
    changed = False
    for target_date, expected, values in sorted(parsed):
        item = current.get(target_date)
        revision = item.revision if item else 0
        same = item is not None and all(getattr(item, p) == values[p] for p in PERIODS)
        if expected != revision:
            if expected == revision - 1 and same:
                continue  # A lost-response retry, never an older overwrite.
            conflict()
        if same:
            continue
        if item is None:
            item = Day(band_id=band.id, membership_id=membership.id, date=target_date, revision=1)
            db.session.add(item)
        else:
            item.revision += 1
        for key, value in values.items():
            setattr(item, key, value)
        changed = True
    return _finish(state, changed)


def reset_day(user, band_id, target_date, payload):
    strict_keys(payload, {'generation', 'revision'})
    local_date(target_date)
    return update_days(user, band_id, {'generation': payload['generation'], 'days': [{
        'date': target_date, 'revision': payload['revision'], **{p: 'UNKNOWN' for p in PERIODS},
    }]})


def update_note(user, band_id, target_date, target_period, payload, delete=False):
    strict_keys(payload, {'generation', 'revision'} if delete else {'generation', 'revision', 'body', 'visibility'})
    target_date, target_period = local_date(target_date), period(target_period)
    expected = integer(payload['revision'], 'revision')
    integer(payload['generation'], 'generation')
    body, visibility = None, 'PRIVATE'
    if not delete:
        body, visibility = payload['body'], payload['visibility']
        if (not isinstance(body, str) or not body.strip() or len(body.strip()) > 200
                or any(unicodedata.category(c) in {'Cc', 'Cs'} and c not in '\n\t' for c in body)):
            raise ValidationError('Note must contain 1-200 plain-text characters.')
        if not isinstance(visibility, str) or visibility not in {'PRIVATE', 'BAND'}:
            raise ValidationError('Invalid note visibility.')
        body = body.strip()
    band, membership = _authorize(user, band_id, True)
    state = _locked_state(band.id, payload['generation'])
    item = Note.query.filter_by(band_id=band.id, membership_id=membership.id, date=target_date,
                                period=target_period).with_for_update().populate_existing().one_or_none()
    revision = item.revision if item else 0
    same = (item.body == body and item.visibility == visibility) if item else False
    if expected != revision:
        if expected == revision - 1 and same:
            return _finish(state, False)
        conflict()
    if same:
        return _finish(state, False)
    if item is None:
        item = Note(band_id=band.id, membership_id=membership.id, date=target_date,
                    period=target_period, revision=1)
        db.session.add(item)
    else:
        item.revision += 1
    item.body, item.visibility = body, visibility
    return _finish(state, True)


def reset_band(user, band_id, payload):
    strict_keys(payload, {'generation', 'confirm'})
    integer(payload['generation'], 'generation')
    if payload['confirm'] is not True:
        raise ValidationError('Explicit reset confirmation is required.')
    band, membership = _authorize(user, band_id, True)
    if membership.role != 'owner':
        raise ValidationError('Only the band owner may reset coordination.', 403)
    state = _locked_state(band.id, payload['generation'])
    day_count = Day.query.filter_by(band_id=band.id).delete(synchronize_session=False)
    note_count = Note.query.filter_by(band_id=band.id).delete(synchronize_session=False)
    state.generation += 1
    state.last_reset_at = datetime.now(timezone.utc)
    state.last_reset_by_membership_id = membership.id
    audit('band.availability.reset', actor=user, band=band,
          details={'generation': state.generation, 'days': day_count, 'notes': note_count})
    return _finish(state, True)
