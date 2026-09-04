from flask import Blueprint, jsonify, request

from extensions import db
from models import GalleryImage
from errors import ValidationError, NotFoundError
from route_helpers import get_or_404
from storage import storage
from upload_helpers import prepare_upload
from validators import (IMAGE_EXTENSIONS, allowed_image_file, generate_secure_filename,
                        guess_content_type)

gallery_bp = Blueprint('gallery', __name__)

def _get_image_or_404(id):
    return get_or_404(db.session, GalleryImage, id, "Image not found")


@gallery_bp.route('/gallery', methods=['GET'])
def get_images():
    images = GalleryImage.query.order_by(GalleryImage.created_at.desc()).all()
    return jsonify([img.to_dict() for img in images])


@gallery_bp.route('/gallery', methods=['POST'])
def upload_image():
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
        filename=upload.filename,
        original_filename=upload.original_filename,
        file_size=upload.file_size,
    )
    db.session.add(image)
    db.session.commit()
    return jsonify(image.to_dict()), 201


@gallery_bp.route('/gallery/<int:id>', methods=['DELETE'])
def delete_image(id):
    image = _get_image_or_404(id)
    storage.delete(f'gallery/{image.filename}')
    db.session.delete(image)
    db.session.commit()
    return jsonify({"message": "Image deleted"}), 200


@gallery_bp.route('/gallery/<int:id>/featured', methods=['PATCH'])
def set_featured(id):
    image = _get_image_or_404(id)

    # 기존 대표 이미지 해제
    GalleryImage.query.filter_by(is_featured=True).update({'is_featured': False})
    image.is_featured = True
    db.session.commit()
    return jsonify(image.to_dict()), 200


@gallery_bp.route('/gallery/featured', methods=['GET'])
def get_featured():
    image = GalleryImage.query.filter_by(is_featured=True).first()
    if not image:
        return jsonify(None), 200
    return jsonify(image.to_dict()), 200
