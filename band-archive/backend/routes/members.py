from flask import Blueprint, jsonify, request

from extensions import db
from models import Member
from errors import NotFoundError, ValidationError
from validators import validate_required_string, validate_string_length

members_bp = Blueprint('members', __name__)


def _get_member_or_404(id):
    member = db.session.get(Member, id)
    if not member:
        raise NotFoundError("Member not found")
    return member


@members_bp.route('/members', methods=['GET'])
def get_members():
    members = Member.query.all()
    return jsonify([m.to_dict() for m in members])


@members_bp.route('/members', methods=['POST'])
def create_member():
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    validate_required_string(data.get('name'), 'name')
    validate_string_length(data.get('name'), 'name', 100)
    validate_required_string(data.get('instrument'), 'instrument')
    validate_string_length(data.get('instrument'), 'instrument', 100)

    member = Member(
        name=data['name'].strip(),
        instrument=data['instrument'].strip(),
    )
    db.session.add(member)
    db.session.commit()
    return jsonify(member.to_dict()), 201


@members_bp.route('/members/<int:id>', methods=['GET'])
def get_member(id):
    member = _get_member_or_404(id)
    return jsonify(member.to_dict())


@members_bp.route('/members/<int:id>', methods=['PUT'])
def update_member(id):
    member = _get_member_or_404(id)

    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    if 'name' in data:
        validate_required_string(data['name'], 'name')
        validate_string_length(data['name'], 'name', 100)
        member.name = data['name'].strip()

    if 'instrument' in data:
        validate_required_string(data['instrument'], 'instrument')
        validate_string_length(data['instrument'], 'instrument', 100)
        member.instrument = data['instrument'].strip()

    db.session.commit()
    return jsonify(member.to_dict())


@members_bp.route('/members/<int:id>', methods=['DELETE'])
def delete_member(id):
    member = _get_member_or_404(id)
    db.session.delete(member)
    db.session.commit()
    return jsonify({"message": "Member deleted"}), 200
