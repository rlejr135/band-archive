import re
import hashlib
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, redirect, current_app
from werkzeug.utils import secure_filename

from extensions import db
from models import Song, SongVote, Media, Rehearsal
from sqlalchemy import text
from errors import ValidationError, NotFoundError
from storage import storage
from media_processing import (create_media, save_media_and_start, retry_audio_processing,
                              delete_original_and_audio, processing_status_response)
from validators import (
    validate_status,
    validate_difficulty,
    validate_required_string,
    validate_non_empty_string,
    validate_string_length,
    allowed_file,
    generate_secure_filename,
    detect_file_type,
    guess_content_type,
    ALLOWED_EXTENSIONS,
)

songs_bp = Blueprint('songs', __name__)
VOTER_ID_HEADER = 'X-Voter-ID'


def _get_song_or_404(id):
    song = db.session.get(Song, id)
    if not song:
        raise NotFoundError()
    return song


def _voter_hash(required=False):
    """Hash a validated client UUID; never persist or log its raw value."""
    voter_id = request.headers.get(VOTER_ID_HEADER)
    if not voter_id:
        if required:
            raise ValidationError(f'{VOTER_ID_HEADER} header is required.')
        return None
    try:
        canonical_id = str(uuid.UUID(voter_id.strip()))
    except (AttributeError, ValueError):
        raise ValidationError(f'{VOTER_ID_HEADER} must be a valid UUID.')
    return hashlib.sha256(canonical_id.encode('utf-8')).hexdigest()


def _song_with_viewer_vote(song, voter_hash=None):
    viewer_vote = 0
    if voter_hash:
        vote = SongVote.query.filter_by(song_id=song.id, voter_hash=voter_hash).first()
        viewer_vote = vote.value if vote else 0
    return song.to_dict(viewer_vote=viewer_vote)



@songs_bp.route('/')
def home():
    return jsonify({"message": "Band Archive API is running!"})


@songs_bp.route('/songs', methods=['GET'])
def get_songs():
    voter_hash = _voter_hash()
    query = Song.query

    q = request.args.get('q')
    if q:
        query = query.filter(
            Song.title.ilike(f'%{q}%') | Song.artist.ilike(f'%{q}%')
        )

    status = request.args.get('status')
    if status:
        query = query.filter(Song.status == status)

    genre = request.args.get('genre')
    if genre:
        query = query.filter(Song.genre == genre)

    songs = query.order_by(Song.vote_score.desc(), Song.id.asc()).all()
    viewer_votes = {}
    if voter_hash and songs:
        viewer_votes = {
            vote.song_id: vote.value
            for vote in SongVote.query.filter(
                SongVote.voter_hash == voter_hash,
                SongVote.song_id.in_([song.id for song in songs]),
            ).all()
        }
    return jsonify([song.to_dict(viewer_vote=viewer_votes.get(song.id, 0)) for song in songs])


@songs_bp.route('/songs/<int:id>', methods=['GET'])
def get_song(id):
    song = _get_song_or_404(id)
    return jsonify(_song_with_viewer_vote(song, _voter_hash()))


@songs_bp.route('/songs/<int:id>/vote', methods=['PATCH'])
def vote_song(id):
    voter_hash = _voter_hash(required=True)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('Request body is required')
    value = data.get('vote')
    if isinstance(value, bool) or value not in (-1, 0, 1):
        raise ValidationError('vote must be -1, 0, or 1.')
    if 'expected_viewer_vote' not in data:
        raise ValidationError('expected_viewer_vote is required.')
    expected_value = data.get('expected_viewer_vote')
    if isinstance(expected_value, bool) or expected_value not in (-1, 0, 1):
        raise ValidationError('expected_viewer_vote must be -1, 0, or 1.')

    try:
        # SQLite has no row-level SELECT FOR UPDATE.  BEGIN IMMEDIATE obtains a
        # write reservation before reading the prior vote, preventing duplicate
        # rows and stale cache-counter updates between simultaneous requests.
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
            song = db.session.get(Song, id)
        else:
            song = Song.query.filter_by(id=id).with_for_update().first()
        if not song:
            raise NotFoundError()

        previous_vote = SongVote.query.filter_by(song_id=id, voter_hash=voter_hash).first()
        previous_value = previous_vote.value if previous_vote else 0
        if previous_value != expected_value:
            # The caller read stale state in another tab.  Do not let a stale
            # idempotent/switch/cancel request overwrite the current vote.
            # Materialize while the row lock is still held: after rollback a
            # competing request could switch the vote before lazy attributes
            # or relationships are read for the conflict response.
            conflict_song = song.to_dict(viewer_vote=previous_value)
            db.session.rollback()
            return jsonify({
                'error': 'vote_conflict',
                'code': 'vote_conflict',
                'song': conflict_song,
            }), 409
        if previous_value != value:
            if previous_vote and value == 0:
                db.session.delete(previous_vote)
            elif previous_vote:
                previous_vote.value = value
            else:
                db.session.add(SongVote(song_id=id, voter_hash=voter_hash, value=value))

            upvote_delta = int(value == 1) - int(previous_value == 1)
            downvote_delta = int(value == -1) - int(previous_value == -1)
            song.upvote_count = max(0, (song.upvote_count or 0) + upvote_delta)
            song.downvote_count = max(0, (song.downvote_count or 0) + downvote_delta)
            song.vote_score = song.upvote_count - song.downvote_count
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(song.to_dict(viewer_vote=value)), 200


@songs_bp.route('/songs', methods=['POST'])
def add_song():
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    validate_required_string(data.get('title'), 'title')
    validate_string_length(data.get('title'), 'title', 100)
    validate_required_string(data.get('artist'), 'artist')
    validate_string_length(data.get('artist'), 'artist', 100)
    validate_string_length(data.get('link'), 'link', 200)
    validate_string_length(data.get('genre'), 'genre', 50)

    status = data.get('status', 'Practice')
    validate_status(status)

    difficulty = data.get('difficulty', 3)
    validate_difficulty(difficulty)

    new_song = Song(
        title=data['title'],
        artist=data['artist'],
        status=status,
        chords=data.get('chords'),
        link=data.get('link'),
        memo=data.get('memo'),
        genre=data.get('genre'),
        difficulty=difficulty,
    )
    db.session.add(new_song)
    db.session.commit()
    return jsonify(new_song.to_dict()), 201


@songs_bp.route('/songs/<int:id>', methods=['PUT'])
def update_song(id):
    song = _get_song_or_404(id)

    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    if 'title' in data:
        validate_non_empty_string(data['title'], 'title')
        validate_string_length(data['title'], 'title', 100)
        song.title = data['title']

    if 'artist' in data:
        validate_non_empty_string(data['artist'], 'artist')
        validate_string_length(data['artist'], 'artist', 100)
        song.artist = data['artist']

    if 'status' in data:
        validate_status(data['status'])
        song.status = data['status']

    if 'difficulty' in data:
        validate_difficulty(data['difficulty'])
        song.difficulty = data['difficulty']

    if 'chords' in data:
        song.chords = data['chords']
    if 'link' in data:
        validate_string_length(data['link'], 'link', 200)
        song.link = data['link']
    if 'memo' in data:
        song.memo = data['memo']
    if 'genre' in data:
        validate_string_length(data['genre'], 'genre', 50)
        song.genre = data['genre']

    db.session.commit()
    return jsonify(song.to_dict())


@songs_bp.route('/songs/<int:id>', methods=['DELETE'])
def delete_song(id):
    song = _get_song_or_404(id)

    for media in song.media_files:
        delete_original_and_audio(media, 'media', current_app.logger)

    db.session.delete(song)
    db.session.commit()
    return jsonify({"message": "Song deleted"}), 200


def _safe_filename(filename):
    # Preserve Korean characters, letters, numbers, dots, underscores, and hyphens.
    # Replace other characters with underscore.
    return re.sub(r'[^a-zA-Z0-9가-힣._-]', '_', filename)


@songs_bp.route('/songs/<int:id>/upload', methods=['POST'])
def upload_sheet_music(id):
    song = _get_song_or_404(id)

    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    filename = generate_secure_filename(file.filename)
    content_type = guess_content_type(filename)

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    storage.upload(f'media/{filename}', file, content_type=content_type)

    media = create_media(
        song_id=id,
        filename=filename,
        original_filename=file.filename,
        file_type=detect_file_type(filename),
        file_size=file_size,
    )
    save_media_and_start(current_app._get_current_object(), media)

    song.sheet_music = filename
    db.session.commit()
    return jsonify(song.to_dict()), 200


@songs_bp.route('/songs/<int:id>/media', methods=['GET'])
def get_media_list(id):
    song = _get_song_or_404(id)
    return jsonify([media.to_dict() for media in song.media_files])


@songs_bp.route('/songs/<int:id>/media', methods=['POST'])
def add_media(id):
    song = _get_song_or_404(id)

    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    filename = generate_secure_filename(file.filename)
    content_type = guess_content_type(filename)

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    storage.upload(f'media/{filename}', file, content_type=content_type)

    rehearsal_id = request.form.get('rehearsal_id', type=int)
    if rehearsal_id:
        rehearsal = db.session.get(Rehearsal, rehearsal_id)
        if not rehearsal:
            raise ValidationError("Rehearsal not found")

    media = create_media(
        song_id=id,
        filename=filename,
        original_filename=file.filename,
        file_type=detect_file_type(filename),
        file_size=file_size,
        rehearsal_id=rehearsal_id,
    )
    save_media_and_start(current_app._get_current_object(), media)

    return jsonify(media.to_dict()), 201


@songs_bp.route('/media/<int:media_id>/rehearsal', methods=['PATCH'])
def link_media_rehearsal(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")

    data = request.get_json()
    rehearsal_id = data.get('rehearsal_id')

    if rehearsal_id is not None:
        rehearsal = db.session.get(Rehearsal, rehearsal_id)
        if not rehearsal:
            raise NotFoundError("Rehearsal not found")

    media.rehearsal_id = rehearsal_id
    db.session.commit()
    return jsonify(media.to_dict())


@songs_bp.route('/media/<int:media_id>/featured', methods=['PATCH'])
def set_featured_media(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")

    Media.query.filter_by(song_id=media.song_id, is_featured=True).update({'is_featured': False})
    media.is_featured = True
    db.session.commit()
    return jsonify(media.to_dict()), 200


@songs_bp.route('/media/<int:media_id>/rename', methods=['PUT'])
def rename_media(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")

    data = request.get_json()
    if not data:
        raise ValidationError("Request body is required")

    new_name = data.get('filename', '').strip()
    if not new_name:
        raise ValidationError("New filename is required")

    if '.' in new_name and not allowed_file(new_name):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    # If no extension provided, append the original extension
    if '.' not in new_name:
        ext = media.filename.rsplit('.', 1)[1] if '.' in media.filename else ''
        if ext:
            new_name = f"{new_name}.{ext}"

    # Allow safe filename characters
    safe_name = _safe_filename(new_name)

    # Try to preserve the ID_TIMESTAMP prefix structure
    parts = media.filename.split('_', 2)
    if len(parts) >= 3:
        prefix = f"{parts[0]}_{parts[1]}_"
    else:
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        prefix = f"{media.song_id}_{timestamp}_"

    new_filename = f"{prefix}{safe_name}"

    old_key = f'media/{media.filename}'
    new_key = f'media/{new_filename}'

    if not storage.exists(old_key):
        raise NotFoundError("Original file not found in storage")

    if storage.exists(new_key):
        raise ValidationError("File with this name already exists")

    storage.copy(old_key, new_key)
    storage.delete(old_key)

    if media.audio_filename:
        old_audio_key = f'media/{media.audio_filename}'
        new_audio_filename = f'{new_filename.rsplit(".", 1)[0]}_audio.m4a'
        new_audio_key = f'media/{new_audio_filename}'
        try:
            if storage.exists(old_audio_key):
                storage.copy(old_audio_key, new_audio_key)
                storage.delete(old_audio_key)
                media.audio_filename = new_audio_filename
        except Exception:
            current_app.logger.exception('Error renaming audio derivative for media %s', media_id)

    media.filename = new_filename
    media.original_filename = new_name
    db.session.commit()

    return jsonify(media.to_dict()), 200


@songs_bp.route('/media/<int:media_id>', methods=['DELETE'])
def delete_media(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")

    delete_original_and_audio(media, 'media', current_app.logger)

    db.session.delete(media)
    db.session.commit()
    return jsonify({"message": "Media deleted"}), 200


@songs_bp.route('/media/<int:media_id>/processing', methods=['GET'])
def get_media_processing(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")
    return jsonify(processing_status_response(media, 'media'))


@songs_bp.route('/media/<int:media_id>/retry-audio', methods=['POST'])
def retry_media_audio(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")
    try:
        retry_audio_processing(current_app._get_current_object(), media)
    except ValueError as exc:
        raise ValidationError(str(exc))
    except RuntimeError as exc:
        raise ValidationError(str(exc), status_code=409)
    return jsonify({'id': media.id, 'status': media.transcoding_status}), 202


@songs_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    """하위 호환: presigned URL로 리다이렉트"""
    url = storage.generate_url(f'media/{filename}')
    return redirect(url)
