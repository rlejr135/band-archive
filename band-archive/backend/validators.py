from errors import ValidationError

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
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


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
