"""Pure validators and normalized values for profile and rehearsal VOCs."""

import json

from errors import ValidationError


POSITION_LABELS = {
    'VOCAL': '보컬',
    'GUITAR': '기타',
    'BASS': '베이스',
    'DRUMS': '드럼',
    'KEYBOARD': '키보드',
    'OTHER': '기타 역할',
}
REHEARSAL_TYPES = frozenset({'REGULAR', 'EXTRA', 'LIVE', 'OTHER', 'UNCLASSIFIED'})


def member_position_values(positions, custom_position=''):
    """Validate a member's choices and return the canonical persistence values."""
    if (not isinstance(positions, list) or len(positions) > len(POSITION_LABELS)
            or any(not isinstance(position, str) or position not in POSITION_LABELS
                   for position in positions)
            or len(set(positions)) != len(positions)):
        raise ValidationError('positions must contain distinct supported positions.')
    if not isinstance(custom_position, str) or len(custom_position) > 50 or '\x00' in custom_position:
        raise ValidationError('custom_position must be text of at most 50 characters.')

    custom = custom_position.strip()
    if 'OTHER' not in positions:
        if custom:
            raise ValidationError('custom_position requires OTHER.')
        custom = ''
    ordered = [position for position in POSITION_LABELS if position in positions]
    instrument = ', '.join(
        custom if position == 'OTHER' and custom else POSITION_LABELS[position]
        for position in ordered
    )
    return {
        'positions': ordered,
        'positions_json': json.dumps(ordered),
        'custom_position': custom,
        'instrument': instrument,
    }


def selected_member_position_values(positions, custom_position=''):
    """Return a normalized non-empty selection, or None for preserve/default semantics."""
    if positions is None:
        positions = []
    values = member_position_values(positions, custom_position)
    return values if values['positions'] else None


def stored_member_position_values(membership):
    """Decode and validate a pending request's temporary profile selection."""
    raw = getattr(membership, 'requested_positions_json', None)
    if not raw:
        return None
    try:
        positions = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValidationError('Stored requested positions are invalid.', 409) from error
    return selected_member_position_values(
        positions, getattr(membership, 'requested_custom_position', None) or '',
    )


def validate_rehearsal_type(value):
    if not isinstance(value, str) or value not in REHEARSAL_TYPES:
        raise ValidationError('rehearsal_type must be REGULAR, EXTRA, LIVE, OTHER, or UNCLASSIFIED.')
    return value
