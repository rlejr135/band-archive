from flask import Blueprint, jsonify, request

from extensions import db
from models import GalleryImage
from errors import ValidationError, NotFoundError
from storage import storage
from validators import allowed_file, generate_secure_filename, guess_content_type

gallery_bp = Blueprint('gallery', __name__)

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def _allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def _get_image_or_404(id):
    image = db.session.get(GalleryImage, id)
    if not image:
        raise NotFoundError("Image not found")
    return image


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

    if not _allowed_image(file.filename):
        raise ValidationError(f"Image files only. Allowed: {', '.join(ALLOWED_IMAGE_EXTENSIONS)}")

    filename = generate_secure_filename(file.filename)
    content_type = guess_content_type(filename)

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    storage.upload(f'gallery/{filename}', file, content_type=content_type)

    image = GalleryImage(
        filename=filename,
        original_filename=file.filename,
        file_size=file_size,
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
