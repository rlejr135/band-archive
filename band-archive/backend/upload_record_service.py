"""Create a processed record after an R2 object has been verified."""

from errors import ValidationError
from validators import detect_file_type


def create_uploaded_record(*, filename, original_filename, actual_size, max_video_bytes,
                           create_record, save_record, app, **record_fields):
    """Apply common size/type rules, persist the record, and wake video work."""
    file_type = detect_file_type(filename)
    if file_type == 'video' and actual_size > max_video_bytes:
        raise ValidationError('Video exceeds the 1 GiB upload limit.')
    record = create_record(
        filename=filename,
        original_filename=original_filename or None,
        file_type=file_type,
        file_size=actual_size,
        **record_fields,
    )
    return save_record(app, record)
