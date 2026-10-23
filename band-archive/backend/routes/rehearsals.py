from datetime import date

from flask import Blueprint, jsonify, request, current_app

from extensions import db
from models import Rehearsal, Song, Media
from errors import ValidationError, NotFoundError
from legacy_scope import (legacy_band_id, legacy_get_or_404, legacy_query,
                          reject_foreign_children, reject_legacy_destructive_write, validate_legacy_graph,
                          validate_legacy_graphs)
from storage import storage
from media_processing import create_media, save_media_and_start
from voting import voter_hash, media_viewer_votes
from upload_helpers import prepare_upload
from validators import (
    validate_required_string,
    validate_string_length,
    allowed_file,
    generate_secure_filename,
    detect_file_type,
    guess_content_type,
    ALLOWED_EXTENSIONS,
)
from legacy_read_visibility import legacy_item_visible, legacy_read_scope
from v2_content_serializers import media_visibility, song_visibility
from v2_content_serializers import rehearsal_visibility
from voc_validation import validate_rehearsal_type

rehearsals_bp = Blueprint('rehearsals', __name__)


def _legacy_band_id():
    return legacy_band_id()


def _get_legacy_rehearsal_or_404(rehearsal_id):
    return legacy_get_or_404(Rehearsal, rehearsal_id, 'Rehearsal not found')


def _visible_rehearsal_payload(scope, rehearsal):
    songs = [song for song in rehearsal.songs if song.band_id == rehearsal.band_id
             and legacy_item_visible(scope, song_visibility(scope, song))]
    from deletion_service import is_pending_deletion
    media = [item for item in rehearsal.media_files if item.band_id == rehearsal.band_id
             and legacy_item_visible(scope, media_visibility(scope, item))
             and not is_pending_deletion('media', item.id, item.band_id)]
    return rehearsal.to_dict(song_items=songs, media_items=media)


def _visible_rehearsal_or_404(rehearsal_id):
    rehearsal = _get_legacy_rehearsal_or_404(rehearsal_id)
    validate_legacy_graph(rehearsal, 'Rehearsal not found')
    scope = legacy_read_scope(rehearsal.band_id)
    if not legacy_item_visible(scope, rehearsal_visibility(scope, rehearsal)):
        raise NotFoundError('Rehearsal not found')
    return rehearsal, scope


def _parse_date(value, field_name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{field_name} must be a valid date (YYYY-MM-DD)")


def _songs_in_rehearsal_band_or_error(band_id, song_ids):
    """Resolve all relation IDs inside the parent tenant or reject atomically."""
    if not isinstance(song_ids, list) or any(not isinstance(song_id, int) for song_id in song_ids):
        raise ValidationError('song_ids must be an array of integer IDs')
    wanted = set(song_ids)
    songs = Song.query.filter(Song.band_id == band_id, Song.id.in_(wanted)).all() if wanted else []
    if len(songs) != len(wanted):
        # Do not distinguish a nonexistent ID from a cross-band ID.
        raise ValidationError('One or more songs are unavailable for this rehearsal', 404)
    for song in songs:
        validate_legacy_graph(song, 'One or more songs are unavailable for this rehearsal')
    return songs


@rehearsals_bp.route('/rehearsals', methods=['GET'])
def get_rehearsals():
    query = legacy_query(Rehearsal)

    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    if year and month:
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)

        query = query.filter(
            # 단일 날짜가 해당 월에 속하거나
            (Rehearsal.date >= start) & (Rehearsal.date < end) |
            # 기간 일정이 해당 월과 겹치는 경우
            (Rehearsal.start_date.isnot(None)) & (Rehearsal.end_date.isnot(None)) &
            (Rehearsal.start_date < end) & (Rehearsal.end_date >= start)
        )

    rehearsals = query.order_by(Rehearsal.date.asc()).all()
    validate_legacy_graphs(rehearsals, 'Rehearsal not found')
    scope = legacy_read_scope(rehearsals[0].band_id if rehearsals else _legacy_band_id())
    rehearsals = [rehearsal for rehearsal in rehearsals if legacy_item_visible(
        scope, rehearsal_visibility(scope, rehearsal),
    )]
    return jsonify([_visible_rehearsal_payload(scope, item) for item in rehearsals])


@rehearsals_bp.route('/rehearsals/<int:id>', methods=['GET'])
def get_rehearsal(id):
    rehearsal, scope = _visible_rehearsal_or_404(id)
    return jsonify(_visible_rehearsal_payload(scope, rehearsal))


@rehearsals_bp.route('/rehearsals', methods=['POST'])
def create_rehearsal():
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    validate_required_string(data.get('title'), 'title')
    validate_string_length(data.get('title'), 'title', 200)

    rehearsal_date = _parse_date(data.get('date'), 'date')
    if not rehearsal_date:
        raise ValidationError("date is required")

    start_date = _parse_date(data.get('start_date'), 'start_date')
    end_date = _parse_date(data.get('end_date'), 'end_date')

    if start_date and end_date and start_date > end_date:
        raise ValidationError("start_date must be before or equal to end_date")

    location = data.get('location')
    if location:
        validate_string_length(location, 'location', 200)

    legacy_band_id = _legacy_band_id()
    song_ids = data.get('song_ids', [])
    # Validate every relation before the new rehearsal is flushed, so a
    # cross-band ID cannot mutate even the current transaction.
    songs = _songs_in_rehearsal_band_or_error(legacy_band_id, song_ids) if song_ids else []

    rehearsal = Rehearsal(
        band_id=legacy_band_id,
        title=data['title'],
        date=rehearsal_date,
        start_date=start_date,
        end_date=end_date,
        time=data.get('time'),
        location=location,
        latitude=data.get('latitude'),
        longitude=data.get('longitude'),
        memo=data.get('memo'),
        color=data.get('color', '#ffd32a'),
    )
    if 'rehearsal_type' in data:
        rehearsal.rehearsal_type = validate_rehearsal_type(data['rehearsal_type'])

    db.session.add(rehearsal)
    if song_ids:
        rehearsal.songs = songs

    db.session.commit()
    return jsonify(rehearsal.to_dict()), 201


@rehearsals_bp.route('/rehearsals/<int:id>', methods=['PUT'])
def update_rehearsal(id):
    rehearsal = _get_legacy_rehearsal_or_404(id)

    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    if 'title' in data:
        validate_required_string(data['title'], 'title')
        validate_string_length(data['title'], 'title', 200)
        rehearsal.title = data['title']

    if 'date' in data:
        rehearsal_date = _parse_date(data['date'], 'date')
        if not rehearsal_date:
            raise ValidationError("date cannot be empty")
        rehearsal.date = rehearsal_date

    if 'start_date' in data:
        rehearsal.start_date = _parse_date(data['start_date'], 'start_date')

    if 'end_date' in data:
        rehearsal.end_date = _parse_date(data['end_date'], 'end_date')

    if rehearsal.start_date and rehearsal.end_date and rehearsal.start_date > rehearsal.end_date:
        raise ValidationError("start_date must be before or equal to end_date")

    if 'time' in data:
        rehearsal.time = data['time']
    if 'location' in data:
        if data['location']:
            validate_string_length(data['location'], 'location', 200)
        rehearsal.location = data['location']
    if 'latitude' in data:
        rehearsal.latitude = data['latitude']
    if 'longitude' in data:
        rehearsal.longitude = data['longitude']
    if 'memo' in data:
        rehearsal.memo = data['memo']
    if 'color' in data:
        rehearsal.color = data['color']
    if 'rehearsal_type' in data:
        rehearsal.rehearsal_type = validate_rehearsal_type(data['rehearsal_type'])

    if 'song_ids' in data:
        rehearsal.songs = _songs_in_rehearsal_band_or_error(rehearsal.band_id, data['song_ids'])

    db.session.commit()
    return jsonify(rehearsal.to_dict())


@rehearsals_bp.route('/rehearsals/<int:id>', methods=['DELETE'])
def delete_rehearsal(id):
    reject_legacy_destructive_write()
    rehearsal = _get_legacy_rehearsal_or_404(id)
    reject_foreign_children(rehearsal, Media, 'rehearsal_id', 'Rehearsal not found')
    if any(song.band_id != rehearsal.band_id for song in rehearsal.songs):
        raise NotFoundError('Rehearsal not found')
    db.session.delete(rehearsal)
    db.session.commit()
    return jsonify({"message": "Rehearsal deleted"}), 200



@rehearsals_bp.route('/rehearsals/<int:id>/media', methods=['GET'])
def get_rehearsal_media(id):
    rehearsal, scope = _visible_rehearsal_or_404(id)
    media_files = legacy_query(Media).filter(Media.rehearsal_id == rehearsal.id).order_by(
        Media.vote_score.desc(), Media.id.asc(),
    ).all()
    validate_legacy_graphs(media_files, 'Media not found')
    from deletion_service import is_pending_deletion
    media_files = [media for media in media_files if media.band_id == rehearsal.band_id
                   and legacy_item_visible(scope, media_visibility(scope, media))
                   and not is_pending_deletion('media', media.id, media.band_id)]
    viewer_votes = media_viewer_votes(media_files, voter_hash())
    return jsonify([media.to_dict(viewer_vote=viewer_votes.get(media.id, 0)) for media in media_files])


@rehearsals_bp.route('/rehearsals/<int:id>/media', methods=['POST'])
def upload_rehearsal_media(id):
    rehearsal = _get_legacy_rehearsal_or_404(id)

    song_id = request.form.get('song_id', type=int)
    if not song_id:
        raise ValidationError("song_id is required")
    song = Song.query.filter(Song.id == song_id, Song.band_id == rehearsal.band_id).one_or_none()
    if not song:
        raise NotFoundError("Song not found")
    validate_legacy_graph(song, 'Song not found')

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
        song_id=song_id,
        rehearsal_id=id,
        filename=upload.filename,
        original_filename=upload.original_filename,
        file_type=detect_file_type(upload.filename),
        file_size=upload.file_size,
    )
    save_media_and_start(current_app._get_current_object(), media)
    return jsonify(media.to_dict()), 201
