"""Shared value validation for authenticated content writes."""

from errors import ValidationError
from validators import validate_external_http_url


VISIBILITIES = {'PUBLIC', 'MEMBERS'}


def text_value(value, field, maximum, *, required=False, nullable=True):
    if value is None:
        if required:
            raise ValidationError(f'{field} is required.')
        return None if nullable else value
    if not isinstance(value, str):
        raise ValidationError(f'{field} must be text.')
    value = value.strip()
    if required and not value:
        raise ValidationError(f'{field} is required.')
    if len(value) > maximum:
        raise ValidationError(f'{field} must be at most {maximum} characters.')
    return value or None


def visibility_value(value, default):
    if value is None:
        return default
    if value not in VISIBILITIES:
        raise ValidationError('visibility must be PUBLIC or MEMBERS.')
    return value


def external_link_value(value, *, required=False):
    if value is None:
        if required:
            raise ValidationError('link is required.')
        return None
    return validate_external_http_url(value, 'link', 500)
