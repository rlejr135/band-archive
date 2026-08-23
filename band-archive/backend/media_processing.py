"""Durable, sequential M4A extraction queue backed by the Media table."""

from datetime import datetime, timedelta, timezone
import atexit
import logging
import os
import subprocess
import tempfile
import threading
import time

from sqlalchemy import func

from extensions import db
from models import Media, PersonalLog
from storage import storage

SAFE_ERRORS = {
    'no_audio': 'Video has no audio track.',
    'timeout': 'Audio processing timed out.',
    'output_missing': 'Audio output could not be verified.',
    'source_missing': 'Source video object is missing.',
    'processing_failed': 'Audio processing failed.',
}


class AudioProcessingError(Exception):
    pass


class AudioWorker:
    """One sequential worker per Python process; DB conditional claims are global."""

    def __init__(self, app):
        self.app = app
        self.wake_event = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name='audio-worker', daemon=True)
        self.thread.start()

    def wake(self):
        self.wake_event.set()

    def stop(self, timeout=5):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=timeout)

    def _run(self):
        poll_seconds = self.app.config['AUDIO_WORKER_POLL_SECONDS']
        while not self.stop_event.is_set():
            with self.app.app_context():
                item = claim_next_queued_item()
                if item is not None:
                    kind, item_id = item
                    if kind == 'media':
                        process_claimed_media(item_id, self.app.config)
                    else:
                        process_claimed_personal_log(item_id, self.app.config)
                    continue
            self.wake_event.wait(poll_seconds)
            self.wake_event.clear()


def utcnow():
    return datetime.now(timezone.utc)


def initial_processing_status(file_type):
    return 'queued' if file_type == 'video' else 'not_required'


def audio_filename_for(filename):
    return f"{filename.rsplit('.', 1)[0]}_audio.m4a"


def create_media(**kwargs):
    kwargs['transcoding_status'] = initial_processing_status(kwargs.get('file_type'))
    return Media(**kwargs)


def create_personal_log(**kwargs):
    kwargs['transcoding_status'] = initial_processing_status(kwargs.get('file_type'))
    return PersonalLog(**kwargs)


def save_media_and_start(app, media):
    """Persist a queued media record, then wake (never create) the process worker."""
    db.session.add(media)
    db.session.commit()
    if media.file_type == 'video':
        start_audio_processing(app, media.id)
    return media


def save_personal_log_and_start(app, log):
    db.session.add(log)
    db.session.commit()
    if log.file_type == 'video':
        start_audio_processing(app, log.id)
    return log


def start_worker(app):
    """Recover stale work and start/reuse this process's one worker."""
    with app.app_context():
        recover_stale_processing(app.config['AUDIO_PROCESSING_STALE_SECONDS'])
    worker = app.extensions.get('audio_worker')
    if worker is None:
        worker = AudioWorker(app)
        app.extensions['audio_worker'] = worker
        # Gunicorn/Fly process shutdown invokes this bounded join. Daemon mode
        # remains a final safeguard if the platform kills the process sooner.
        atexit.register(worker.stop)
    worker.start()
    worker.wake()
    return worker


def stop_worker(app, timeout=5):
    worker = app.extensions.get('audio_worker')
    if worker:
        worker.stop(timeout)


def start_audio_processing(app, media_id):
    """Part 1 compatible enqueue/wakeup entry point. `media_id` is already queued."""
    worker = app.extensions.get('audio_worker')
    if worker:
        worker.wake()


def retry_audio_processing(app, media):
    if media.file_type != 'video':
        raise ValueError('Only video media can be retried.')
    if media.transcoding_status in ('queued', 'processing'):
        raise RuntimeError('Audio processing is already queued or running.')
    media.transcoding_status = 'queued'
    media.processing_error = None
    media.processing_started_at = None
    media.processing_completed_at = None
    media.processing_heartbeat_at = None
    db.session.commit()
    start_audio_processing(app, media.id)


def recover_stale_processing(stale_seconds):
    """Return only abandoned processing rows to queue; live heartbeats are preserved."""
    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    recovered = Media.query.filter(
        Media.file_type == 'video',
        Media.transcoding_status == 'processing',
        (Media.processing_heartbeat_at.is_(None)) | (Media.processing_heartbeat_at < cutoff),
    ).update({
        'transcoding_status': 'queued',
        'processing_error': None,
        'processing_started_at': None,
        'processing_heartbeat_at': None,
    }, synchronize_session=False)
    recovered += PersonalLog.query.filter(
        PersonalLog.file_type == 'video',
        PersonalLog.transcoding_status == 'processing',
        (PersonalLog.processing_heartbeat_at.is_(None)) | (PersonalLog.processing_heartbeat_at < cutoff),
    ).update({
        'transcoding_status': 'queued', 'processing_error': None,
        'processing_started_at': None, 'processing_heartbeat_at': None,
    }, synchronize_session=False)
    db.session.commit()
    return recovered


def _claim_media(media_id=None):
    now = utcnow()
    query = Media.query.filter_by(transcoding_status='queued', file_type='video')
    if media_id is not None:
        query = query.filter_by(id=media_id)
    else:
        candidate = query.order_by(Media.created_at.asc(), Media.id.asc()).with_entities(Media.id).first()
        if not candidate:
            return None
        media_id = candidate.id
        query = query.filter_by(id=media_id)
    claimed = query.update({
        'transcoding_status': 'processing',
        'processing_started_at': now,
        'processing_completed_at': None,
        'processing_heartbeat_at': now,
        'processing_error': None,
        'processing_attempts': func.coalesce(Media.processing_attempts, 0) + 1,
    }, synchronize_session=False)
    db.session.commit()
    return media_id if claimed else None


def claim_next_queued_media():
    """Conditionally claim exactly one queued item. Safe across worker processes."""
    while True:
        media_id = _claim_media()
        if media_id is not None:
            return media_id
        return None


def _claim_personal_log(log_id=None):
    now = utcnow()
    query = PersonalLog.query.filter_by(transcoding_status='queued', file_type='video')
    if log_id is not None:
        query = query.filter_by(id=log_id)
    else:
        candidate = query.order_by(PersonalLog.created_at.asc(), PersonalLog.id.asc()).with_entities(PersonalLog.id).first()
        if not candidate:
            return None
        log_id = candidate.id
        query = query.filter_by(id=log_id)
    claimed = query.update({
        'transcoding_status': 'processing', 'processing_started_at': now,
        'processing_completed_at': None, 'processing_heartbeat_at': now,
        'processing_error': None,
        'processing_attempts': func.coalesce(PersonalLog.processing_attempts, 0) + 1,
    }, synchronize_session=False)
    db.session.commit()
    return log_id if claimed else None


def claim_next_queued_item():
    """Claim the oldest queued video from either Media or PersonalLog."""
    media = Media.query.filter_by(transcoding_status='queued', file_type='video').order_by(Media.created_at.asc(), Media.id.asc()).first()
    log = PersonalLog.query.filter_by(transcoding_status='queued', file_type='video').order_by(PersonalLog.created_at.asc(), PersonalLog.id.asc()).first()
    if not media and not log:
        return None
    if log is None or (media is not None and media.created_at <= log.created_at):
        item_id = _claim_media(media.id)
        return ('media', item_id) if item_id else None
    item_id = _claim_personal_log(log.id)
    return ('personal_log', item_id) if item_id else None


def _heartbeat(media_id):
    Media.query.filter_by(id=media_id, transcoding_status='processing').update({
        'processing_heartbeat_at': utcnow(),
    }, synchronize_session=False)
    db.session.commit()


def _personal_log_heartbeat(log_id):
    PersonalLog.query.filter_by(id=log_id, transcoding_status='processing').update({
        'processing_heartbeat_at': utcnow(),
    }, synchronize_session=False)
    db.session.commit()


def _process_audio_job(app, media_id):
    """Part 1-compatible synchronous entry point used by tests and maintenance."""
    with app.app_context():
        claimed_id = _claim_media(media_id)
        if claimed_id is not None:
            process_claimed_media(claimed_id, app.config)


def process_claimed_media(media_id, config):
    media = db.session.get(Media, media_id)
    if not media or media.transcoding_status != 'processing':
        return
    try:
        audio_filename = extract_m4a_audio(
            media.filename,
            timeout_seconds=config['AUDIO_PROCESSING_TIMEOUT_SECONDS'],
            heartbeat=lambda: _heartbeat(media_id),
            heartbeat_seconds=config['AUDIO_PROCESSING_HEARTBEAT_SECONDS'],
        )
        if not storage.exists(f'media/{audio_filename}'):
            raise AudioProcessingError('output_missing')
        media = db.session.get(Media, media_id)
        media.audio_filename = audio_filename
        media.transcoding_status = 'completed'
        media.processing_error = None
        media.processing_completed_at = utcnow()
        media.processing_heartbeat_at = utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        media = db.session.get(Media, media_id)
        if media:
            media.transcoding_status = 'failed'
            media.processing_error = _safe_error(exc)
            media.processing_completed_at = utcnow()
            media.processing_heartbeat_at = utcnow()
            db.session.commit()
        logging.exception('Audio extraction failed for media id %s', media_id)


def process_claimed_personal_log(log_id, config):
    log = db.session.get(PersonalLog, log_id)
    if not log or log.transcoding_status != 'processing':
        return
    try:
        audio_filename = extract_m4a_audio(
            log.filename, source_prefix='personal_logs',
            timeout_seconds=config['AUDIO_PROCESSING_TIMEOUT_SECONDS'],
            heartbeat=lambda: _personal_log_heartbeat(log_id),
            heartbeat_seconds=config['AUDIO_PROCESSING_HEARTBEAT_SECONDS'],
        )
        if not storage.exists(f'personal_logs/{audio_filename}'):
            raise AudioProcessingError('output_missing')
        log = db.session.get(PersonalLog, log_id)
        log.audio_filename = audio_filename
        log.transcoding_status = 'completed'
        log.processing_error = None
        log.processing_completed_at = utcnow()
        log.processing_heartbeat_at = utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log = db.session.get(PersonalLog, log_id)
        if log:
            log.transcoding_status = 'failed'
            log.processing_error = _safe_error(exc)
            log.processing_completed_at = utcnow()
            log.processing_heartbeat_at = utcnow()
            db.session.commit()
        logging.exception('Audio extraction failed for personal log id %s', log_id)


def _safe_error(exc):
    return SAFE_ERRORS.get(str(exc), SAFE_ERRORS['processing_failed'])


def _run_command(args, timeout_seconds, heartbeat=None, heartbeat_seconds=15):
    if heartbeat is None:
        try:
            return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise AudioProcessingError('timeout') from exc
        except subprocess.CalledProcessError as exc:
            raise AudioProcessingError('processing_failed') from exc

    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            process.communicate()
            raise AudioProcessingError('timeout')
        heartbeat()
        time.sleep(min(heartbeat_seconds, max(0.1, deadline - time.monotonic())))
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise AudioProcessingError('processing_failed')
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _audio_codec(input_path, timeout_seconds):
    result = _run_command([
        'ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name',
        '-of', 'default=noprint_wrappers=1:nokey=1', input_path,
    ], timeout_seconds)
    codec = result.stdout.strip().lower()
    if not codec:
        raise AudioProcessingError('no_audio')
    return codec


def extract_m4a_audio(filename, source_prefix='media', timeout_seconds=1800, heartbeat=None, heartbeat_seconds=15):
    """Download one source video and upload exactly one M4A derivative to R2."""
    output_filename = audio_filename_for(filename)
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, filename)
        output_path = os.path.join(tmpdir, output_filename)
        with open(input_path, 'wb') as source:
            storage.download(f'{source_prefix}/{filename}', source)
        codec = _audio_codec(input_path, timeout_seconds)
        command = ['ffmpeg', '-y', '-i', input_path, '-vn', '-map', '0:a:0']
        command.extend(['-c:a', 'copy'] if codec == 'aac' else ['-c:a', 'aac', '-b:a', '128k'])
        command.append(output_path)
        _run_command(command, timeout_seconds, heartbeat, heartbeat_seconds)
        with open(output_path, 'rb') as output:
            storage.upload(f'{source_prefix}/{output_filename}', output, content_type='audio/mp4')
    return output_filename
