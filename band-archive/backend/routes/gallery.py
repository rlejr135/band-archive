from flask import Blueprint, jsonify, request

from extensions import db
from models import GalleryImage
from errors import ValidationError, NotFoundError
from legacy_scope import (legacy_band_id, legacy_get_or_404, legacy_query,
                          reject_legacy_destructive_write, validate_legacy_graphs)
from storage import storage
from upload_helpers import prepare_upload
from validators import (IMAGE_EXTENSIONS, allowed_image_file, generate_secure_filename,
                        guess_content_type)
from legacy_read_visibility import legacy_item_visible, legacy_read_scope
from content_visibility import gallery_visibility

gallery_bp = Blueprint('gallery', __name__)

def _get_image_or_404(id):
    return legacy_get_or_404(GalleryImage, id, "Image not found")


@gallery_bp.route('/gallery', methods=['GET'])
def get_images():
    images = legacy_query(GalleryImage).order_by(GalleryImage.created_at.desc()).all()
    validate_legacy_graphs(images, 'Image not found')
    scope = legacy_read_scope(images[0].band_id if images else legacy_band_id())
    images = [image for image in images if legacy_item_visible(
        scope, gallery_visibility(scope, image),
    )]
    return jsonify([img.to_dict() for img in images])


@gallery_bp.route('/gallery', methods=['POST'])
def upload_image():
    # Prove the target tenant before reading a file or touching object storage.
    legacy_band_id()
    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    if not allowed_image_file(file.filename):
        raise ValidationError(f"Image files only. Allowed: {', '.join(IMAGE_EXTENSIONS)}")

    upload = prepare_upload(file, generate_secure_filename(file.filename),
                            guess_content_type(file.filename))
    storage.upload(f'gallery/{upload.filename}', file, content_type=upload.content_type)

    image = GalleryImage(
        band_id=legacy_band_id(),
        filename=upload.filename,
        original_filename=upload.original_filename,
        file_size=upload.file_size,
    )
    db.session.add(image)
    db.session.commit()
    return jsonify(image.to_dict()), 201


@gallery_bp.route('/gallery/<int:id>', methods=['DELETE'])
def delete_image(id):
    reject_legacy_destructive_write()
    image = _get_image_or_404(id)
    storage.delete(f'gallery/{image.filename}')
    db.session.delete(image)
    db.session.commit()
    return jsonify({"message": "Image deleted"}), 200


@gallery_bp.route('/gallery/<int:id>/featured', methods=['PATCH'])
def set_featured(id):
    image = _get_image_or_404(id)

    # 기존 대표 이미지 해제
    legacy_query(GalleryImage).filter(GalleryImage.is_featured.is_(True)).update(
        {'is_featured': False}, synchronize_session=False,
    )
    image.is_featured = True
    db.session.commit()
    return jsonify(image.to_dict()), 200


@gallery_bp.route('/gallery/featured', methods=['GET'])
def get_featured():
    image = legacy_query(GalleryImage).filter(GalleryImage.is_featured.is_(True)).first()
    if not image:
        return jsonify(None), 200
    validate_legacy_graphs([image], 'Image not found')
    scope = legacy_read_scope(image.band_id)
    if not legacy_item_visible(scope, gallery_visibility(scope, image)):
        return jsonify(None), 200
    return jsonify(image.to_dict()), 200
