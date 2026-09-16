
from datetime import datetime, timedelta, timezone
import uuid
from sqlalchemy import event, text
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


class AppUser(db.Model):
    """Local authorization profile for a Supabase-authenticated person.

    Passwords and refresh tokens are intentionally never stored here.
    """
    __tablename__ = 'app_user'

    id = db.Column(db.Integer, primary_key=True)
    supabase_user_id = db.Column(db.String(64), nullable=False, unique=True)
    email = db.Column(db.String(320), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='active', server_default='active')
    email_verified = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    is_platform_admin = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            'id': self.supabase_user_id,
            'email': self.email,
            'display_name': self.display_name,
            'status': self.status,
            'email_verified': self.email_verified,
            'is_platform_admin': self.is_platform_admin,
        }


class AuthBootstrapState(db.Model):
    """One-row claim guard for the initial platform administrator."""
    __tablename__ = 'auth_bootstrap_state'

    key = db.Column(db.String(64), primary_key=True)
    claimed_by_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=False, unique=True)
    claimed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


# Phase 3 tables are intentionally not part of ``baseline_metadata()``.  They
# are created only by Alembic revision 20260915_03, never by the v1
# compatibility ``create_all`` startup path.
class Band(db.Model):
    __tablename__ = 'band'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    default_visibility = db.Column(db.String(20), nullable=False,
                                   default='PUBLIC', server_default='PUBLIC')
    member_roster_public = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    storage_quota_bytes = db.Column(db.BigInteger, nullable=False,
                                    default=20 * 1024 * 1024 * 1024,
                                    server_default=str(20 * 1024 * 1024 * 1024))
    # NULL is reserved for the immutable, ownerless legacy import band. New
    # creation requests continue to require a real requester at service level.
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True)
    is_legacy_import = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True)
    purge_after = db.Column(db.DateTime, nullable=True)

    memberships = db.relationship('BandMembership', backref='band', lazy=True,
                                  cascade='all, delete-orphan')

    __table_args__ = (
        db.CheckConstraint("default_visibility IN ('PUBLIC', 'MEMBERS')", name='ck_band_visibility'),
        db.CheckConstraint('storage_quota_bytes > 0', name='ck_band_storage_quota'),
    )


class BandMembership(db.Model):
    __tablename__ = 'band_membership'

    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('app_user.id', ondelete='CASCADE'), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default='member', server_default='member')
    status = db.Column(db.String(20), nullable=False, default='pending', server_default='pending')
    requested_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    left_at = db.Column(db.DateTime, nullable=True)
    acted_by_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True)

    user = db.relationship('AppUser', foreign_keys=[user_id])
    acted_by = db.relationship('AppUser', foreign_keys=[acted_by_user_id])

    __table_args__ = (
        db.UniqueConstraint('band_id', 'user_id', name='uq_band_membership_user'),
        # A partial unique index is portable between SQLite and PostgreSQL and
        # gives the owner invariant a database backstop: a band can never have
        # two active owners, including when two application processes race.
        db.Index(
            'uq_band_membership_active_owner', 'band_id', unique=True,
            sqlite_where=db.text("role = 'owner' AND status = 'active'"),
            postgresql_where=db.text("role = 'owner' AND status = 'active'"),
        ),
        db.CheckConstraint("role IN ('owner', 'admin', 'member')", name='ck_band_membership_role'),
        db.CheckConstraint("status IN ('pending', 'active', 'rejected', 'left')", name='ck_band_membership_status'),
    )


class BandCreationRequest(db.Model):
    __tablename__ = 'band_creation_request'

    id = db.Column(db.Integer, primary_key=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id', ondelete='CASCADE'), nullable=False,
                                      index=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending', server_default='pending')
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True)
    review_note = db.Column(db.Text, nullable=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    requested_by = db.relationship('AppUser', foreign_keys=[requested_by_user_id])
    reviewed_by = db.relationship('AppUser', foreign_keys=[reviewed_by_user_id])
    band = db.relationship('Band')

    __table_args__ = (
        # Only one still-actionable request for a user/slug can exist.  Old
        # decisions remain as history and do not prevent a later request.
        db.Index(
            'uq_band_creation_request_pending_user_slug', 'requested_by_user_id', 'slug', unique=True,
            sqlite_where=db.text("status = 'pending'"),
            postgresql_where=db.text("status = 'pending'"),
        ),
        db.CheckConstraint("status IN ('pending', 'active', 'rejected')", name='ck_band_creation_request_status'),
    )


class BandInviteCode(db.Model):
    __tablename__ = 'band_invite_code'

    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id', ondelete='CASCADE'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True)

    band = db.relationship('Band')
    created_by = db.relationship('AppUser', foreign_keys=[created_by_user_id])
    revoked_by = db.relationship('AppUser', foreign_keys=[revoked_by_user_id])


class AuditEvent(db.Model):
    __tablename__ = 'audit_event'

    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id', ondelete='SET NULL'), nullable=True, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id', ondelete='SET NULL'), nullable=True, index=True)
    event_type = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(100), nullable=True)
    target_id = db.Column(db.String(100), nullable=True)
    details_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    band = db.relationship('Band')
    actor = db.relationship('AppUser')


class TenantBackfillState(db.Model):
    """Explicit operator evidence for the expand -> backfill -> contract path."""
    __tablename__ = 'tenant_backfill_state'

    key = db.Column(db.String(80), primary_key=True)
    status = db.Column(db.String(20), nullable=False)
    report_json = db.Column(db.Text, nullable=True)
    validated_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        db.CheckConstraint("status IN ('planned', 'applying', 'validated', 'failed')",
                           name='ck_tenant_backfill_state_status'),
    )


@event.listens_for(AuditEvent, 'before_update')
@event.listens_for(AuditEvent, 'before_delete')
def _audit_events_are_append_only(mapper, connection, target):
    """Keep audit history immutable even when code uses an ORM session."""
    raise RuntimeError('Audit events are append-only and cannot be changed or deleted.')


class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    visibility = db.Column(db.String(20), nullable=True, default='PUBLIC', server_default='PUBLIC')
    title = db.Column(db.String(100), nullable=False)
    artist = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='Practice')  # Practice, Completed, OnHold
    chords = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(200), nullable=True)
    memo = db.Column(db.Text, nullable=True)
    genre = db.Column(db.String(50), nullable=True)
    difficulty = db.Column(db.Integer, default=3)
    sheet_music = db.Column(db.String(200), nullable=True)
    sheet_music_object_key = db.Column(db.String(500), nullable=True)
    upvote_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    downvote_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    vote_score = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    votes = db.relationship('SongVote', backref='song', lazy=True,
                            cascade='all, delete-orphan')
    __table_args__ = (db.CheckConstraint("visibility IN ('PUBLIC', 'MEMBERS')", name='ck_song_visibility'),)

    def to_dict(self, media_viewer_votes=None):
        media_viewer_votes = media_viewer_votes or {}
        # Keep the pre-vote list order for Song itself.  Votes only rank media
        # within a song; ties retain the historical ID/insertion order.
        media = sorted(self.media_files, key=lambda item: (-item.vote_score, item.id))
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
            'has_featured_media': any(m.is_featured for m in self.media_files),
            'media': [item.to_dict(viewer_vote=media_viewer_votes.get(item.id, 0)) for item in media],
            'rehearsals': [{'id': r.id, 'title': r.title, 'date': r.date.isoformat()} for r in self.rehearsals],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class SongVote(db.Model):
    """One privacy-preserving vote per song and browser/device identity."""
    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    is_legacy = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
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
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    visibility = db.Column(db.String(20), nullable=True, default='PUBLIC', server_default='PUBLIC')
    song_id = db.Column(db.Integer, db.ForeignKey('song.id'), nullable=False)
    rehearsal_id = db.Column(db.Integer, db.ForeignKey('rehearsal.id'), nullable=True)
    filename = db.Column(db.String(200), nullable=False)
    # Canonical keys are additive: filename remains the v1 compatibility field.
    object_key = db.Column(db.String(500), nullable=True)
    original_filename = db.Column(db.String(200), nullable=True)
    file_type = db.Column(db.String(20), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    # Video: queued -> processing -> completed/failed. Other media: not_required.
    transcoding_status = db.Column(db.String(20), default='not_required', nullable=False)
    audio_filename = db.Column(db.String(200), nullable=True)
    audio_object_key = db.Column(db.String(500), nullable=True)
    processing_error = db.Column(db.String(500), nullable=True)
    processing_started_at = db.Column(db.DateTime, nullable=True)
    processing_completed_at = db.Column(db.DateTime, nullable=True)
    processing_attempts = db.Column(db.Integer, default=0, nullable=False)
    processing_heartbeat_at = db.Column(db.DateTime, nullable=True)
    # Immutable 720p migration derivative.  ``filename`` remains the original
    # R2 key basename until an explicit, CAS-checked finalize references this.
    video_720_filename = db.Column(db.String(300), nullable=True)
    video_720_object_key = db.Column(db.String(500), nullable=True)
    video_720_source_etag = db.Column(db.String(200), nullable=True)
    video_720_profile = db.Column(db.String(100), nullable=True)
    video_720_completed_at = db.Column(db.DateTime, nullable=True)
    # Set only by the audited R2-prune command after the linked 720p video
    # and extracted M4A have both been verified.  ``filename`` remains the
    # historical original basename for auditability and record deletion.
    original_pruned_at = db.Column(db.DateTime, nullable=True)
    is_featured = db.Column(db.Boolean, default=False)
    upvote_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    downvote_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    vote_score = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    song = db.relationship('Song', backref=db.backref('media_files', lazy=True, cascade='all, delete-orphan'))
    rehearsal = db.relationship('Rehearsal', backref=db.backref('media_files', lazy=True))
    comments = db.relationship('Comment', backref='media', lazy=True, cascade='all, delete-orphan',
                               foreign_keys='Comment.media_id')
    votes = db.relationship('MediaVote', backref='media', lazy=True,
                            cascade='all, delete-orphan')
    __table_args__ = (db.CheckConstraint("visibility IN ('PUBLIC', 'MEMBERS')", name='ck_media_visibility'),)

    def to_dict(self, viewer_vote=0):
        from media_processing import processing_fields
        fields = processing_fields(self, 'media')
        qualities = {}
        if fields['original_url']:
            qualities['original'] = fields['original_url']
        if fields['video_720_url']:
            qualities['720p'] = fields['video_720_url']
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
            'upvote_count': self.upvote_count,
            'downvote_count': self.downvote_count,
            'vote_score': self.vote_score,
            'viewer_vote': viewer_vote,
            'comment_count': len(self.comments),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class MediaVote(db.Model):
    """One privacy-preserving vote per media item and browser/device identity."""
    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    is_legacy = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    media_id = db.Column(db.Integer, db.ForeignKey('media.id', ondelete='CASCADE'), nullable=False, index=True)
    voter_hash = db.Column(db.String(64), nullable=False)
    value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('media_id', 'voter_hash', name='uq_media_vote_voter'),
        db.CheckConstraint('value IN (-1, 1)', name='ck_media_vote_value'),
    )


class MultipartUploadSession(db.Model):
    """Server-owned authorization boundary for one R2 multipart media upload."""
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    # Existing multipart upload ids address legacy object keys and cannot be
    # safely resumed under a future tenant prefix without an R2-side copy.
    is_legacy = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
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
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    visibility = db.Column(db.String(20), nullable=True, default='PUBLIC', server_default='PUBLIC')
    title = db.Column(db.String(100), nullable=False)
    artist = db.Column(db.String(100), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    memo = db.Column(db.Text, nullable=True)
    thumbs_up = db.Column(db.Integer, default=0)
    thumbs_down = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.CheckConstraint("visibility IN ('PUBLIC', 'MEMBERS')", name='ck_song_suggestion_visibility'),)

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
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    linked_user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True, index=True)
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
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    visibility = db.Column(db.String(20), nullable=True, default='PUBLIC', server_default='PUBLIC')
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    object_key = db.Column(db.String(500), nullable=True)
    original_filename = db.Column(db.String(200), nullable=True)
    file_type = db.Column(db.String(20), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    transcoding_status = db.Column(db.String(20), default='not_required', nullable=False)
    audio_filename = db.Column(db.String(200), nullable=True)
    audio_object_key = db.Column(db.String(500), nullable=True)
    processing_error = db.Column(db.String(500), nullable=True)
    processing_started_at = db.Column(db.DateTime, nullable=True)
    processing_completed_at = db.Column(db.DateTime, nullable=True)
    processing_attempts = db.Column(db.Integer, default=0, nullable=False)
    processing_heartbeat_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    member = db.relationship('Member', backref=db.backref('personal_logs', lazy=True, cascade='all, delete-orphan'))
    comments = db.relationship('Comment', backref='personal_log', lazy=True, cascade='all, delete-orphan',
                               foreign_keys='Comment.personal_log_id')
    __table_args__ = (db.CheckConstraint("visibility IN ('PUBLIC', 'MEMBERS')", name='ck_personal_log_visibility'),)

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
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    is_legacy = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
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
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    visibility = db.Column(db.String(20), nullable=True, default='PUBLIC', server_default='PUBLIC')
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
    __table_args__ = (db.CheckConstraint("visibility IN ('PUBLIC', 'MEMBERS')", name='ck_rehearsal_visibility'),)

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
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    visibility = db.Column(db.String(20), nullable=True, default='PUBLIC', server_default='PUBLIC')
    content = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.CheckConstraint("visibility IN ('PUBLIC', 'MEMBERS')", name='ck_announcement_visibility'),)

    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class GalleryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    visibility = db.Column(db.String(20), nullable=True, default='PUBLIC', server_default='PUBLIC')
    filename = db.Column(db.String(200), nullable=False)
    object_key = db.Column(db.String(500), nullable=True)
    original_filename = db.Column(db.String(200), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    is_featured = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.CheckConstraint("visibility IN ('PUBLIC', 'MEMBERS')", name='ck_gallery_image_visibility'),)

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
    # Denormalized scope is filled from media, personal log, or parent comment.
    band_id = db.Column(db.Integer, db.ForeignKey('band.id'), nullable=True, index=True)
    created_by_membership_id = db.Column(db.Integer, db.ForeignKey('band_membership.id'), nullable=True, index=True)
    is_legacy = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    media_id = db.Column(db.Integer, db.ForeignKey('media.id'), nullable=True)
    personal_log_id = db.Column(db.Integer, db.ForeignKey('personal_log.id'), nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    author = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    # A media playback offset in seconds.  This remains nullable so comments
    # written before timestamp links, and ordinary text comments, retain the
    # same representation.
    timestamp_seconds = db.Column(db.Float, nullable=True)
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
            'timestamp_seconds': self.timestamp_seconds,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'replies': [reply.to_dict() for reply in self.replies],
        }


class TenantScopeUnavailable(RuntimeError):
    """Raised only outside an HTTP request that the readiness gate catches."""


def _legacy_band_id(connection):
    state = connection.execute(text(
        "SELECT status FROM tenant_backfill_state WHERE key = 'phase4_tenant_scope'"
    )).scalar_one_or_none()
    if state != 'validated':
        # Isolated unit fixtures deliberately use full metadata without an
        # Alembic history; they are not a production compatibility path.
        try:
            from flask import current_app
            if current_app.testing:
                return None
        except RuntimeError:
            pass
        raise TenantScopeUnavailable('Phase 4 tenant backfill is not validated.')
    band_id = connection.execute(text(
        'SELECT id FROM band WHERE is_legacy_import = 1 AND created_by_user_id IS NULL'
    )).scalar_one_or_none()
    if band_id is None:
        raise TenantScopeUnavailable('Validated legacy tenant band is missing.')
    return band_id


def _parent_band(connection, table, record_id):
    if record_id is None:
        return None
    return connection.execute(text(f'SELECT band_id FROM {table} WHERE id = :id'), {'id': record_id}).scalar_one_or_none()


def _assign_legacy_scope(mapper, connection, target):
    """Central dual-write guard for legacy v1 ORM writers after backfill."""
    legacy_band_id = _legacy_band_id(connection)
    if legacy_band_id is None:
        return
    parent = None
    if isinstance(target, (Media, MultipartUploadSession, SongVote)):
        parent = _parent_band(connection, 'song', target.song_id)
    elif isinstance(target, (PersonalLog, PersonalLogMultipartUploadSession)):
        parent = _parent_band(connection, 'member', target.member_id)
    elif isinstance(target, MediaVote):
        parent = _parent_band(connection, 'media', target.media_id)
    elif isinstance(target, Comment):
        if target.media_id is not None:
            parent = _parent_band(connection, 'media', target.media_id)
        elif target.personal_log_id is not None:
            parent = _parent_band(connection, 'personal_log', target.personal_log_id)
        else:
            parent = _parent_band(connection, 'comment', target.parent_id)
    if parent is not None and parent != legacy_band_id:
        raise TenantScopeUnavailable('Parent record is outside the legacy tenant.')
    if target.band_id is None:
        target.band_id = parent or legacy_band_id
    if target.band_id != legacy_band_id:
        raise TenantScopeUnavailable('v1 writer cannot create a non-legacy tenant record.')
    if hasattr(target, 'visibility') and target.visibility is None:
        target.visibility = 'PUBLIC'


for _scoped_model in (Song, Rehearsal, Media, Member, PersonalLog, GalleryImage,
                      Announcement, SongSuggestion, Comment, SongVote, MediaVote,
                      MultipartUploadSession, PersonalLogMultipartUploadSession):
    event.listen(_scoped_model, 'before_insert', _assign_legacy_scope)
