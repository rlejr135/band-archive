import uuid
import mimetypes
import string
import unicodedata
from urllib.parse import unquote, urlsplit

from errors import ValidationError

IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | {
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


def validate_external_http_url(value, field_name='link', maximum=500):
    """Return one strict absolute http(s) URL suitable for outbound links.

    This is intentionally stricter than browser URL parsing: storage must not
    normalize dangerous whitespace, credentials, backslashes, or protocol
    relative values that can later be interpreted differently by a client.
    """
    error = f'{field_name} must be a valid external HTTP(S) URL.'
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValidationError(error)

    # Keep internationalised URLs usable (normal Unicode letters and symbols
    # are allowed), while rejecting every Unicode ``C`` category: that covers
    # C0/C1 controls, format characters such as zero-width space, surrogates,
    # private-use characters, and unassigned code points.  Check each decoded
    # form too; clients do not all agree about when a URL is decoded.
    def unsafe_characters(candidate):
        return any(
            character.isspace() or character == '\\' or unicodedata.category(character).startswith('C')
            for character in candidate
        )

    def malformed_percent(candidate):
        return any(
            character == '%' and (
                position + 2 >= len(candidate)
                or candidate[position + 1] not in string.hexdigits
                or candidate[position + 2] not in string.hexdigits
            )
            for position, character in enumerate(candidate)
        )

    if unsafe_characters(value) or malformed_percent(value) or value.startswith('//'):
        raise ValidationError(error)

    # Unquote only a bounded number of times, but fail closed if another
    # decode would still change the value.  ``%25`` is a valid literal percent
    # and is retained: after it becomes a bare percent it is no longer an
    # encoded escape, rather than being misclassified as malformed input.
    decoded = value
    for _ in range(4):
        if '%' not in decoded:
            break
        try:
            next_decoded = unquote(decoded, encoding='utf-8', errors='strict')
        except UnicodeDecodeError as exc:
            raise ValidationError(error) from exc
        if unsafe_characters(next_decoded):
            raise ValidationError(error)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    else:
        try:
            if unquote(decoded, encoding='utf-8', errors='strict') != decoded:
                raise ValidationError(error)
        except UnicodeDecodeError as exc:
            raise ValidationError(error) from exc
    try:
        parsed = urlsplit(value)
        # Accessing port makes malformed values such as ``:bad`` fail closed.
        _ = parsed.port
    except ValueError as url_error:
        raise ValidationError(error) from url_error
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError(error)
    return value


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in IMAGE_EXTENSIONS


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
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    return 'document'


def guess_content_type(filename):
    """Guess MIME content type with .m4a special handling."""
    ct, _ = mimetypes.guess_type(filename)
    if filename.lower().endswith('.m4a'):
        return 'audio/mp4'
    return ct or 'application/octet-stream'
