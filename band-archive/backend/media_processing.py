"""Durable, sequential M4A extraction queue backed by the Media table."""

from datetime import datetime, timedelta, timezone
import atexit
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ProcessingSpec:
    kind: str
    model: type
    key_prefix: str
    label: str


MEDIA_SPEC = ProcessingSpec('media', Media, 'media', 'media')
PERSONAL_LOG_SPEC = ProcessingSpec('personal_log', PersonalLog, 'personal_logs', 'personal log')
PROCESSING_SPECS = {MEDIA_SPEC.kind: MEDIA_SPEC, PERSONAL_LOG_SPEC.kind: PERSONAL_LOG_SPEC}


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


def processing_fields(record, key_prefix):
    """Shared JSON fields; callers retain their established endpoint names."""
    original_url = storage.generate_url(f'{key_prefix}/{record.filename}')
    audio_url = None
    if record.file_type == 'video' and record.transcoding_status == 'completed' and record.audio_filename:
        audio_url = storage.generate_url(f'{key_prefix}/{record.audio_filename}')
    return {
        'url': original_url, 'audio_url': audio_url,
        'transcoding_status': record.transcoding_status, 'audio_filename': record.audio_filename,
        'processing_error': record.processing_error,
        'processing_started_at': record.processing_started_at.isoformat() if record.processing_started_at else None,
        'processing_completed_at': record.processing_completed_at.isoformat() if record.processing_completed_at else None,
        'processing_attempts': record.processing_attempts,
        'processing_heartbeat_at': record.processing_heartbeat_at.isoformat() if record.processing_heartbeat_at else None,
    }


def processing_status_response(record, key_prefix):
    fields = processing_fields(record, key_prefix)
    return {
        'id': record.id, 'file_type': record.file_type, 'status': fields['transcoding_status'],
        'audio_filename': fields['audio_filename'], 'audio_url': fields['audio_url'],
        'error': fields['processing_error'], 'attempts': fields['processing_attempts'],
        'started_at': fields['processing_started_at'], 'heartbeat_at': fields['processing_heartbeat_at'],
        'completed_at': fields['processing_completed_at'],
    }


def delete_original_and_audio(record, key_prefix, logger=None):
    """Delete derivative then original; preserve DB state if either delete fails."""
    keys = ([f'{key_prefix}/{record.audio_filename}'] if record.audio_filename else [])
    keys.append(f'{key_prefix}/{record.filename}')
    errors = []
    for key in keys:
        try:
            storage.delete(key)
        except Exception as exc:
            errors.append((key, exc))
            if logger:
                logger.exception('Failed to delete stored object %s', key)
    if errors:
        failed_keys = ', '.join(key for key, _ in errors)
        raise RuntimeError(f'Failed to delete stored objects: {failed_keys}') from errors[0][1]


def create_media(**kwargs):
    return _create_record(MEDIA_SPEC, **kwargs)


def create_personal_log(**kwargs):
    return _create_record(PERSONAL_LOG_SPEC, **kwargs)


def _create_record(spec, **kwargs):
    kwargs['transcoding_status'] = initial_processing_status(kwargs.get('file_type'))
    return spec.model(**kwargs)


def save_media_and_start(app, media):
    return _save_record_and_start(app, media)


def save_personal_log_and_start(app, log):
    return _save_record_and_start(app, log)


def _save_record_and_start(app, record):
    db.session.add(record)
    db.session.commit()
    if record.file_type == 'video':
        start_audio_processing(app, record.id)
    return record


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
    return retry_audio_processing_record(app, media, MEDIA_SPEC)


def retry_audio_processing_record(app, record, spec):
    if record.file_type != 'video':
        raise ValueError(f'Only video {spec.label}s can be retried.')
    if record.transcoding_status in ('queued', 'processing'):
        raise RuntimeError('Audio processing is already queued or running.')
    record.transcoding_status = 'queued'
    record.processing_error = None
    record.processing_started_at = record.processing_completed_at = record.processing_heartbeat_at = None
    db.session.commit()
    start_audio_processing(app, record.id)


def recover_stale_processing(stale_seconds):
    """Return only abandoned processing rows to queue; live heartbeats are preserved."""
    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    recovered = sum(_recover_stale(spec, cutoff) for spec in PROCESSING_SPECS.values())
    db.session.commit()
    return recovered


def _recover_stale(spec, cutoff):
    model = spec.model
    return model.query.filter(model.file_type == 'video', model.transcoding_status == 'processing',
        (model.processing_heartbeat_at.is_(None)) | (model.processing_heartbeat_at < cutoff)).update({
        'transcoding_status': 'queued', 'processing_error': None,
        'processing_started_at': None, 'processing_heartbeat_at': None,
    }, synchronize_session=False)


def _claim_media(media_id=None):
    return _claim_record(MEDIA_SPEC, media_id)


def _claim_record(spec, record_id=None):
    model = spec.model
    now = utcnow()
    query = model.query.filter_by(transcoding_status='queued', file_type='video')
    if record_id is not None:
        query = query.filter_by(id=record_id)
    else:
        candidate = query.order_by(model.created_at.asc(), model.id.asc()).with_entities(model.id).first()
        if not candidate:
            return None
        record_id = candidate.id
        query = query.filter_by(id=record_id)
    claimed = query.update({
        'transcoding_status': 'processing',
        'processing_started_at': now,
        'processing_completed_at': None,
        'processing_heartbeat_at': now,
        'processing_error': None,
        'processing_attempts': func.coalesce(model.processing_attempts, 0) + 1,
    }, synchronize_session=False)
    db.session.commit()
    return record_id if claimed else None


def claim_next_queued_media():
    """Conditionally claim exactly one queued item. Safe across worker processes."""
    while True:
        media_id = _claim_media()
        if media_id is not None:
            return media_id
        return None


def _claim_personal_log(log_id=None):
    return _claim_record(PERSONAL_LOG_SPEC, log_id)


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
    _heartbeat_record(MEDIA_SPEC, media_id)


def _personal_log_heartbeat(log_id):
    _heartbeat_record(PERSONAL_LOG_SPEC, log_id)


def _heartbeat_record(spec, record_id):
    spec.model.query.filter_by(id=record_id, transcoding_status='processing').update({
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
    return process_claimed_record(MEDIA_SPEC, media_id, config)


def process_claimed_personal_log(log_id, config):
    return process_claimed_record(PERSONAL_LOG_SPEC, log_id, config)


def process_claimed_record(spec, record_id, config):
    record = db.session.get(spec.model, record_id)
    if not record or record.transcoding_status != 'processing':
        return
    try:
        audio_filename = extract_m4a_audio(
            record.filename, source_prefix=spec.key_prefix,
            timeout_seconds=config['AUDIO_PROCESSING_TIMEOUT_SECONDS'],
            heartbeat=lambda: _heartbeat_record(spec, record_id),
            heartbeat_seconds=config['AUDIO_PROCESSING_HEARTBEAT_SECONDS'],
        )
        if not storage.exists(f'{spec.key_prefix}/{audio_filename}'):
            raise AudioProcessingError('output_missing')
        record = db.session.get(spec.model, record_id)
        record.audio_filename = audio_filename
        record.transcoding_status = 'completed'
        record.processing_error = None
        record.processing_completed_at = utcnow()
        record.processing_heartbeat_at = utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        record = db.session.get(spec.model, record_id)
        if record:
            record.transcoding_status = 'failed'
            record.processing_error = _safe_error(exc)
            record.processing_completed_at = utcnow()
            record.processing_heartbeat_at = utcnow()
            db.session.commit()
        logging.exception('Audio extraction failed for %s id %s', spec.label, record_id)


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
