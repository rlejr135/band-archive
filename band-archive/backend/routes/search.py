import json
import os
import time

import requests
from flask import Blueprint, current_app, jsonify, request

from place_search import sanitize_place_results

search_bp = Blueprint('search', __name__)
_NAVER_LOCAL_SEARCH_URL = 'https://openapi.naver.com/v1/search/local.json'
_NAVER_TIMEOUT = (2, 3)
_MAX_PROVIDER_DURATION_SECONDS = 5
_MAX_PROVIDER_BODY_BYTES = 256 * 1024


@search_bp.route('/api/search-places', methods=['GET'])
def search_places():
    guard = current_app.extensions['place_search_guard']
    try:
        query = guard.normalize_query(request.args.get('query', ''))
    except ValueError:
        return jsonify({'error': 'invalid_query'}), 400
    if not query:
        return jsonify([])

    allowed, retry_after = guard.allow_request(request.remote_addr)
    if not allowed:
        return jsonify({'error': 'rate_limited'}), 429, {'Retry-After': str(retry_after)}

    cached, results = guard.get_cached(query)
    if cached:
        return jsonify(results)

    client_id = os.getenv('NAVER_SEARCH_CLIENT_ID', '')
    client_secret = os.getenv('NAVER_SEARCH_CLIENT_SECRET', '')

    if not client_id or not client_secret:
        return jsonify({'error': 'place_search_unavailable'}), 503

    headers = {
        'X-Naver-Client-Id': client_id,
        'X-Naver-Client-Secret': client_secret,
    }
    params = {
        'query': query,
        'display': 5,
    }

    reserved, retry_after = guard.reserve_upstream()
    if not reserved:
        return jsonify({'error': 'rate_limited'}), 429, {'Retry-After': str(retry_after)}

    response = None
    try:
        deadline = time.monotonic() + _MAX_PROVIDER_DURATION_SECONDS
        resp = requests.get(
            _NAVER_LOCAL_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=_NAVER_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )
        response = resp
        resp.raise_for_status()
        if not 200 <= resp.status_code < 300:
            raise requests.HTTPError('unexpected provider status')
        content_length = resp.headers.get('Content-Length')
        if content_length and int(content_length) > _MAX_PROVIDER_BODY_BYTES:
            raise ValueError('provider response too large')
        body = bytearray()
        for chunk in resp.iter_content(chunk_size=8192):
            if time.monotonic() >= deadline:
                raise requests.Timeout('provider response deadline exceeded')
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > _MAX_PROVIDER_BODY_BYTES:
                raise ValueError('provider response too large')
        data = json.loads(body.decode('utf-8'))
        results = sanitize_place_results(data)
    except (requests.RequestException, ValueError, TypeError, UnicodeError, RecursionError):
        return jsonify({'error': 'place_search_upstream_unavailable'}), 502
    finally:
        try:
            if response is not None:
                response.close()
        except Exception:
            # Closing a provider stream must not turn a handled provider error
            # into a response containing implementation details.
            pass
        finally:
            guard.release_upstream()

    guard.cache_results(query, results)

    return jsonify(results)
