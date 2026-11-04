"""Startup recovery coordination independent of the upload HTTP routes."""

from sqlalchemy import inspect

from direct_upload_service import recover_direct_sessions
from extensions import db
from multipart_upload_service import recover_sessions


def recover_multipart_upload_sessions(app):
    """Recover interrupted completion attempts and expire old R2 upload IDs.

    This runs on process startup.  It does not expose or regenerate legacy
    capabilities, so an old session without a capability remains inaccessible.
    """
    with app.app_context():
        # The Phase 4 model is additive, but a process may intentionally boot
        # against a recorded older schema while the isolated runner is pending.
        # Avoid selecting future ORM columns in that compatibility interval.
        columns = {column['name'] for column in inspect(db.engine).get_columns('multipart_upload_session')}
        if 'band_id' not in columns:
            return
        recover_sessions()
        if inspect(db.engine).has_table('direct_upload_session'):
            recover_direct_sessions()
