
from datetime import datetime, timedelta, timezone
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
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
    upvote_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    downvote_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    vote_score = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    votes = db.relationship('SongVote', backref='song', lazy=True,
                            cascade='all, delete-orphan')

    def to_dict(self, viewer_vote=0):
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
            'upvote_count': self.upvote_count,
            'downvote_count': self.downvote_count,
            'vote_score': self.vote_score,
            'viewer_vote': viewer_vote,
            'has_featured_media': any(m.is_featured for m in self.media_files),
            'media': [media.to_dict() for media in self.media_files],
            'rehearsals': [{'id': r.id, 'title': r.title, 'date': r.date.isoformat()} for r in self.rehearsals],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class SongVote(db.Model):
    """One privacy-preserving vote per song and browser/device identity."""
    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.id', ondelete='CASCADE'), nullable=False, index=True)
    voter_hash = db.Column(db.String(64), nullable=False)
    value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('song_id', 'voter_hash', name='uq_song_vote_voter'),
        db.CheckConstraint('value IN (-1, 1)', name='ck_song_vote_value'),
    )


class Media(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.id'), nullable=False)
    rehearsal_id = db.Column(db.Integer, db.ForeignKey('rehearsal.id'), nullable=True)
    filename = db.Column(db.String(200), nullable=False)
    original_filename = db.Column(db.String(200), nullable=True)
    file_type = db.Column(db.String(20), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    # Video: queued -> processing -> completed/failed. Other media: not_required.
    transcoding_status = db.Column(db.String(20), default='not_required', nullable=False)
    audio_filename = db.Column(db.String(200), nullable=True)
    processing_error = db.Column(db.String(500), nullable=True)
    processing_started_at = db.Column(db.DateTime, nullable=True)
    processing_completed_at = db.Column(db.DateTime, nullable=True)
    processing_attempts = db.Column(db.Integer, default=0, nullable=False)
    processing_heartbeat_at = db.Column(db.DateTime, nullable=True)
    is_featured = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    song = db.relationship('Song', backref=db.backref('media_files', lazy=True, cascade='all, delete-orphan'))
    rehearsal = db.relationship('Rehearsal', backref=db.backref('media_files', lazy=True))
    comments = db.relationship('Comment', backref='media', lazy=True, cascade='all, delete-orphan',
                               foreign_keys='Comment.media_id')

    def to_dict(self):
        from media_processing import processing_fields
        fields = processing_fields(self, 'media')
        qualities = {'original': fields['url']}
        if fields['audio_url']:
            qualities['audio'] = fields['audio_url']
        
        return {
            'id': self.id,
            'song_id': self.song_id,
            'rehearsal_id': self.rehearsal_id,
            'rehearsal_title': self.rehearsal.title if self.rehearsal else None,
            'rehearsal_date': self.rehearsal.date.isoformat() if self.rehearsal and self.rehearsal.date else None,
            'song_title': self.song.title if self.song else None,
            'song_artist': self.song.artist if self.song else None,
            'filename': self.original_filename or self.filename,
            'file_type': self.file_type,
            'file_size': self.file_size,
            **fields,
            'qualities': qualities,
            'is_featured': self.is_featured,
            'comment_count': len(self.comments),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class MultipartUploadSession(db.Model):
    """Server-owned authorization boundary for one R2 multipart media upload."""
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    r2_upload_id = db.Column(db.String(200), nullable=False)
    object_key = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(200), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    declared_bytes = db.Column(db.BigInteger, nullable=False)
    song_id = db.Column(db.Integer, db.ForeignKey('song.id'), nullable=False)
    rehearsal_id = db.Column(db.Integer, db.ForeignKey('rehearsal.id'), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='initiated')
    # The raw capability is returned only once by initiate.  Session IDs are
    # deliberately not sufficient to read or mutate an upload.
    capability_token_hash = db.Column(db.String(255), nullable=True)
    media_id = db.Column(db.Integer, db.ForeignKey('media.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc) + timedelta(hours=24), nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    completion_started_at = db.Column(db.DateTime, nullable=True)

    parts = db.relationship('MultipartUploadPart', backref='session', lazy=True,
                            cascade='all, delete-orphan', order_by='MultipartUploadPart.part_number')


class MultipartUploadPart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), db.ForeignKey('multipart_upload_session.id'), nullable=False)
    part_number = db.Column(db.Integer, nullable=False)
    etag = db.Column(db.String(500), nullable=True)
    uploaded_bytes = db.Column(db.BigInteger, nullable=True)
    checksum = db.Column(db.String(200), nullable=True)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (db.UniqueConstraint('session_id', 'part_number', name='uq_multipart_session_part'),)


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
    transcoding_status = db.Column(db.String(20), default='not_required', nullable=False)
    audio_filename = db.Column(db.String(200), nullable=True)
    processing_error = db.Column(db.String(500), nullable=True)
    processing_started_at = db.Column(db.DateTime, nullable=True)
    processing_completed_at = db.Column(db.DateTime, nullable=True)
    processing_attempts = db.Column(db.Integer, default=0, nullable=False)
    processing_heartbeat_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    member = db.relationship('Member', backref=db.backref('personal_logs', lazy=True, cascade='all, delete-orphan'))
    comments = db.relationship('Comment', backref='personal_log', lazy=True, cascade='all, delete-orphan',
                               foreign_keys='Comment.personal_log_id')

    def to_dict(self):
        from media_processing import processing_fields
        fields = processing_fields(self, 'personal_logs')
        return {
            'id': self.id,
            'member_id': self.member_id,
            'member_name': self.member.name if self.member else None,
            'title': self.title,
            'filename': self.original_filename or self.filename,
            'file_type': self.file_type,
            'file_size': self.file_size,
            **fields,
            'comment_count': len(self.comments),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PersonalLogMultipartUploadSession(db.Model):
    """Server-owned multipart session for a member's personal log."""
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    r2_upload_id = db.Column(db.String(200), nullable=False)
    object_key = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    declared_bytes = db.Column(db.BigInteger, nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='initiated')
    capability_token_hash = db.Column(db.String(255), nullable=True)
    personal_log_id = db.Column(db.Integer, db.ForeignKey('personal_log.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc) + timedelta(hours=24), nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    completion_started_at = db.Column(db.DateTime, nullable=True)
    parts = db.relationship('PersonalLogMultipartUploadPart', backref='session', lazy=True,
                            cascade='all, delete-orphan', order_by='PersonalLogMultipartUploadPart.part_number')


class PersonalLogMultipartUploadPart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), db.ForeignKey('personal_log_multipart_upload_session.id'), nullable=False)
    part_number = db.Column(db.Integer, nullable=False)
    etag = db.Column(db.String(500), nullable=True)
    uploaded_bytes = db.Column(db.BigInteger, nullable=True)
    checksum = db.Column(db.String(200), nullable=True)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    __table_args__ = (db.UniqueConstraint('session_id', 'part_number', name='uq_personal_log_multipart_session_part'),)


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
            'media_count': len(self.media_files),
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


class GalleryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    original_filename = db.Column(db.String(200), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    is_featured = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        from storage import storage
        return {
            'id': self.id,
            'filename': self.original_filename or self.filename,
            'file_size': self.file_size,
            'is_featured': self.is_featured,
            'url': storage.generate_url(f'gallery/{self.filename}'),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    media_id = db.Column(db.Integer, db.ForeignKey('media.id'), nullable=True)
    personal_log_id = db.Column(db.Integer, db.ForeignKey('personal_log.id'), nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    author = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    replies = db.relationship('Comment', backref=db.backref('parent', remote_side='Comment.id'),
                              cascade='all, delete-orphan', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'media_id': self.media_id,
            'personal_log_id': self.personal_log_id,
            'parent_id': self.parent_id,
            'author': self.author,
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'replies': [reply.to_dict() for reply in self.replies],
        }
