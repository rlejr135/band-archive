"""Privacy-preserving voter identity and MediaVote lookup helpers."""

import hashlib
import uuid

from flask import request

from errors import ValidationError
from models import MediaVote


VOTER_ID_HEADER = 'X-Voter-ID'
VALID_VOTE_VALUES = (-1, 0, 1)


def voter_hash(required=False):
    """Hash a validated client UUID; never persist or log its raw value."""
    voter_id = request.headers.get(VOTER_ID_HEADER)
    if not voter_id:
        if required:
            raise ValidationError(f'{VOTER_ID_HEADER} header is required.')
        return None
    try:
        canonical_id = str(uuid.UUID(voter_id.strip()))
    except (AttributeError, ValueError):
        raise ValidationError(f'{VOTER_ID_HEADER} must be a valid UUID.')
    return hashlib.sha256(canonical_id.encode('utf-8')).hexdigest()


def parse_vote_payload(data):
    """Validate the optimistic-concurrency vote request shared by both APIs."""
    if not isinstance(data, dict):
        raise ValidationError('Request body is required')

    value = data.get('vote')
    if isinstance(value, bool) or value not in VALID_VOTE_VALUES:
        raise ValidationError('vote must be -1, 0, or 1.')

    if 'expected_viewer_vote' not in data:
        raise ValidationError('expected_viewer_vote is required.')
    expected_value = data['expected_viewer_vote']
    if isinstance(expected_value, bool) or expected_value not in VALID_VOTE_VALUES:
        raise ValidationError('expected_viewer_vote must be -1, 0, or 1.')
    return value, expected_value


def media_viewer_votes(media_files, voter_hash_value=None):
    """Fetch this viewer's votes for a media collection in one query."""
    if not voter_hash_value:
        return {}
    media_ids = [media.id for media in media_files]
    if not media_ids:
        return {}
    return {
        vote.media_id: vote.value
        for vote in MediaVote.query.filter(
            MediaVote.voter_hash == voter_hash_value,
            MediaVote.media_id.in_(media_ids),
        ).all()
    }
