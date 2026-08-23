"""Inspect legacy media/R2 state; only mutate when explicitly passed --enqueue."""

import argparse

from sqlalchemy import and_, or_

from app import create_app
from extensions import db
from media_processing import SAFE_ERRORS, audio_filename_for
from models import Media
from storage import storage


def repair_media_processing(app, enqueue=False, limit=None, storage_client=storage):
    """Return an idempotent repair summary. Default mode is a read-only dry run."""
    summary = {
        'examined': 0, 'would_change': 0, 'changed': 0, 'completed': 0,
        'queued': 0, 'failed': 0, 'non_video_fixed': 0, 'source_missing': 0,
        'storage_errors': 0,
    }
    with app.app_context():
        non_video_needing_correction = and_(
            or_(Media.file_type.is_(None), Media.file_type != 'video'),
            or_(Media.transcoding_status.is_(None), Media.transcoding_status != 'not_required'),
        )
        # Apply --limit after selecting repair candidates. Normal non-video rows
        # must not consume the video recovery budget merely because of their id.
        query = Media.query.filter(or_(
            Media.file_type == 'video',
            non_video_needing_correction,
        )).order_by(Media.id.asc())
        if limit is not None:
            query = query.limit(limit)
        for media in query:
            summary['examined'] += 1
            desired = None
            if media.file_type != 'video':
                if media.transcoding_status != 'not_required':
                    desired = {'transcoding_status': 'not_required', 'processing_error': None}
                    summary['non_video_fixed'] += 1
            else:
                expected_audio = audio_filename_for(media.filename)
                try:
                    source_exists = storage_client.exists(f'media/{media.filename}')
                    audio_exists = storage_client.exists(f'media/{expected_audio}')
                except Exception:
                    summary['storage_errors'] += 1
                    continue
                if not source_exists:
                    summary['source_missing'] += 1
                if audio_exists:
                    desired = {
                        'transcoding_status': 'completed', 'audio_filename': expected_audio,
                        'processing_error': None,
                    }
                    summary['completed'] += 1
                elif source_exists:
                    desired = {
                        'transcoding_status': 'queued', 'audio_filename': None,
                        'processing_error': None, 'processing_started_at': None,
                        'processing_completed_at': None, 'processing_heartbeat_at': None,
                    }
                    summary['queued'] += 1
                else:
                    desired = {
                        'transcoding_status': 'failed', 'audio_filename': None,
                        'processing_error': SAFE_ERRORS['source_missing'],
                    }
                    summary['failed'] += 1

            if desired and any(getattr(media, key) != value for key, value in desired.items()):
                summary['would_change'] += 1
                if enqueue:
                    for key, value in desired.items():
                        setattr(media, key, value)
                    summary['changed'] += 1
        if enqueue:
            db.session.commit()
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='Dry-run repair for Media audio processing state.')
    parser.add_argument('--enqueue', action='store_true', help='Apply corrections and queue missing audio.')
    parser.add_argument('--limit', type=int, help='Maximum Media rows to inspect.')
    args = parser.parse_args(argv)
    app = create_app(start_worker=False)
    summary = repair_media_processing(app, enqueue=args.enqueue, limit=args.limit)
    print(('APPLIED' if args.enqueue else 'DRY RUN'), summary)
    return 0 if summary['storage_errors'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
