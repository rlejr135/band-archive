from flask import Blueprint, jsonify


search_bp = Blueprint('search', __name__)


@search_bp.route('/api/search-places', methods=['GET'])
def search_places():
    # Retire the former public proxy. Keep this explicit to make the old
    # endpoint fail closed even when compatibility writes remain enabled.
    return jsonify({'error': 'place_search_requires_band_membership'}), 410
