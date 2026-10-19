"""Names submitted for a band application, not global account identity."""
import unicodedata

from errors import ValidationError


def required_join_name(value):
    if not isinstance(value, str):
        raise ValidationError('name is required and must be text.')
    # Validate before trimming so trailing tabs/newlines cannot hide controls.
    if any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} for char in value):
        raise ValidationError('name contains unsupported control characters.')
    value = value.strip()
    if not 1 <= len(value) <= 100:
        raise ValidationError('name must be between 1 and 100 characters.')
    return value
