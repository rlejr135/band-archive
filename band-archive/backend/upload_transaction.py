"""Existing outer write boundary shared by upload initiation and completion.

Operations may retain their own durable intermediate commits. This helper does
not turn the multipart workflow or its R2 effects into one atomic transaction.
"""

from sqlalchemy import text

from extensions import db


def _begin_write():
    if db.engine.dialect.name == 'sqlite':
        db.session.execute(text('BEGIN IMMEDIATE'))


def run_upload_write(operation):
    try:
        _begin_write()
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise
