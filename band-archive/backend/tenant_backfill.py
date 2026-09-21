"""Offline, fail-closed Phase 4 tenant backfill.

No storage client is imported here: this module only records canonical names
for objects that already exist.  It never calls R2, never copies/deletes an
object, and deliberately refuses URL/cross-prefix values it cannot prove.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import unquote

from sqlalchemy import text


STATE_KEY = 'phase4_tenant_scope'
LEGACY_BAND_NAME = '들뜬'
LEGACY_BAND_SLUG = 'deutteun-legacy'
SCOPE_TABLES = (
    'song', 'rehearsal', 'media', 'member', 'personal_log', 'gallery_image',
    'announcement', 'song_suggestion', 'comment', 'song_vote', 'media_vote',
    'multipart_upload_session', 'personal_log_multipart_upload_session',
)


class TenantBackfillError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _canonical_key(value, prefix, label, unresolved):
    """Return an existing in-bucket key without guessing at URLs or buckets."""
    if value is None:
        return None
    value = str(value)
    if not value:
        unresolved.append({'field': label, 'reason': 'empty stored name'})
        return None
    if value != value.strip():
        unresolved.append({'field': label, 'reason': 'leading or trailing whitespace is not canonical'})
        return None
    decoded = value
    # This is detection only: stored keys are never decoded or rewritten. A
    # bounded loop also catches a doubly-encoded dot segment without treating
    # an arbitrary URL-ish value as a safe object key.
    for _ in range(4):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    raw_segments = value.split('/')
    decoded_segments = decoded.split('/')
    if ('://' in value or '://' in decoded or value.startswith('/') or '\\' in value
            or '\\' in decoded or any(not segment for segment in raw_segments)
            or any(not segment for segment in decoded_segments)
            or any(segment in {'.', '..'} for segment in raw_segments)
            or any(segment in {'.', '..'} for segment in decoded_segments)):
        unresolved.append({'field': label, 'reason': 'URL, non-canonical separator, or traversal value'})
        return None
    if not prefix:
        return value
    if '/' not in value:
        return f'{prefix}/{value}'
    if value.startswith(f'{prefix}/'):
        return value
    unresolved.append({'field': label, 'reason': f'cross-prefix key (expected {prefix}/)'})
    return None


def _checksum(rows):
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(dict(row), sort_keys=True, default=str, separators=(',', ':')).encode())
        digest.update(b'\n')
    return digest.hexdigest()


def _counts(connection):
    return {table: connection.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in SCOPE_TABLES}


def _legacy_band(connection):
    rows = connection.execute(text(
        'SELECT id, slug, is_legacy_import, created_by_user_id FROM band '
        'WHERE is_legacy_import = 1 OR slug = :slug ORDER BY id'
    ), {'slug': LEGACY_BAND_SLUG}).mappings().all()
    if len(rows) > 1:
        raise TenantBackfillError('ambiguous legacy band: more than one legacy marker/slug exists')
    if rows:
        row = rows[0]
        if (not row['slug'].startswith(LEGACY_BAND_SLUG) or not row['is_legacy_import']
                or row['created_by_user_id'] is not None):
            raise TenantBackfillError('legacy band marker is inconsistent; refusing ambiguous partial state')
        return row['id']
    slug = LEGACY_BAND_SLUG
    suffix = 0
    while connection.execute(text('SELECT 1 FROM band WHERE slug = :slug'), {'slug': slug}).scalar() is not None:
        suffix += 1
        slug = f'{LEGACY_BAND_SLUG}-{suffix}'
    connection.execute(text(
        'INSERT INTO band (name, slug, description, default_visibility, member_roster_public, '
        'storage_quota_bytes, created_by_user_id, is_legacy_import, created_at, updated_at) '
        'VALUES (:name, :slug, :description, :visibility, :roster, :quota, NULL, 1, :now, :now)'
    ), {'name': LEGACY_BAND_NAME, 'slug': slug, 'description': 'Imported legacy archive; ownerless until claimed.',
        'visibility': 'PUBLIC', 'roster': True, 'quota': 20 * 1024 * 1024 * 1024, 'now': _now()})
    return connection.execute(text('SELECT id FROM band WHERE slug = :slug'), {'slug': slug}).scalar_one()


def _assert_resume_safe(connection, band_id):
    for table in SCOPE_TABLES:
        values = set(connection.execute(text(
            f'SELECT DISTINCT band_id FROM {table} WHERE band_id IS NOT NULL'
        )).scalars())
        if values and values != {band_id}:
            raise TenantBackfillError(f'{table} has non-legacy tenant assignments; refusing ambiguous partial state')
    announcements = connection.execute(text('SELECT count(*) FROM announcement WHERE band_id IS NULL')).scalar_one()
    if announcements > 1:
        raise TenantBackfillError('multiple unscoped legacy announcements cannot become one-per-band safely')


def _object_manifest(connection):
    unresolved, updates = [], []
    rules = (
        # Historic sheet uploads used the same ``media/`` R2 namespace as the
        # upload route.  Keep the old compatibility filename and record only
        # the provable existing key; do not copy or inspect object storage.
        ('song', 'id', 'sheet_music', 'sheet_music_object_key', 'media'),
        ('media', 'id', 'filename', 'object_key', 'media'),
        ('media', 'id', 'audio_filename', 'audio_object_key', 'media'),
        ('media', 'id', 'video_720_filename', 'video_720_object_key', 'media'),
        ('personal_log', 'id', 'filename', 'object_key', 'personal_logs'),
        ('personal_log', 'id', 'audio_filename', 'audio_object_key', 'personal_logs'),
        ('gallery_image', 'id', 'filename', 'object_key', 'gallery'),
    )
    for table, pk, source, target, prefix in rules:
        rows = connection.execute(text(
            f'SELECT {pk}, {source}, {target} FROM {table} WHERE {source} IS NOT NULL'
        )).mappings().all()
        for row in rows:
            canonical = _canonical_key(row[target] or row[source], prefix, f'{table}:{row[pk]}:{source}', unresolved)
            if canonical and row[target] != canonical:
                updates.append((table, pk, row[pk], target, canonical))
    # Multipart sessions deliberately keep their exact existing key: replacing
    # it would point an in-flight R2 upload at a nonexistent object.
    for table in ('multipart_upload_session', 'personal_log_multipart_upload_session'):
        for row in connection.execute(text(f'SELECT id, object_key FROM {table}')).mappings():
            _canonical_key(row['object_key'], '', f'{table}:{row["id"]}:object_key', unresolved)
    return updates, unresolved


SCOPE_RELATIONSHIP_CHECKS = (
    ('media/song', 'SELECT m.id FROM media m JOIN song s ON s.id=m.song_id '
     'WHERE m.band_id != s.band_id'),
    ('media/rehearsal', 'SELECT m.id FROM media m JOIN rehearsal r ON r.id=m.rehearsal_id '
     'WHERE m.rehearsal_id IS NOT NULL AND m.band_id != r.band_id'),
    ('personal_log/member', 'SELECT p.id FROM personal_log p JOIN member m ON m.id=p.member_id '
     'WHERE p.band_id != m.band_id'),
    ('session/song', 'SELECT x.id FROM multipart_upload_session x JOIN song s ON s.id=x.song_id '
     'WHERE x.band_id != s.band_id'),
    ('session/rehearsal', 'SELECT x.id FROM multipart_upload_session x JOIN rehearsal r ON r.id=x.rehearsal_id '
     'WHERE x.rehearsal_id IS NOT NULL AND x.band_id != r.band_id'),
    ('session/member', 'SELECT x.id FROM personal_log_multipart_upload_session x JOIN member m ON m.id=x.member_id '
     'WHERE x.band_id != m.band_id'),
    ('rehearsal/song', 'SELECT rs.rehearsal_id FROM rehearsal_songs rs JOIN rehearsal r ON r.id=rs.rehearsal_id '
     'JOIN song s ON s.id=rs.song_id WHERE r.band_id != s.band_id'),
    ('song_vote/song', 'SELECT v.id FROM song_vote v JOIN song s ON s.id=v.song_id '
     'WHERE v.band_id != s.band_id'),
    ('media_vote/media', 'SELECT v.id FROM media_vote v JOIN media m ON m.id=v.media_id '
     'WHERE v.band_id != m.band_id'),
    ('upload/media', 'SELECT x.id FROM multipart_upload_session x JOIN media m ON m.id=x.media_id '
     'WHERE x.media_id IS NOT NULL AND x.band_id != m.band_id'),
    ('upload/log', 'SELECT x.id FROM personal_log_multipart_upload_session x JOIN personal_log p ON p.id=x.personal_log_id '
     'WHERE x.personal_log_id IS NOT NULL AND x.band_id != p.band_id'),
)


def _validate_relationships(connection, band_id):
    defects = []
    for label, statement in SCOPE_RELATIONSHIP_CHECKS:
        ids = connection.execute(text(statement)).scalars().all()
        if ids:
            defects.append({'relationship': label, 'ids': ids[:20]})
    for table in ('song', 'rehearsal', 'media', 'personal_log', 'gallery_image', 'announcement',
                  'song_suggestion', 'comment', 'song_vote', 'media_vote', 'multipart_upload_session',
                  'personal_log_multipart_upload_session'):
        ids = connection.execute(text(
            f'SELECT x.id FROM {table} x JOIN band_membership bm ON bm.id=x.created_by_membership_id '
            'WHERE x.created_by_membership_id IS NOT NULL AND x.band_id != bm.band_id'
        )).scalars().all()
        if ids:
            defects.append({'relationship': f'{table}/creator_membership', 'ids': ids[:20]})
    # A comment root names exactly one parent resource; descendants name only
    # their parent.  Resolve recursively and detect cycles/contradictions.
    comments = {row['id']: dict(row) for row in connection.execute(text(
        'SELECT id, media_id, personal_log_id, parent_id, band_id FROM comment'
    )).mappings()}
    media_bands = dict(connection.execute(text('SELECT id, band_id FROM media')).all())
    log_bands = dict(connection.execute(text('SELECT id, band_id FROM personal_log')).all())
    resolved, visiting = {}, set()
    def scope(comment_id):
        if comment_id in resolved:
            return resolved[comment_id]
        if comment_id in visiting:
            raise TenantBackfillError('comment parent cycle detected')
        visiting.add(comment_id)
        row = comments[comment_id]
        direct = [media_bands.get(row['media_id'])] if row['media_id'] is not None else []
        direct += [log_bands.get(row['personal_log_id'])] if row['personal_log_id'] is not None else []
        if row['parent_id'] is not None:
            if direct or row['parent_id'] not in comments:
                raise TenantBackfillError(f'comment {comment_id} has conflicting/missing parent scope')
            result = scope(row['parent_id'])
        else:
            if len(direct) != 1:
                raise TenantBackfillError(f'comment {comment_id} has no unambiguous root scope')
            result = direct[0] if direct[0] is not None else band_id
        if row['band_id'] is not None and row['band_id'] != result:
            raise TenantBackfillError(f'comment {comment_id} crosses tenant scope')
        visiting.remove(comment_id)
        resolved[comment_id] = result
        return result
    for comment_id in comments:
        scope(comment_id)
    return defects, resolved


def validate_relationships(connection, band_id):
    """Public Phase 4/6 tenant-scope relationship contract."""
    return _validate_relationships(connection, band_id)


def plan(connection):
    """Read-only preflight report. Calling this never opens a transaction to write."""
    state = connection.execute(text('SELECT status, report_json FROM tenant_backfill_state WHERE key=:key'),
                               {'key': STATE_KEY}).mappings().first()
    band_rows = connection.execute(text('SELECT id FROM band WHERE is_legacy_import=1')).scalars().all()
    if len(band_rows) > 1:
        raise TenantBackfillError('ambiguous legacy band markers')
    # Existing marker permits a resumable re-plan; absent marker uses a virtual
    # band id because all legacy rows must be NULL at the expand stage.
    virtual_band = band_rows[0] if band_rows else -1
    if band_rows:
        _assert_resume_safe(connection, virtual_band)
    else:
        for table in SCOPE_TABLES:
            assigned = connection.execute(text(f'SELECT count(*) FROM {table} WHERE band_id IS NOT NULL')).scalar_one()
            if assigned:
                raise TenantBackfillError(f'{table} has tenant rows but no legacy marker')
    defects, _ = _validate_relationships(connection, virtual_band)
    if defects:
        raise TenantBackfillError('cross-scope relationships: ' + json.dumps(defects))
    updates, unresolved = _object_manifest(connection)
    if unresolved:
        raise TenantBackfillError('unresolved object keys: ' + json.dumps(unresolved, ensure_ascii=False))
    return {
        'state': state['status'] if state else 'unstarted',
        'counts': _counts(connection),
        'canonical_object_key_updates': len(updates),
        'unresolved': unresolved,
        'checksum': _checksum(connection.execute(text('SELECT id, slug FROM band ORDER BY id')).mappings().all()),
    }


def apply(connection):
    """Apply an idempotent backfill inside the caller's single transaction."""
    report = plan(connection)
    band_id = _legacy_band(connection)
    _assert_resume_safe(connection, band_id)
    connection.execute(text(
        'INSERT INTO tenant_backfill_state (key,status,updated_at) VALUES (:key,:status,:now) '
        'ON CONFLICT(key) DO UPDATE SET status=:status, updated_at=:now'
    ), {'key': STATE_KEY, 'status': 'applying', 'now': _now()})
    for table in SCOPE_TABLES:
        connection.execute(text(f'UPDATE {table} SET band_id=:band WHERE band_id IS NULL'), {'band': band_id})
    for table in ('song_vote', 'media_vote', 'comment', 'multipart_upload_session',
                  'personal_log_multipart_upload_session'):
        connection.execute(text(f'UPDATE {table} SET is_legacy=1'))
    updates, unresolved = _object_manifest(connection)
    if unresolved:
        raise TenantBackfillError('unresolved object keys during apply')
    for table, pk, row_id, target, value in updates:
        connection.execute(text(f'UPDATE {table} SET {target}=:value WHERE {pk}=:id'), {'value': value, 'id': row_id})
    defects, comment_scopes = _validate_relationships(connection, band_id)
    if defects:
        raise TenantBackfillError('cross-scope relationships: ' + json.dumps(defects))
    for comment_id, scoped_band_id in comment_scopes.items():
        connection.execute(text('UPDATE comment SET band_id=:band, is_legacy=1 WHERE id=:id'),
                           {'band': scoped_band_id, 'id': comment_id})
    # Recheck after recursive comment assignment and capture only non-secret
    # metadata in the operator marker.
    defects, _ = _validate_relationships(connection, band_id)
    if defects:
        raise TenantBackfillError('post-backfill validation failed')
    report.update({'band_id': band_id, 'status': 'validated', 'counts_after': _counts(connection)})
    connection.execute(text(
        'UPDATE tenant_backfill_state SET status=:status, report_json=:report, validated_at=:now, updated_at=:now '
        'WHERE key=:key'
    ), {'key': STATE_KEY, 'status': 'validated', 'report': json.dumps(report, ensure_ascii=False, sort_keys=True),
        'now': _now()})
    return report
