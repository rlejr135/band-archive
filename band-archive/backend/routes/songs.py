import os

from flask import Blueprint, jsonify, request, send_from_directory, current_app
from werkzeug.utils import secure_filename

from extensions import db
from models import Song
from errors import ValidationError, NotFoundError
from validators import (
    validate_status,
    validate_difficulty,
    validate_required_string,
    validate_non_empty_string,
    allowed_file,
    ALLOWED_EXTENSIONS,
)

songs_bp = Blueprint('songs', __name__)


def _get_song_or_404(id):
    song = db.session.get(Song, id)
    if not song:
        raise NotFoundError()
    return song


@songs_bp.route('/')
def home():
    return jsonify({"message": "Band Archive API is running!"})


@songs_bp.route('/songs', methods=['GET'])
def get_songs():
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
    validate_required_string(data.get('artist'), 'artist')

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
        song.title = data['title']

    if 'artist' in data:
        validate_non_empty_string(data['artist'], 'artist')
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
        song.link = data['link']
    if 'memo' in data:
        song.memo = data['memo']
    if 'genre' in data:
        song.genre = data['genre']

    db.session.commit()
    return jsonify(song.to_dict())


@songs_bp.route('/songs/<int:id>', methods=['DELETE'])
def delete_song(id):
    song = _get_song_or_404(id)
    db.session.delete(song)
    db.session.commit()
    return jsonify({"message": "Song deleted"}), 200


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

    filename = secure_filename(file.filename)
    filename = f"{id}_{filename}"
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))

    song.sheet_music = filename
    db.session.commit()
    return jsonify(song.to_dict()), 200


@songs_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
