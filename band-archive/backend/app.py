
import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from extensions import db
from models import Song

load_dotenv()

app = Flask(__name__)
CORS(app)

# Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///band_archive.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()

@app.route('/')
def home():
    return jsonify({"message": "Band Archive API is running!"})

# Song Routes (Basic CRUD)
@app.route('/songs', methods=['GET'])
def get_songs():
    songs = Song.query.all()
    return jsonify([song.to_dict() for song in songs])

@app.route('/songs', methods=['POST'])
def add_song():
    data = request.json
    new_song = Song(
        title=data['title'],
        artist=data['artist'],
        status=data.get('status', 'Practice'),
        lyrics=data.get('lyrics'),
        chords=data.get('chords'),
        link=data.get('link'),
        memo=data.get('memo')
    )
    db.session.add(new_song)
    db.session.commit()
    return jsonify(new_song.to_dict()), 201

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
