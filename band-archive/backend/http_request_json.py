"""Bounded JSON object parsing shared by existing HTTP endpoints."""
import json

from flask import request

from errors import ValidationError


def payload():
    if request.content_length is not None and request.content_length > 32768:
        raise ValidationError('Calendar request too large.', 413)
    raw = request.stream.read(32769)
    if len(raw) > 32768:
        raise ValidationError('Calendar request too large.', 413)
    try:
        def no_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=no_duplicates)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValidationError('A valid JSON object is required.')
    if not isinstance(value, dict):
        raise ValidationError('A JSON object is required.')
    return value
