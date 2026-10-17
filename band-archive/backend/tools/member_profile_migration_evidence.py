"""Content-free old-row preservation evidence for profile schema/backfill.

No connections are opened here. A reviewed operator owns the transaction,
database selection, backup, migration, locks, security checks and commit.
Snapshots retain hashes, column names and numeric member/link identifiers only.
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from tools.member_profile_backfill import ProfileRepairError

PROFILE_COLUMNS = {'biography', 'positions_json', 'custom_position', 'profile_revision'}


def _digest(row):
    payload = json.dumps(dict(row), sort_keys=True, default=str, ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def capture_old_rows(connection, *, source=None):
    """Hash all source columns. A later capture uses the original projection.

    Include every existing application table (including calendar/policy data),
    not an historical handwritten inventory. Alembic's version row is checked
    independently by the migration runner.
    """
    inspector = sa.inspect(connection)
    names = sorted(name for name in inspector.get_table_names() if name != 'alembic_version')
    if source is not None and names != sorted(source['tables']):
        raise ProfileRepairError('Unapproved table topology change')
    result = {'tables': {}, 'member_rows': {}}
    for name in names:
        table = sa.Table(name, sa.MetaData(), autoload_with=connection)
        columns = list(table.c.keys()) if source is None else source['tables'][name]['columns']
        actual = set(table.c.keys())
        expected = set(columns) | (PROFILE_COLUMNS if name == 'member' and source is not None else set())
        if source is not None and actual != expected:
            raise ProfileRepairError('Unapproved column topology change')
        hashes = []
        for row in connection.execute(sa.select(*(table.c[column] for column in columns))).mappings():
            values = dict(row)
            row_hash = _digest(values)
            hashes.append(row_hash)
            if name == 'member':
                without_link = {key: value for key, value in values.items() if key != 'linked_user_id'}
                result['member_rows'][str(values['id'])] = {
                    'sha256': row_hash, 'without_link_sha256': _digest(without_link),
                    'band_id': values['band_id'], 'linked_user_id': values['linked_user_id'],
                }
        digest = hashlib.sha256('\n'.join(sorted(hashes)).encode('ascii')).hexdigest()
        result['tables'][name] = {'columns': columns, 'rows': len(hashes), 'sha256': digest}
    return result


def assert_old_rows_preserved(before, after, *, approved_new_member_ids=(), approved_links=()):
    """Allow only manifest-listed new members and exact old unlinked mappings."""
    if set(before['tables']) != set(after['tables']):
        raise ProfileRepairError('Unapproved table topology change')
    for name, record in before['tables'].items():
        if name != 'member' and record != after['tables'][name]:
            raise ProfileRepairError('Existing non-member data changed')
        if record['columns'] != after['tables'][name]['columns']:
            raise ProfileRepairError('Existing column projection changed')
    old = before['member_rows']
    current = after['member_rows']
    new_ids = tuple(approved_new_member_ids)
    inserted = {str(value) for value in new_ids}
    if len(inserted) != len(new_ids):
        raise ProfileRepairError('Duplicate inserted member manifest')
    links = {}
    for item in approved_links:
        if set(item) != {'member_id', 'user_id', 'band_id'}:
            raise ProfileRepairError('Exact mapping manifest fields required')
        key = str(item['member_id'])
        if key in links or key not in old or old[key]['band_id'] != item['band_id']:
            raise ProfileRepairError('Invalid mapping manifest')
        if old[key]['linked_user_id'] not in {None, item['user_id']}:
            raise ProfileRepairError('Mapping cannot reassign an existing account')
        links[key] = item['user_id']
    if set(current) != set(old) | inserted or set(old) & inserted:
        raise ProfileRepairError('Inserted or removed member differs from approved manifest')
    for key, record in old.items():
        now = current[key]
        if key in links:
            if (now['linked_user_id'] != links[key]
                    or now['without_link_sha256'] != record['without_link_sha256']):
                raise ProfileRepairError('Explicit link changed unrelated legacy fields')
        elif now != record:
            raise ProfileRepairError('Existing member data changed')
    return True
