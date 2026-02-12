import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_from_directory, current_app
from werkzeug.utils import secure_filename

from extensions import db
from models import Song, Media
from errors import ValidationError, NotFoundError
from validators import (
    validate_status,
    validate_difficulty,
    validate_required_string,
    validate_non_empty_string,
    validate_string_length,
    allowed_file,
    generate_secure_filename,
    ALLOWED_EXTENSIONS,
)

songs_bp = Blueprint('songs', __name__)


def _get_song_or_404(id):
    song = db.session.get(Song, id)
    if not song:
        raise NotFoundError()
    return song


def _detect_file_type(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext in ('mp4', 'webm', 'mov', 'avi', 'mkv'):
        return 'video'
    if ext in ('mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'):
        return 'audio'
    if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return 'image'
    return 'document'


@songs_bp.route('/')
def home():
    return jsonify({"message": "Band Archive API is running!"})


@songs_bp.route('/songs', methods=['GET'])
def get_songs():
    try:
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

        songs = query.all()
        return jsonify([song.to_dict() for song in songs])
    except Exception as e:
        current_app.logger.error(f"Error fetching songs: {str(e)}")
        return jsonify({"error": str(e), "message": "Internal Server Error in get_songs"}), 500


@songs_bp.route('/songs/<int:id>', methods=['GET'])
def get_song(id):
    song = _get_song_or_404(id)
    return jsonify(song.to_dict())


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
        lyrics=data.get('lyrics'),
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

    if 'lyrics' in data:
        song.lyrics = data['lyrics']
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
    db.session.delete(song)
    db.session.commit()
    return jsonify({"message": "Song deleted"}), 200


import re

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
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)
    os.chmod(file_path, 0o644)

    media = Media(
        song_id=id,
        filename=filename,
        original_filename=file.filename,
        file_type=_detect_file_type(filename),
        file_size=os.path.getsize(file_path),
    )
    db.session.add(media)

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
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)
    os.chmod(file_path, 0o644)

    media = Media(
        song_id=id,
        filename=filename,
        original_filename=file.filename,
        file_type=_detect_file_type(filename),
        file_size=os.path.getsize(file_path),
    )
    db.session.add(media)
    db.session.commit()
    return jsonify(media.to_dict()), 201


@songs_bp.route('/media/<int:media_id>/rename', methods=['PUT'])
def rename_media(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")

    new_name = request.json.get('filename')
    if not new_name:
        raise ValidationError("New filename is required")

    if not allowed_file(new_name) and '.' in new_name:
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
        # Should not happen typically, but fallback
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        prefix = f"{media.song_id}_{timestamp}_"
        
    new_filename = f"{prefix}{safe_name}"
    
    old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], media.filename)
    new_path = os.path.join(current_app.config['UPLOAD_FOLDER'], new_filename)
    
    if os.path.exists(new_path):
        raise ValidationError("File with this name already exists")
        
    if os.path.exists(old_path):
        os.rename(old_path, new_path)
        
    media.filename = new_filename
    db.session.commit()
    
    return jsonify(media.to_dict()), 200


@songs_bp.route('/media/<int:media_id>', methods=['DELETE'])
def delete_media(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")

    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], media.filename)
    if os.path.exists(file_path):
        os.remove(file_path)

    db.session.delete(media)
    db.session.commit()
    return jsonify({"message": "Media deleted"}), 200


@songs_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    response = send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
    # Explicitly set MIME type for .m4a files for browser compatibility
    if filename.lower().endswith('.m4a'):
        response.headers['Content-Type'] = 'audio/mp4'
    return response
