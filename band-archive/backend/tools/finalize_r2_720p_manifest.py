"""CAS-link completed immutable 720p objects from a private R2 manifest.

Run this only on an existing production application machine with its /data
volume mounted.  It has no R2 write/delete operation; default mode is a DB/R2
read-only preflight and ``--apply`` updates Media reference fields only.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


# ``python tools/finalize_...py`` must be able to import the application when
# launched from a Fly command, not just when backend is the current directory.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tools.migrate_r2_720p import (  # noqa: E402
    PROFILE, MigrationError, R2, destination_key, get_etag, load_bytes, metadata, safe_error,
)


def _event(media_id, state, reason=''):
    # Match the worker's secret-safe log contract: no R2 keys or exception text.
    suffix = f' reason={reason}' if reason else ''
    print(f'media={media_id} state={state}{suffix}')


def _item_is_complete(item):
    source, output = item.get('source') or {}, item.get('output') or {}
    required = ('media_id', 'source_key', 'filename', 'state')
    return (all(name in item for name in required) and item['state'] == 'completed' and
            all(source.get(name) for name in ('etag', 'sha256')) and source.get('size') is not None and
            all(output.get(name) for name in ('key', 'sha256', 'profile', 'content_type')) and
            output.get('size') is not None)


def _verify_item(r2, item):
    if not _item_is_complete(item):
        raise MigrationError('manifest_item_not_completed')
    source, output = item['source'], item['output']
    if item['source_key'] != f"media/{item['filename']}":
        raise MigrationError('manifest_source_key_mismatch')
    if output['key'] == item['source_key'] or output['key'] != destination_key(
            item['media_id'], source['etag'], item['filename']):
        raise MigrationError('manifest_destination_key_mismatch')
    source_head = r2.head(item['source_key'])
    if get_etag(source_head) != source['etag'] or int(source_head.get('ContentLength', -1)) != source['size']:
        raise MigrationError('source_changed_before_finalize')
    output_head = r2.head(output['key'])
    if (int(output_head.get('ContentLength', -1)) != output['size'] or
            (output_head.get('ContentType') or '') != output['content_type'] or
            metadata(output_head, 'migration-profile') != PROFILE or
            metadata(output_head, 'source-etag') != source['etag'] or
            metadata(output_head, 'sha256') != output['sha256']):
        raise MigrationError('derivative_verification_failed')
    sha256, size = r2.stream_hash(output['key'])
    if sha256 != output['sha256'] or size != output['size']:
        raise MigrationError('derivative_sha256_mismatch')


def finalize_items(r2, manifest, media_model, session, apply=False, continue_on_error=False):
    """Validate every item and optionally update only the matching Media row.

    The source object ETag plus Media.id + current filename form the CAS
    boundary.  A prior finalize for exactly the same immutable object is an
    idempotent success; a different existing reference is never replaced.
    """
    from sqlalchemy import text

    # Flask-SQLAlchemy exposes ``db.session`` as a scoped-session registry.
    # Its query/commit proxy methods work directly, but SQLAlchemy 2's
    # transaction-inspection API belongs to the concrete Session instance.
    # Resolve it once so the command behaves the same in a Flask app process
    # and in unit tests that pass a Session directly.
    if not hasattr(session, 'in_transaction') and callable(session):
        session = session()

    # The dedicated command must not inherit an application/request transaction.
    # Explicitly clear it before doing any long R2 preflight work.
    if session.in_transaction():
        session.rollback()

    failed, results, preflight = False, [], []
    # R2 HEAD + full SHA work happens with *no DB transaction/lock held*.
    for item in manifest['items']:
        media_id = item.get('media_id', '?')
        try:
            _verify_item(r2, item)
            preflight.append((item, (item['output']['key'], item['source']['etag'], PROFILE)))
        except Exception as exc:
            failed = True
            reason = safe_error(exc)
            results.append({'media_id': media_id, 'state': 'blocked', 'reason': reason})
            _event(media_id, 'blocked', reason)
    if failed:
        return results, 1

    # A dry run is strictly read-only: it must not take SQLite's writer lock
    # while an ordinary member action is trying to commit.  Only `--apply`
    # enters the short, all-or-nothing DB CAS section.  `get_bind()` works
    # with scoped SQLAlchemy sessions whereas `.bind` can be None.
    dialect = session.get_bind().dialect.name
    if apply and dialect == 'sqlite':
        session.execute(text('BEGIN IMMEDIATE'))
    prepared = []
    try:
        for item, desired in preflight:
            query = session.query(media_model).filter_by(id=item['media_id'])
            media = query.first() if dialect == 'sqlite' else query.with_for_update().first()
            if not media or media.file_type != 'video':
                raise MigrationError('media_not_found_or_not_video')
            if media.filename != item['filename']:
                raise MigrationError('media_filename_cas_conflict')
            if apply:
                # The long SHA preflight is already complete.  Re-check only
                # this original object's immutable identity while the short
                # DB CAS lock is held, closing the preflight-to-commit change
                # window without holding locks during network streaming.
                source_head = r2.head(item['source_key'])
                if (get_etag(source_head) != item['source']['etag'] or
                        int(source_head.get('ContentLength', -1)) != item['source']['size']):
                    raise MigrationError('source_changed_during_finalize_lock')
            existing = (media.video_720_filename, media.video_720_source_etag, media.video_720_profile)
            if any(existing) and existing != desired:
                raise MigrationError('media_720_reference_conflict')
            prepared.append((item, media, desired, 'already_finalized' if any(existing) else
                            ('finalized' if apply else 'would_finalize')))
        if apply:
            for _item, media, desired, state in prepared:
                if state == 'finalized':
                    media.video_720_filename, media.video_720_source_etag, media.video_720_profile = desired
                    media.video_720_completed_at = datetime.now(timezone.utc)
            session.commit()
        else:
            session.rollback()
    except Exception as exc:
        session.rollback()
        # No item was mutated/committed. Keep errors secret-safe and batch-wide.
        reason = safe_error(exc)
        return [{'media_id': item['media_id'], 'state': 'blocked', 'reason': reason} for item, _ in preflight], 1
    for item, _media, _desired, state in prepared:
        results.append({'media_id': item['media_id'], 'state': state})
        _event(item['media_id'], state)
    return results, 0


def parse(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--finalize-manifest', required=True)
    parser.add_argument('--apply', action='store_true', help='perform DB reference updates after preflight')
    parser.add_argument('--continue-on-error', action='store_true')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    # These imports happen only in the production app-machine finalize command,
    # never in the disposable R2 conversion worker.
    from app import create_app
    from extensions import db
    from models import Media

    app = create_app()
    with app.app_context():
        r2 = R2.configured()
        manifest = load_bytes(r2.get_manifest(args.finalize_manifest))
        return finalize_items(r2, manifest, Media, db.session, apply=args.apply,
                              continue_on_error=args.continue_on_error)


if __name__ == '__main__':
    raise SystemExit(main()[1])
