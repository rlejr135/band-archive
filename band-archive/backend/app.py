
import os
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_migrate import Migrate
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from extensions import db
from models import Song

load_dotenv()

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
VALID_STATUSES = {'Practice', 'Completed', 'OnHold'}

app = Flask(__name__)
CORS(app)

# Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///band_archive.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Upload Configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

db.init_app(app)
migrate = Migrate(app, db)

with app.app_context():
    db.create_all()
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def home():
    return jsonify({"message": "Band Archive API is running!"})


# Song Routes
@app.route('/songs', methods=['GET'])
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


@app.route('/songs/<int:id>', methods=['GET'])
def get_song(id):
    song = db.session.get(Song, id)
    if not song:
        return jsonify({"error": "Song not found"}), 404
    return jsonify(song.to_dict())


@app.route('/songs', methods=['POST'])
def add_song():
    data = request.json
    if not data:
        return jsonify({"error": "Request body is required"}), 400
    if not data.get('title'):
        return jsonify({"error": "title is required"}), 400
    if not data.get('artist'):
        return jsonify({"error": "artist is required"}), 400

    status = data.get('status', 'Practice')
    if status not in VALID_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}"}), 400

    difficulty = data.get('difficulty', 3)
    if not isinstance(difficulty, int) or difficulty < 1 or difficulty > 5:
        return jsonify({"error": "difficulty must be an integer between 1 and 5"}), 400

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


@app.route('/songs/<int:id>', methods=['PUT'])
def update_song(id):
    song = db.session.get(Song, id)
    if not song:
        return jsonify({"error": "Song not found"}), 404

    data = request.json
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    if 'title' in data:
        if not data['title']:
            return jsonify({"error": "title cannot be empty"}), 400
        song.title = data['title']

    if 'artist' in data:
        if not data['artist']:
            return jsonify({"error": "artist cannot be empty"}), 400
        song.artist = data['artist']

    if 'status' in data:
        if data['status'] not in VALID_STATUSES:
            return jsonify({"error": f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}"}), 400
        song.status = data['status']

    if 'difficulty' in data:
        d = data['difficulty']
        if not isinstance(d, int) or d < 1 or d > 5:
            return jsonify({"error": "difficulty must be an integer between 1 and 5"}), 400
        song.difficulty = d

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


@app.route('/songs/<int:id>', methods=['DELETE'])
def delete_song(id):
    song = db.session.get(Song, id)
    if not song:
        return jsonify({"error": "Song not found"}), 404
    db.session.delete(song)
    db.session.commit()
    return jsonify({"message": "Song deleted"}), 200


@app.route('/songs/<int:id>/upload', methods=['POST'])
def upload_sheet_music(id):
    song = db.session.get(Song, id)
    if not song:
        return jsonify({"error": "Song not found"}), 404

    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    filename = secure_filename(file.filename)
    # Prefix with song id to avoid collisions
    filename = f"{id}_{filename}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

    song.sheet_music = filename
    db.session.commit()
    return jsonify(song.to_dict()), 200


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
