"""Explicitly scoped, dry-run-first member-profile repair.

This tool never matches names, merges profiles, deletes records, or touches R2.
Supply an explicitly selected database through MEMBER_PROFILE_DATABASE_URL;
production execution still requires separate operator authorization and backup.
The callable accepts a caller-owned transaction for reviewed migration runbooks.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import sqlalchemy as sa


class ProfileRepairError(ValueError):
    pass


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ProfileRepairError('Identifiers must be positive integers')
    return value


def _tables(connection):
    metadata = sa.MetaData()
    return {name: sa.Table(name, metadata, autoload_with=connection)
            for name in ('band', 'band_membership', 'app_user', 'member')}


def _mapping_rows(mappings):
    if not isinstance(mappings, list):
        raise ProfileRepairError('Mappings must be a JSON array')
    seen_members, seen_users, result = set(), set(), []
    for row in mappings:
        if not isinstance(row, dict) or set(row) != {'member_id', 'user_id'}:
            raise ProfileRepairError('Each mapping requires only member_id and local user_id')
        member_id, user_id = _positive(row['member_id']), _positive(row['user_id'])
        if member_id in seen_members or user_id in seen_users:
            raise ProfileRepairError('Duplicate mapping identifier')
        seen_members.add(member_id)
        seen_users.add(user_id)
        result.append((member_id, user_id))
    return result


def repair_profiles(connection, *, band_id, mappings=None, apply=False):
    """Inspect or repair one band. Never commit the caller's transaction.

    ``user_id`` is the internal AppUser primary key, not a display name/email.
    Reports deliberately contain only operational numeric identifiers/counts.
    In apply mode the band row is locked before reading any mutable data.
    """
    _positive(band_id)
    if type(apply) is not bool:
        raise ProfileRepairError('apply must be boolean')
    links = _mapping_rows([] if mappings is None else mappings)
    tables = _tables(connection)
    band, membership, user, member = (tables[name] for name in
                                     ('band', 'band_membership', 'app_user', 'member'))
    query = sa.select(band.c.id).where(band.c.id == band_id, band.c.deleted_at.is_(None))
    if apply:
        query = query.with_for_update()
    if connection.execute(query).scalar_one_or_none() is None:
        raise ProfileRepairError('Active band not found')
    rows = [dict(row) for row in connection.execute(
        sa.select(member).where(member.c.band_id == band_id)).mappings()]
    accounts = [dict(row) for row in connection.execute(
        sa.select(membership.c.user_id, membership.c.id.label('membership_id'), user.c.display_name,
                  user.c.status.label('account_status'), user.c.email_verified)
        .join(user, user.c.id == membership.c.user_id)
        .where(membership.c.band_id == band_id, membership.c.status == 'active')).mappings()]
    active = {row['user_id']: row for row in accounts
              if row['account_status'] == 'active' and row['email_verified'] is True}
    blocked = sorted(row['user_id'] for row in accounts if row['user_id'] not in active)
    by_id = {row['id']: row for row in rows}
    linked = {}
    for row in rows:
        user_id = row['linked_user_id']
        if user_id is not None:
            if user_id in linked:
                raise ProfileRepairError('Duplicate linked profiles; repair explicitly before proceeding')
            linked[user_id] = row['id']
    for member_id, user_id in links:
        row = by_id.get(member_id)
        if row is None or user_id not in active:
            raise ProfileRepairError('Mapping must target this band and an active verified membership')
        if row['linked_user_id'] == user_id:
            continue  # Re-running an already completed exact mapping is harmless.
        if row['linked_user_id'] is not None or user_id in linked:
            raise ProfileRepairError('Mapping would reassign or merge an existing linked profile')
        linked[user_id] = member_id
    missing = sorted(set(active) - set(linked))
    report = {
        'band_id': band_id, 'mode': 'apply' if apply else 'dry_run',
        'active_verified_memberships': len(active),
        'blocked_active_user_ids': blocked,
        'existing_profiles': len(rows),
        'unlinked_legacy_member_ids': sorted(row['id'] for row in rows
                                            if row['linked_user_id'] is None),
        'explicit_mappings': [{'member_id': mid, 'user_id': uid} for mid, uid in links],
        'missing_user_ids': missing, 'created_member_ids': [],
    }
    if not apply:
        return report
    if blocked:
        raise ProfileRepairError('Active memberships with inactive or unverified accounts require operator review')
    required = {'biography', 'positions_json', 'custom_position', 'profile_revision'}
    if not required.issubset(member.c.keys()):
        raise ProfileRepairError('Profile schema expansion must be applied before backfill')
    for member_id, user_id in links:
        if by_id[member_id]['linked_user_id'] is None:
            changed = connection.execute(member.update().where(
                member.c.id == member_id, member.c.band_id == band_id,
                member.c.linked_user_id.is_(None)).values(linked_user_id=user_id))
            if changed.rowcount != 1:
                raise ProfileRepairError('Mapping changed concurrently; transaction must roll back')
    from datetime import datetime, timezone
    for user_id in missing:
        name = (active[user_id]['display_name'] or '').strip()[:100] or '멤버'
        if '@' in name:
            name = '멤버'
        result = connection.execute(member.insert().values(
            band_id=band_id, linked_user_id=user_id, name=name, instrument='',
            biography='', positions_json='[]', custom_position='', profile_revision=0,
            created_at=datetime.now(timezone.utc)))
        report['created_member_ids'].append(result.inserted_primary_key[0])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--band-id', type=int, required=True)
    parser.add_argument('--mapping-file', type=Path)
    parser.add_argument('--apply', action='store_true', help='Write in a single transaction; requires authorization')
    args = parser.parse_args(argv)
    raw_url = os.environ.get('MEMBER_PROFILE_DATABASE_URL')
    if not raw_url:
        parser.error('MEMBER_PROFILE_DATABASE_URL must explicitly select the target database')
    engine = None
    try:
        mappings = json.loads(args.mapping_file.read_text(encoding='utf-8')) if args.mapping_file else []
        engine = sa.create_engine(raw_url, hide_parameters=True)
        with engine.begin() as connection:
            if not args.apply and connection.dialect.name == 'postgresql':
                connection.execute(sa.text('SET TRANSACTION READ ONLY'))
            report = repair_profiles(connection, band_id=args.band_id, mappings=mappings, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        # Driver exception strings may contain DSNs, credentials or personal data.
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
