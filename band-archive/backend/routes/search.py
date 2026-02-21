import os
import re

import requests
from flask import Blueprint, jsonify, request

search_bp = Blueprint('search', __name__)


@search_bp.route('/api/search-places', methods=['GET'])
def search_places():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify([])

    client_id = os.getenv('NAVER_SEARCH_CLIENT_ID', '')
    client_secret = os.getenv('NAVER_SEARCH_CLIENT_SECRET', '')

    if not client_id or not client_secret:
        return jsonify({'error': 'Naver Search API credentials not configured'}), 500

    headers = {
        'X-Naver-Client-Id': client_id,
        'X-Naver-Client-Secret': client_secret,
    }
    params = {
        'query': query,
        'display': 5,
    }

    try:
        resp = requests.get(
            'https://openapi.naver.com/v1/search/local.json',
            headers=headers,
            params=params,
            timeout=5,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return jsonify({'error': 'Failed to fetch from Naver API'}), 502

    data = resp.json()
    results = []
    for item in data.get('items', []):
        title = re.sub(r'</?b>', '', item.get('title', ''))
        results.append({
            'title': title,
            'address': item.get('address', ''),
            'roadAddress': item.get('roadAddress', ''),
            'mapx': item.get('mapx', ''),
            'mapy': item.get('mapy', ''),
        })

    return jsonify(results)
