"""Read the logical storage bytes charged against a band's upload quota."""

from sqlalchemy import func, select

from extensions import db
from models import (
    DirectUploadSession, GalleryImage, Media, MultipartUploadSession, PersonalLog,
    PersonalLogMultipartUploadSession,
)


def _sum_band_bytes(model, column, band_id, *conditions):
    return select(func.coalesce(func.sum(column), 0)).where(
        model.band_id == band_id, *conditions,
    ).scalar_subquery()


def read_band_storage_bytes(band_id):
    """Return stored and reserved bytes from one database snapshot.

    Reservations remain charged until their recorded state is terminal. Upload
    initiation can clean expired sessions under its existing write lock first.
    This read never changes a session or inspects storage objects.
    """
    used = sum(_sum_band_bytes(model, model.file_size, band_id)
               for model in (Media, PersonalLog, GalleryImage))
    multipart_reserved = sum(
        _sum_band_bytes(model, model.declared_bytes, band_id,
                        model.status.in_(('initiated', 'completing', 'aborting')))
        for model in (MultipartUploadSession, PersonalLogMultipartUploadSession)
    )
    direct_reserved = _sum_band_bytes(
        DirectUploadSession, DirectUploadSession.declared_bytes, band_id,
        DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
    )
    used_bytes, reserved_bytes = db.session.execute(select(used, multipart_reserved + direct_reserved)).one()
    return int(used_bytes), int(reserved_bytes)
