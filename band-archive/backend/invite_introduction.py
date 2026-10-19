"""Invitation-only rendering of the shared, strictly public introduction."""
from sqlalchemy import func

from band_access import BandReadScope, RequestAudience
from errors import NotFoundError
from extensions import db
from models import Announcement, BandMembership, GalleryImage, Song
from v2_content_serializers import scope_with_band_visibility


def introduction_payload(band, actor=None, *, audience=None):
    if band is None or band.deleted_at is not None:
        raise NotFoundError('Invitation not found')
    if audience is None:
        audience = RequestAudience(
            user_id=actor.id if actor is not None else None,
            subject=actor.supabase_user_id if actor is not None else None,
            account_active=bool(actor and actor.status == 'active'),
            email_verified=bool(actor and actor.email_verified is True),
            is_platform_admin=bool(actor and actor.is_platform_admin is True),
        )
    scope = BandReadScope(band.id, band.slug, audience, None)
    payload = render_introduction(scope, band)
    payload['viewer']['can_open_workspace'] = bool(
        audience.account_active and audience.email_verified
        and payload['viewer']['membership_status'] == 'active'
    )
    return payload


def render_introduction(scope, band):
    """Aggregate the shared introduction after caller resolves visibility scope."""
    from routes.content_reads import (
        _announcement_query, _featured_gallery_rows_query, _gallery_query, _song_query,
    )
    from v2_remaining_read_serializers import serialize_announcement, serialize_gallery_image

    scope = scope_with_band_visibility(scope, band)
    total_songs = _song_query(scope, with_rehearsals=False).order_by(None).with_entities(
        func.count(Song.id),
    ).scalar() or 0
    featured_gallery = None
    featured_rows = _featured_gallery_rows_query(scope).all()
    if len(featured_rows) == 1:
        image = _gallery_query(scope).filter(GalleryImage.id == featured_rows[0].id).one_or_none()
        if image is not None:
            try:
                featured_gallery = serialize_gallery_image(scope, image)
            except Exception:
                featured_gallery = None
    announcement_rows = _announcement_query(scope).order_by(Announcement.id.asc()).limit(2).all()
    announcement = None
    if len(announcement_rows) == 1:
        announcement = serialize_announcement(scope, announcement_rows[0])
        text = announcement.get('content') or ''
        announcement['content'] = text[:10_000]
        if len(text) > 10_000:
            announcement['content_truncated'] = True
    membership_status = None
    if scope.audience.authenticated:
        membership_status = db.session.query(BandMembership.status).filter_by(
            band_id=band.id, user_id=scope.audience.user_id,
        ).scalar()
    return {
        'band': {'id': band.id, 'name': band.name, 'slug': band.slug, 'description': band.description},
        'featured_gallery': featured_gallery,
        'stats': {'total_songs': total_songs},
        'song_count_scope': 'MEMBERS' if scope.is_member else 'PUBLIC',
        'announcement': announcement,
        'viewer': {'membership_status': membership_status,
                   'can_open_workspace': scope.is_member},
    }
