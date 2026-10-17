from flask import Blueprint, jsonify, request

from extensions import db
from models import Member
from errors import NotFoundError, ValidationError
from legacy_scope import (legacy_band_id, legacy_get_or_404, legacy_query,
                          reject_foreign_children, reject_legacy_destructive_write,
                          validate_legacy_graphs)
from storage import storage
from media_processing import delete_original_and_audio
from validators import validate_required_string, validate_string_length
from legacy_read_visibility import legacy_read_scope

members_bp = Blueprint('members', __name__)


def _get_member_or_404(id):
    member = legacy_get_or_404(Member, id, "Member not found")
    from member_profile_service import active_profile_condition
    if Member.query.filter(Member.id == member.id, active_profile_condition()).first() is None:
        raise NotFoundError('Member not found')
    return member


@members_bp.route('/members', methods=['GET'])
def get_members():
    from member_profile_service import active_profile_condition
    members = legacy_query(Member).filter(active_profile_condition()).all()
    validate_legacy_graphs(members, 'Member not found')
    legacy_read_scope(members[0].band_id if members else legacy_band_id(), roster=True)
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
        band_id=legacy_band_id(),
        name=data['name'].strip(),
        instrument=data['instrument'].strip(),
    )
    db.session.add(member)
    db.session.commit()
    return jsonify(member.to_dict()), 201


@members_bp.route('/members/<int:id>', methods=['GET'])
def get_member(id):
    member = _get_member_or_404(id)
    validate_legacy_graphs([member], 'Member not found')
    legacy_read_scope(member.band_id, roster=True)
    return jsonify(member.to_dict())


@members_bp.route('/members/<int:id>', methods=['PUT'])
def update_member(id):
    member = _get_member_or_404(id)
    if member.linked_user_id is not None:
        raise ValidationError('Use the authenticated own profile endpoint.', 403)

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
    reject_legacy_destructive_write()
    member = _get_member_or_404(id)

    from models import PersonalLog
    reject_foreign_children(member, PersonalLog, 'member_id', 'Member not found')
    for log in legacy_query(PersonalLog).filter(PersonalLog.member_id == member.id).all():
        delete_original_and_audio(log, 'personal_logs')

    db.session.delete(member)
    db.session.commit()
    return jsonify({"message": "Member deleted"}), 200
