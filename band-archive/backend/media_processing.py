"""Centralized media processing state and background audio extraction."""

from datetime import datetime, timezone
import logging
import os
import subprocess
import tempfile
import threading

from extensions import db
from models import Media
from storage import storage

PROCESSING_TIMEOUT_SECONDS = 300
SAFE_ERRORS = {
    'no_audio': 'Video has no audio track.',
    'timeout': 'Audio processing timed out.',
    'output_missing': 'Audio output could not be verified.',
    'processing_failed': 'Audio processing failed.',
}


def initial_processing_status(file_type):
    return 'queued' if file_type == 'video' else 'not_required'


def audio_filename_for(filename):
    base_name = filename.rsplit('.', 1)[0]
    return f'{base_name}_audio.m4a'


def create_media(**kwargs):
    """Build Media with the only allowed initial processing states."""
    file_type = kwargs.get('file_type')
    kwargs['transcoding_status'] = initial_processing_status(file_type)
    return Media(**kwargs)


def save_media_and_start(app, media):
    """Persist a newly uploaded media object, then schedule video audio extraction."""
    db.session.add(media)
    db.session.commit()
    if media.file_type == 'video':
        start_audio_processing(app, media.id)
    return media


def start_audio_processing(app, media_id):
    """Start one daemon worker; only a queued record can claim processing work."""
    thread = threading.Thread(target=_process_audio_job, args=(app, media_id), daemon=True)
    thread.start()


def _process_audio_job(app, media_id):
    with app.app_context():
        claimed = Media.query.filter_by(id=media_id, transcoding_status='queued').update({
            'transcoding_status': 'processing',
            'processing_started_at': datetime.now(timezone.utc),
            'processing_completed_at': None,
            'processing_error': None,
        })
        db.session.commit()
        if not claimed:
            return

        media = db.session.get(Media, media_id)
        try:
            audio_filename = extract_m4a_audio(media.filename)
            if not storage.exists(f'media/{audio_filename}'):
                raise AudioProcessingError('output_missing')
            media.audio_filename = audio_filename
            media.transcoding_status = 'completed'
            media.processing_error = None
            media.processing_completed_at = datetime.now(timezone.utc)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            media = db.session.get(Media, media_id)
            if media:
                media.transcoding_status = 'failed'
                media.processing_error = _safe_error(exc)
                media.processing_completed_at = datetime.now(timezone.utc)
                db.session.commit()
            logging.exception('Audio extraction failed for media id %s', media_id)


class AudioProcessingError(Exception):
    pass


def _safe_error(exc):
    code = str(exc)
    return SAFE_ERRORS.get(code, SAFE_ERRORS['processing_failed'])


def _run_command(args):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=True,
            timeout=PROCESSING_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioProcessingError('timeout') from exc
    except subprocess.CalledProcessError as exc:
        raise AudioProcessingError('processing_failed') from exc


def _audio_codec(input_path):
    result = _run_command([
        'ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', input_path,
    ])
    codec = result.stdout.strip().lower()
    if not codec:
        raise AudioProcessingError('no_audio')
    return codec


def extract_m4a_audio(filename):
    """Download one source video and upload exactly one M4A derivative to R2."""
    output_filename = audio_filename_for(filename)
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, filename)
        output_path = os.path.join(tmpdir, output_filename)
        with open(input_path, 'wb') as source:
            storage.download(f'media/{filename}', source)

        codec = _audio_codec(input_path)
        command = ['ffmpeg', '-y', '-i', input_path, '-vn', '-map', '0:a:0']
        if codec == 'aac':
            command.extend(['-c:a', 'copy'])
        else:
            command.extend(['-c:a', 'aac', '-b:a', '128k'])
        command.append(output_path)
        _run_command(command)

        with open(output_path, 'rb') as output:
            storage.upload(f'media/{output_filename}', output, content_type='audio/mp4')
    return output_filename


def retry_audio_processing(app, media):
    if media.file_type != 'video':
        raise ValueError('Only video media can be retried.')
    if media.transcoding_status in ('queued', 'processing'):
        raise RuntimeError('Audio processing is already queued or running.')
    media.transcoding_status = 'queued'
    media.processing_error = None
    media.processing_started_at = None
    media.processing_completed_at = None
    db.session.commit()
    start_audio_processing(app, media.id)
