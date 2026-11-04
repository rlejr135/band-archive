"""Atomic authenticated votes, preserving each domain's account identity."""

import hashlib

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auth_service import current_app_user
from band_service import audit
from comment_rules import _owned_or_404, _target_for_comment
from content_write_scope import active_member_write_scope
from extensions import db
from models import MediaVote, SongSuggestion, SongSuggestionVote


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _run_vote_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise



def _v2_voter_hash(membership_id):
    # Namespacing prevents account votes from ever becoming a legacy device
    # identity, while the digest preserves the existing fixed-width schema.
    return hashlib.sha256(f'v2-membership:{membership_id}'.encode('utf-8')).hexdigest()


def _suggestion_or_404(suggestion_id, band_id, *, lock=False):
    return _owned_or_404(SongSuggestion, suggestion_id, band_id, 'Suggestion not found', lock=lock)


def set_media_vote(*, band_id, media_id, value, prepare_response):
    """Apply one membership vote and prepare its response before commit."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        media = _target_for_comment('media', media_id, band.id, lock=True)
        # Locking the parent media serializes inserts as well as updates on
        # PostgreSQL; BEGIN IMMEDIATE does the same on SQLite.
        row = MediaVote.query.filter_by(media_id=media.id, created_by_membership_id=membership.id).one_or_none()
        previous = row.value if row else 0
        if previous != value:
            if row is not None and value == 0:
                db.session.delete(row)
            elif row is not None:
                row.value = value
            elif value:
                # The parent media lock serializes normal v2 callers.  The
                # savepoint is a second line of defence for a direct writer or
                # a transaction that began before it acquired that lock: after
                # a uniqueness race, reread the authoritative account vote
                # and apply the requested state exactly once.
                try:
                    with db.session.begin_nested():
                        row = MediaVote(
                            media_id=media.id, band_id=band.id, created_by_membership_id=membership.id,
                            is_legacy=False, voter_hash=_v2_voter_hash(membership.id), value=value,
                        )
                        db.session.add(row)
                        db.session.flush()
                except IntegrityError:
                    row = MediaVote.query.filter_by(
                        media_id=media.id, created_by_membership_id=membership.id,
                    ).one_or_none()
                    if row is None:
                        raise
                    previous = row.value
                    row.created_by_membership_id = membership.id
                    if previous != value:
                        row.value = value
            media.upvote_count = max(0, (media.upvote_count or 0) + int(value == 1) - int(previous == 1))
            media.downvote_count = max(0, (media.downvote_count or 0) + int(value == -1) - int(previous == -1))
            media.vote_score = media.upvote_count - media.downvote_count
        db.session.flush()
        audit('content.media_vote.changed', user, band, media, {'value': value, 'changed': previous != value})
        return prepare_response(scope, media, value)
    return _run_vote_write(operation)


def set_song_suggestion_vote(*, band_id, suggestion_id, value, prepare_response):
    """Apply one account vote while retaining the current membership context."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        suggestion = _suggestion_or_404(suggestion_id, band.id, lock=True)
        row = SongSuggestionVote.query.filter_by(
            suggestion_id=suggestion.id, voter_user_id=user.id,
        ).one_or_none()
        previous = row.value if row else 0
        # The account is the vote identity; retain the newest active
        # membership only as mutable audit context after a leave/rejoin.
        if row is not None:
            row.created_by_membership_id = membership.id
        if previous != value:
            if row is not None and value == 0:
                db.session.delete(row)
            elif row is not None:
                row.value = value
            elif value:
                # The locked suggestion serializes ordinary PG callers.  The
                # savepoint makes direct or stale concurrent inserts converge
                # on one membership row without double-counting the totals.
                try:
                    with db.session.begin_nested():
                        row = SongSuggestionVote(
                            band_id=band.id, suggestion_id=suggestion.id,
                            voter_user_id=user.id, created_by_membership_id=membership.id,
                            is_legacy=False, value=value,
                        )
                        db.session.add(row); db.session.flush()
                except IntegrityError:
                    row = SongSuggestionVote.query.filter_by(
                        suggestion_id=suggestion.id, voter_user_id=user.id,
                    ).one_or_none()
                    if row is None:
                        raise
                    previous = row.value
                    if previous != value:
                        row.value = value
            suggestion.thumbs_up = max(0, (suggestion.thumbs_up or 0) + int(value == 1) - int(previous == 1))
            suggestion.thumbs_down = max(0, (suggestion.thumbs_down or 0) + int(value == -1) - int(previous == -1))
        db.session.flush()
        audit('content.suggestion_vote.changed', user, band, suggestion,
              {'value': value, 'changed': previous != value})
        return prepare_response(scope, suggestion, value)
    return _run_vote_write(operation)
