
from datetime import datetime, timezone
from extensions import db


class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    artist = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='Practice')  # Practice, Completed, OnHold
    chords = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(200), nullable=True)
    memo = db.Column(db.Text, nullable=True)
    genre = db.Column(db.String(50), nullable=True)
    difficulty = db.Column(db.Integer, default=3)
    sheet_music = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'artist': self.artist,
            'status': self.status,
            'chords': self.chords,
            'link': self.link,
            'memo': self.memo,
            'genre': self.genre,
            'difficulty': self.difficulty,
            'sheet_music': self.sheet_music,
            'media': [media.to_dict() for media in self.media_files],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Media(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    original_filename = db.Column(db.String(200), nullable=True)
    file_type = db.Column(db.String(20), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    song = db.relationship('Song', backref=db.backref('media_files', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'song_id': self.song_id,
            'filename': self.original_filename or self.filename,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'url': f'/uploads/{self.filename}',
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class SongSuggestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    artist = db.Column(db.String(100), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    memo = db.Column(db.Text, nullable=True)
    thumbs_up = db.Column(db.Integer, default=0)
    thumbs_down = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'artist': self.artist,
            'link': self.link,
            'memo': self.memo,
            'thumbs_up': self.thumbs_up,
            'thumbs_down': self.thumbs_down,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    instrument = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'instrument': self.instrument,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PersonalLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    original_filename = db.Column(db.String(200), nullable=True)
    file_type = db.Column(db.String(20), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    member = db.relationship('Member', backref=db.backref('personal_logs', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'member_id': self.member_id,
            'member_name': self.member.name if self.member else None,
            'title': self.title,
            'filename': self.original_filename or self.filename,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'url': f'/uploads/personal_logs/{self.filename}',
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PracticeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.id'), nullable=False)
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    content = db.Column(db.Text, nullable=True)
    feedback = db.Column(db.Text, nullable=True)
    recording = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    song = db.relationship('Song', backref=db.backref('practice_logs', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'song_id': self.song_id,
            'song_title': self.song.title if self.song else None,
            'date': self.date.isoformat() if self.date else None,
            'content': self.content,
            'feedback': self.feedback,
            'recording': self.recording,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


rehearsal_songs = db.Table('rehearsal_songs',
    db.Column('rehearsal_id', db.Integer, db.ForeignKey('rehearsal.id'), primary_key=True),
    db.Column('song_id', db.Integer, db.ForeignKey('song.id'), primary_key=True)
)


class Rehearsal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    time = db.Column(db.String(20), nullable=True)
    location = db.Column(db.String(200), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    memo = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(7), default='#ffd32a')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    songs = db.relationship('Song', secondary=rehearsal_songs, backref='rehearsals', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'date': self.date.isoformat() if self.date else None,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'time': self.time,
            'location': self.location,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'memo': self.memo,
            'color': self.color,
            'songs': [{'id': s.id, 'title': s.title, 'artist': s.artist} for s in self.songs],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
