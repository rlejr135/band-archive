"""Compatibility adapters for v2 upload completion responses.

Callers retain authorization, completion transactions, session reloads and HTTP
status handling.  These adapters preserve the existing model ``to_dict``
responses, including their possible ORM reads and URL signing.  They are not
authorization checks or the tenant-aware v2 content-read serializers.
"""

from multipart_upload_service import completion_payload


def serialize_direct_upload_completion(session, item):
    result_key = 'personal_log' if session.kind == 'personal_log' else 'media'
    return {'session_id': session.id, 'status': 'completed', result_key: item.to_dict() if item else None}


def serialize_direct_gallery_completion(session, item):
    return {'session_id': session.id, 'status': 'completed', 'gallery': item.to_dict() if item else None}


def serialize_multipart_upload_completion(session, item):
    return completion_payload(session, item)
