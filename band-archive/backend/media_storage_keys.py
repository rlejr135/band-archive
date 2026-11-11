"""Construct and verify the band folder used by new song media objects."""

import re


_BAND_SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_BASENAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}')


def validated_object_basename(value):
    if not isinstance(value, str) or not _BASENAME.fullmatch(value):
        raise ValueError('invalid_key')
    return value


def band_media_object_key(band_slug, basename):
    # BandSlug is unique and is preserved when the band's display data changes.
    if not isinstance(band_slug, str) or len(band_slug) > 80 or not _BAND_SLUG.fullmatch(band_slug):
        raise ValueError('invalid_key')
    return f'media/{band_slug}/{validated_object_basename(basename)}'


def is_band_media_object_key(band_slug, key):
    if not isinstance(key, str):
        return False
    try:
        return key == band_media_object_key(band_slug, key.rsplit('/', 1)[-1])
    except ValueError:
        return False
