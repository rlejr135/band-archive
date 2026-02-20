import os

import requests
from flask import Blueprint, jsonify, request

search_bp = Blueprint('search', __name__)

NAVER_SEARCH_CLIENT_ID = os.getenv('NAVER_SEARCH_CLIENT_ID', '')
NAVER_SEARCH_CLIENT_SECRET = os.getenv('NAVER_SEARCH_CLIENT_SECRET', '')


@search_bp.route('/api/search-places')
def search_places():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify([])

    if not NAVER_SEARCH_CLIENT_ID or not NAVER_SEARCH_CLIENT_SECRET:
        return jsonify({'error': 'Naver Search API credentials not configured'}), 500

    resp = requests.get(
        'https://openapi.naver.com/v1/search/local.json',
        params={'query': query, 'display': 5},
        headers={
            'X-Naver-Client-Id': NAVER_SEARCH_CLIENT_ID,
            'X-Naver-Client-Secret': NAVER_SEARCH_CLIENT_SECRET,
        },
        timeout=5,
    )

    if resp.status_code != 200:
        return jsonify({'error': 'Naver Search API error'}), 502

    data = resp.json()
    results = []
    for item in data.get('items', []):
        results.append({
            'title': item.get('title', '').replace('<b>', '').replace('</b>', ''),
            'address': item.get('address', ''),
            'roadAddress': item.get('roadAddress', ''),
            'mapx': item.get('mapx', ''),
            'mapy': item.get('mapy', ''),
        })

    return jsonify(results)
