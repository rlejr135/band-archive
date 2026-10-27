import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, redirect, current_app
from werkzeug.utils import secure_filename

from extensions import db
from models import Song, SongVote, Media, MediaVote, Rehearsal
from errors import ValidationError, NotFoundError
from legacy_scope import (legacy_band_id, legacy_get_or_404, legacy_query,
                          reject_foreign_children, reject_legacy_destructive_write, validate_legacy_graph,
                          validate_legacy_graphs)
from storage import storage
from media_processing import (create_media, save_media_and_start, retry_audio_processing,
                              delete_original_and_audio, processing_status_response)
from media_sources import select_media_source_keys
from voting import voter_hash, media_viewer_votes, parse_vote_payload
from vote_service import apply_vote
from upload_helpers import prepare_upload
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
from legacy_read_visibility import legacy_item_visible, legacy_read_scope
from content_visibility import media_visibility, rehearsal_visibility, song_visibility

songs_bp = Blueprint('songs', __name__)


def _legacy_band_id():
    return legacy_band_id()


def _get_legacy_song_or_404(song_id):
    return legacy_get_or_404(Song, song_id, 'Song not found')


def _get_legacy_media_or_404(media_id):
    return legacy_get_or_404(Media, media_id, 'Media not found')


def _song_with_media_viewer_votes(song, voter_hash=None):
    scope = legacy_read_scope(song.band_id)
    if not _visible_song(scope, song):
        raise NotFoundError('Song not found')
    return _legacy_song_payload(scope, song, voter_hash)


def _visible_song(scope, song):
    return legacy_item_visible(scope, song_visibility(scope, song))


def _legacy_song_payload(scope, song, vote_hash=None, viewer_votes=None):
    visible_media = []
    for media in song.media_files:
        if media.band_id != song.band_id:
            continue
        if legacy_item_visible(scope, media_visibility(scope, media)):
            from deletion_service import is_pending_deletion
            if not is_pending_deletion('media', media.id, media.band_id):
                visible_media.append(media)
    visible_rehearsals = [
        rehearsal for rehearsal in song.rehearsals
        if rehearsal.band_id == song.band_id
        and legacy_item_visible(scope, rehearsal_visibility(scope, rehearsal))
    ]
    votes = viewer_votes if viewer_votes is not None else media_viewer_votes(visible_media, vote_hash)
    return song.to_dict(media_viewer_votes=votes, media_items=visible_media,
                        rehearsal_items=visible_rehearsals)


def _legacy_song_or_404(song_id):
    song = _get_legacy_song_or_404(song_id)
    validate_legacy_graph(song, 'Song not found')
    scope = legacy_read_scope(song.band_id)
    if not _visible_song(scope, song):
        raise NotFoundError('Song not found')
    return song, scope



@songs_bp.route('/')
def home():
    return jsonify({"message": "Band Archive API is running!"})


@songs_bp.route('/songs', methods=['GET'])
def get_songs():
    voter_hash_value = voter_hash()
    query = legacy_query(Song)

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

    # Restore the pre-song-vote list behavior: no vote-score ordering.
    songs = query.order_by(Song.id.asc()).all()
    validate_legacy_graphs(songs, 'Song not found')
    scope = legacy_read_scope(songs[0].band_id if songs else _legacy_band_id())
    songs = [song for song in songs if _visible_song(scope, song)]
    all_visible_media = [media for song in songs for media in _visible_media_for_song(scope, song)]
    viewer_votes = media_viewer_votes(all_visible_media, voter_hash_value)
    return jsonify([_legacy_song_payload(scope, song, voter_hash_value, viewer_votes) for song in songs])


def _visible_media_for_song(scope, song):
    from deletion_service import is_pending_deletion
    return [media for media in song.media_files if media.band_id == song.band_id
            and legacy_item_visible(scope, media_visibility(scope, media))
            and not is_pending_deletion('media', media.id, media.band_id)]


@songs_bp.route('/songs/<int:id>', methods=['GET'])
def get_song(id):
    song, scope = _legacy_song_or_404(id)
    return jsonify(_legacy_song_payload(scope, song, voter_hash()))


@songs_bp.route('/songs/<int:id>/vote', methods=['PATCH'])
def vote_song(id):
    """Legacy compatibility endpoint; new clients must vote on Media instead.

    Historic SongVote rows/counters intentionally remain untouched because a
    song vote cannot be losslessly assigned to one of its media items.
    """
    voter_hash_value = voter_hash(required=True)
    value, expected_value = parse_vote_payload(request.get_json(silent=True))

    result = apply_vote(
        item_model=Song, vote_model=SongVote, foreign_key='song_id', item_id=id,
        voter_hash_value=voter_hash_value, value=value, expected_value=expected_value,
        band_id_resolver=_legacy_band_id,
        conflict_snapshot=lambda song, _previous_value: song.to_dict(),
    )
    if result.is_conflict:
        return jsonify({
            'error': 'vote_conflict',
            'code': 'vote_conflict',
            'song': result.conflict_snapshot,
        }), 409
    return jsonify(result.item.to_dict()), 200


@songs_bp.route('/media/<int:media_id>/vote', methods=['PATCH'])
def vote_media(media_id):
    voter_hash_value = voter_hash(required=True)
    value, expected_value = parse_vote_payload(request.get_json(silent=True))

    result = apply_vote(
        item_model=Media, vote_model=MediaVote, foreign_key='media_id', item_id=media_id,
        voter_hash_value=voter_hash_value, value=value, expected_value=expected_value,
        band_id_resolver=_legacy_band_id,
        not_found_message='Media not found',
        conflict_snapshot=lambda media, previous_value: media.to_dict(viewer_vote=previous_value),
    )
    if result.is_conflict:
        return jsonify({
            'error': 'vote_conflict',
            'code': 'vote_conflict',
            'media': result.conflict_snapshot,
        }), 409
    return jsonify(result.item.to_dict(viewer_vote=value)), 200


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
        band_id=_legacy_band_id(),
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
    song = _get_legacy_song_or_404(id)

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
    return jsonify(_song_with_media_viewer_votes(song, voter_hash()))


@songs_bp.route('/songs/<int:id>', methods=['DELETE'])
def delete_song(id):
    reject_legacy_destructive_write()
    song = _get_legacy_song_or_404(id)
    reject_foreign_children(song, Media, 'song_id', 'Song not found')

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
    song = _get_legacy_song_or_404(id)

    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    upload = prepare_upload(file, generate_secure_filename(file.filename),
                            guess_content_type(file.filename))
    storage.upload(f'media/{upload.filename}', file, content_type=upload.content_type)

    media = create_media(
        song_id=id,
        filename=upload.filename,
        original_filename=upload.original_filename,
        file_type=detect_file_type(upload.filename),
        file_size=upload.file_size,
    )
    save_media_and_start(current_app._get_current_object(), media)

    song.sheet_music = upload.filename
    db.session.commit()
    return jsonify(song.to_dict()), 200


@songs_bp.route('/songs/<int:id>/media', methods=['GET'])
def get_media_list(id):
    song, scope = _legacy_song_or_404(id)
    voter_hash_value = voter_hash()
    media_files = legacy_query(Media).filter(Media.song_id == id).order_by(
        Media.vote_score.desc(), Media.id.asc(),
    ).all()
    validate_legacy_graphs(media_files, 'Media not found')
    from deletion_service import is_pending_deletion
    media_files = [media for media in media_files if media.band_id == song.band_id
                   and legacy_item_visible(scope, media_visibility(scope, media))
                   and not is_pending_deletion('media', media.id, media.band_id)]
    viewer_votes = media_viewer_votes(media_files, voter_hash_value)
    return jsonify([media.to_dict(viewer_vote=viewer_votes.get(media.id, 0)) for media in media_files])


@songs_bp.route('/songs/<int:id>/media', methods=['POST'])
def add_media(id):
    song = _get_legacy_song_or_404(id)

    # Resolve the optional relation before reading or uploading the file.  A
    # cross-band rehearsal ID is intentionally indistinguishable from a
    # missing one and must not leave an orphan object in storage.
    rehearsal_id = request.form.get('rehearsal_id', type=int)
    if rehearsal_id is not None:
        rehearsal = Rehearsal.query.filter(
            Rehearsal.id == rehearsal_id, Rehearsal.band_id == song.band_id,
        ).one_or_none()
        if rehearsal is None:
            raise NotFoundError("Rehearsal not found")
        validate_legacy_graph(rehearsal, 'Rehearsal not found')

    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    upload = prepare_upload(file, generate_secure_filename(file.filename),
                            guess_content_type(file.filename))
    storage.upload(f'media/{upload.filename}', file, content_type=upload.content_type)

    media = create_media(
        song_id=id,
        filename=upload.filename,
        original_filename=upload.original_filename,
        file_type=detect_file_type(upload.filename),
        file_size=upload.file_size,
        rehearsal_id=rehearsal_id,
    )
    save_media_and_start(current_app._get_current_object(), media)

    return jsonify(media.to_dict()), 201


@songs_bp.route('/media/<int:media_id>/rehearsal', methods=['PATCH'])
def link_media_rehearsal(media_id):
    media = _get_legacy_media_or_404(media_id)

    data = request.get_json()
    rehearsal_id = data.get('rehearsal_id')

    if rehearsal_id is not None:
        rehearsal = Rehearsal.query.filter(
            Rehearsal.id == rehearsal_id, Rehearsal.band_id == media.band_id,
        ).one_or_none()
        if not rehearsal:
            raise NotFoundError("Rehearsal not found")
        validate_legacy_graph(rehearsal, 'Rehearsal not found')

    media.rehearsal_id = rehearsal_id
    db.session.commit()
    return jsonify(media.to_dict())


@songs_bp.route('/media/<int:media_id>/featured', methods=['PATCH'])
def set_featured_media(media_id):
    reject_legacy_destructive_write()


@songs_bp.route('/media/<int:media_id>/rename', methods=['PUT'])
def rename_media(media_id):
    reject_legacy_destructive_write()
    media = _get_legacy_media_or_404(media_id)

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
    reject_legacy_destructive_write()
    media = _get_legacy_media_or_404(media_id)
    from models import Comment
    reject_foreign_children(media, Comment, 'media_id', 'Media not found')

    delete_original_and_audio(media, 'media', current_app.logger)

    db.session.delete(media)
    db.session.commit()
    return jsonify({"message": "Media deleted"}), 200


@songs_bp.route('/media/<int:media_id>/processing', methods=['GET'])
def get_media_processing(media_id):
    media = _get_legacy_media_or_404(media_id)
    validate_legacy_graph(media, 'Media not found')
    scope = legacy_read_scope(media.band_id)
    if not legacy_item_visible(scope, media_visibility(scope, media)):
        raise NotFoundError('Media not found')
    from deletion_service import is_pending_deletion
    if is_pending_deletion('media', media.id, media.band_id):
        raise NotFoundError('Media not found')
    return jsonify(processing_status_response(media, 'media'))


@songs_bp.route('/media/<int:media_id>/retry-audio', methods=['POST'])
def retry_media_audio(media_id):
    media = _get_legacy_media_or_404(media_id)
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
    requested_key = f'media/{filename}'
    # A filename is never a storage capability.  It must first resolve to a
    # real legacy-scoped record whose currently allowed original source is the
    # exact legacy key.  This also rejects pruned originals without signing.
    matching_records = legacy_query(Media).filter(Media.filename == filename).all()
    from deletion_service import is_pending_deletion
    validate_legacy_graphs(matching_records, 'Media source not found')
    matching_records = [record for record in matching_records if not is_pending_deletion(
        'media', record.id, record.band_id,
    ) and legacy_item_visible(
        legacy_read_scope(record.band_id),
        media_visibility(legacy_read_scope(record.band_id), record),
    )]
    if not any(select_media_source_keys(media, 'media')['original'] == requested_key for media in matching_records):
        raise NotFoundError("Media source not found")
    url = storage.generate_url(requested_key)
    return redirect(url)
