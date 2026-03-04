import uuid
import mimetypes

from errors import ValidationError

ALLOWED_EXTENSIONS = {
    # Images
    'png', 'jpg', 'jpeg', 'gif', 'webp',
    # Documents
    'pdf',
    # Audio
    'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac',
    # Video
    'mp4', 'webm', 'mov', 'avi', 'mkv'
}
VALID_STATUSES = {'Practice', 'Completed', 'OnHold'}


def validate_status(status):
    if status not in VALID_STATUSES:
        raise ValidationError(f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")


def validate_difficulty(difficulty):
    if not isinstance(difficulty, int) or difficulty < 1 or difficulty > 5:
        raise ValidationError("difficulty must be an integer between 1 and 5")


def validate_required_string(value, field_name):
    if not value:
        raise ValidationError(f"{field_name} is required")


def validate_non_empty_string(value, field_name):
    if not value:
        raise ValidationError(f"{field_name} cannot be empty")


def validate_string_length(value, field_name, max_length):
    if value and len(value) > max_length:
        raise ValidationError(f"{field_name} must be {max_length} characters or less")


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_secure_filename(filename):
    """Generate a randomized filename preserving the original extension."""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    random_name = uuid.uuid4().hex
    return f"{random_name}.{ext}" if ext else random_name


def detect_file_type(filename):
    """Detect file type (video/audio/image/document) from extension."""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext in ('mp4', 'webm', 'mov', 'avi', 'mkv'):
        return 'video'
    if ext in ('mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'):
        return 'audio'
    if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return 'image'
    return 'document'


def guess_content_type(filename):
    """Guess MIME content type with .m4a special handling."""
    ct, _ = mimetypes.guess_type(filename)
    if filename.lower().endswith('.m4a'):
        return 'audio/mp4'
    return ct or 'application/octet-stream'
