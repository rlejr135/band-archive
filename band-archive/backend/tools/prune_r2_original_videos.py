"""Prune verified original song videos after the immutable 720p rollout.

This command is deliberately separate from conversion/finalize.  It only
deletes an original after the DB link, 720p object, and completed M4A object
are present.  It is dry-run by default and emits only media IDs/reason codes.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from extensions import db  # noqa: E402
from models import Media  # noqa: E402
from storage import storage  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


def _head_nonempty(key):
    head = storage.head(key)
    size = head.get('ContentLength')
    return isinstance(size, int) and not isinstance(size, bool) and size > 0, head


def _etag(head):
    value = head.get('ETag') or head.get('etag')
    return value.strip('"') if isinstance(value, str) else None


def _event(media_id, state, reason=''):
    suffix = f' reason={reason}' if reason else ''
    print(f'media={media_id} state={state}{suffix}')


def _validate(media):
    if media.file_type != 'video':
        return 'blocked', 'not_video'
    if media.original_pruned_at:
        return 'skipped', 'already_pruned'
    if not media.video_720_filename or not media.video_720_source_etag:
        return 'blocked', '720_reference_missing'
    if media.transcoding_status != 'completed' or not media.audio_filename:
        return 'blocked', 'audio_not_completed'
    try:
        derivative_ok, _ = _head_nonempty(media.video_720_filename)
        audio_ok, _ = _head_nonempty(f'media/{media.audio_filename}')
        original_ok, original_head = _head_nonempty(f'media/{media.filename}')
    except Exception:
        return 'blocked', 'r2_verification_failed'
    if not derivative_ok:
        return 'blocked', '720_object_missing'
    if not audio_ok:
        return 'blocked', 'audio_object_missing'
    if not original_ok:
        return 'reconcile', 'original_already_missing'
    if _etag(original_head) != media.video_720_source_etag:
        return 'blocked', 'original_etag_mismatch'
    return 'eligible', ''


def prune_originals(apply=False):
    """Return a secret-safe summary; delete only fully verified originals."""
    results = []
    for media_id in [row.id for row in Media.query.filter_by(file_type='video').order_by(Media.id).all()]:
        media = db.session.get(Media, media_id)
        state, reason = _validate(media)
        if state == 'eligible' and apply:
            # Re-load immediately before delete so a concurrent rename/delete
            # cannot change the DB record between validation and mutation.
            db.session.rollback()
            media = db.session.get(Media, media_id)
            state, reason = _validate(media)
            if state == 'eligible':
                storage.delete(f'media/{media.filename}')
                media.original_pruned_at = _now()
                db.session.commit()
                state = 'pruned'
        elif state == 'reconcile' and apply:
            media.original_pruned_at = _now()
            db.session.commit()
            state = 'reconciled'
        result = {'media_id': media_id, 'state': state}
        if reason:
            result['reason'] = reason
        results.append(result)
        _event(media_id, state, reason)
    return results


def parse(argv=None):
    parser = argparse.ArgumentParser(description='Prune verified original R2 song videos.')
    parser.add_argument('--apply', action='store_true', help='delete eligible originals and record their prune time')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    from app import create_app

    app = create_app()
    with app.app_context():
        results = prune_originals(apply=args.apply)
    return int(any(result['state'] == 'blocked' for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
