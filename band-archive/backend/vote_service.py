"""Transactional optimistic vote mutation shared by Song and Media routes."""

from dataclasses import dataclass

from sqlalchemy import text

from extensions import db
from errors import NotFoundError
from legacy_scope import validate_legacy_graph


@dataclass(frozen=True)
class VoteResult:
    item: object
    previous_value: int
    conflict_snapshot: object = None

    @property
    def is_conflict(self):
        return self.conflict_snapshot is not None


def apply_vote(*, item_model, vote_model, foreign_key, item_id, voter_hash_value,
               value, expected_value, band_id_resolver, not_found_message=None, conflict_snapshot):
    """Apply one compare-and-set vote and keep denormalized counters in sync.

    ``band_id_resolver`` is required rather than optional so every caller
    supplies an explicit tenant boundary. It is evaluated after SQLite obtains
    its write lock, avoiding a pre-lock scope query that would weaken its
    current compare-and-set semantics.

    ``conflict_snapshot`` is deliberately evaluated while the database lock is
    still held. The caller may access relationships while serializing, and a
    later competing request must not change what the 409 response represents.
    """
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
            band_id = band_id_resolver()
            item = db.session.query(item_model).filter(
                item_model.id == item_id, item_model.band_id == band_id,
            ).one_or_none()
        else:
            band_id = band_id_resolver()
            item = db.session.query(item_model).filter(
                item_model.id == item_id, item_model.band_id == band_id,
            ).with_for_update().first()
        if not item:
            if not_found_message is None:
                raise NotFoundError()
            raise NotFoundError(not_found_message)
        validate_legacy_graph(item, not_found_message or 'Resource not found')

        filters = {foreign_key: item_id, 'voter_hash': voter_hash_value, 'band_id': band_id}
        previous_vote = db.session.query(vote_model).filter_by(**filters).first()
        previous_value = previous_vote.value if previous_vote else 0
        if previous_value != expected_value:
            snapshot = conflict_snapshot(item, previous_value)
            db.session.rollback()
            return VoteResult(item=item, previous_value=previous_value, conflict_snapshot=snapshot)

        if previous_value != value:
            if previous_vote and value == 0:
                db.session.delete(previous_vote)
            elif previous_vote:
                previous_vote.value = value
            else:
                db.session.add(vote_model(**{
                    foreign_key: item_id, 'band_id': band_id,
                    'voter_hash': voter_hash_value, 'value': value,
                }))

            upvote_delta = int(value == 1) - int(previous_value == 1)
            downvote_delta = int(value == -1) - int(previous_value == -1)
            item.upvote_count = max(0, (item.upvote_count or 0) + upvote_delta)
            item.downvote_count = max(0, (item.downvote_count or 0) + downvote_delta)
            item.vote_score = item.upvote_count - item.downvote_count
        db.session.commit()
        return VoteResult(item=item, previous_value=previous_value)
    except Exception:
        db.session.rollback()
        raise
