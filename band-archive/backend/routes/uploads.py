from flask import Blueprint, jsonify, request

from extensions import db
from models import Media, Song, GalleryImage
from errors import ValidationError, NotFoundError
from storage import storage
from validators import (
    allowed_file,
    generate_secure_filename,
    detect_file_type,
    guess_content_type,
)

uploads_bp = Blueprint('uploads', __name__)

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def _allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


@uploads_bp.route('/uploads/presign', methods=['POST'])
def presign():
    data = request.get_json()
    if not data:
        raise ValidationError("Request body is required")

    filename = data.get('filename', '').strip()
    content_type = data.get('content_type', '').strip()
    upload_type = data.get('upload_type', '').strip()

    if not filename:
        raise ValidationError("filename is required")
    if upload_type not in ('media', 'gallery'):
        raise ValidationError("upload_type must be 'media' or 'gallery'")

    # 확장자 검증
    if upload_type == 'media' and not allowed_file(filename):
        raise ValidationError("File type not allowed")
    if upload_type == 'gallery' and not _allowed_image(filename):
        raise ValidationError("Image files only. Allowed: png, jpg, jpeg, gif, webp")

    # content_type fallback
    if not content_type:
        content_type = guess_content_type(filename)

    secure_name = generate_secure_filename(filename)
    key = f'{upload_type}/{secure_name}'
    upload_url = storage.generate_upload_url(key, content_type=content_type)

    return jsonify({
        'upload_url': upload_url,
        'key': key,
        'filename': secure_name,
        'content_type': content_type,
    }), 200


@uploads_bp.route('/uploads/complete/media', methods=['POST'])
def complete_media():
    data = request.get_json()
    if not data:
        raise ValidationError("Request body is required")

    filename = data.get('filename', '').strip()
    original_filename = data.get('original_filename', '').strip()
    file_size = data.get('file_size')
    song_id = data.get('song_id')
    rehearsal_id = data.get('rehearsal_id')

    if not filename:
        raise ValidationError("filename is required")
    if not song_id:
        raise ValidationError("song_id is required")

    # Song 존재 확인
    song = db.session.get(Song, song_id)
    if not song:
        raise NotFoundError("Song not found")

    # R2에 실제 업로드 확인
    if not storage.exists(f'media/{filename}'):
        raise ValidationError("File not found in storage. Upload may have failed.")

    media = Media(
        song_id=song_id,
        rehearsal_id=rehearsal_id,
        filename=filename,
        original_filename=original_filename or None,
        file_type=detect_file_type(filename),
        file_size=file_size,
    )
    db.session.add(media)
    db.session.commit()
    return jsonify(media.to_dict()), 201


@uploads_bp.route('/uploads/complete/gallery', methods=['POST'])
def complete_gallery():
    data = request.get_json()
    if not data:
        raise ValidationError("Request body is required")

    filename = data.get('filename', '').strip()
    original_filename = data.get('original_filename', '').strip()
    file_size = data.get('file_size')

    if not filename:
        raise ValidationError("filename is required")

    # R2에 실제 업로드 확인
    if not storage.exists(f'gallery/{filename}'):
        raise ValidationError("File not found in storage. Upload may have failed.")

    image = GalleryImage(
        filename=filename,
        original_filename=original_filename or None,
        file_size=file_size,
    )
    db.session.add(image)
    db.session.commit()
    return jsonify(image.to_dict()), 201
