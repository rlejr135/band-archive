"""Add pending admission positions and event types without inferring old data.

Revision ID: 20261023_23
Revises: 20261021_22
"""
import importlib.util
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = '20261023_23'
down_revision = '20261021_22'
branch_labels = None
depends_on = None


def _bound_timeouts(bind):
    """Bound this transaction without relaxing any shorter operator timeout."""
    current = dict(bind.execute(sa.text(
        "SELECT name, setting::bigint FROM pg_settings WHERE name IN ('lock_timeout','statement_timeout')"
    )).all())
    for name, maximum in (('lock_timeout', 5000), ('statement_timeout', 30000)):
        if current[name] == 0 or current[name] > maximum:
            # Names and limits are immutable constants; SET LOCAL expires on
            # transaction completion and never changes global/session config.
            bind.exec_driver_sql(f"SET LOCAL {name} = '{maximum}ms'")


def _preflight_and_lock():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Admission/event migration requires a live PostgreSQL connection.')
        _bound_timeouts(bind)
        path = Path(__file__).with_name('20261019_20_reusable_invite_links.py')
        spec = importlib.util.spec_from_file_location('admission_event_security_revision20', path)
        security = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(security)
        tables = (*security.PREVIOUS_TABLES, 'band_invite_link', 'invite_resume_context', 'content_restriction')
        security._assert_private(bind, tables)
        bind.exec_driver_sql('LOCK TABLE public.band_membership, public.rehearsal IN ACCESS EXCLUSIVE MODE')
        return bind, lambda: security._assert_private(bind, tables)
    if bind.dialect.name != 'sqlite':
        raise RuntimeError('Admission/event migration supports PostgreSQL and SQLite only.')
    return bind, lambda: None


def upgrade():
    _, assert_private = _preflight_and_lock()
    op.add_column('band_membership', sa.Column('requested_positions_json', sa.Text(), nullable=True))
    op.add_column('band_membership', sa.Column('requested_custom_position', sa.String(50), nullable=True))
    with op.batch_alter_table('rehearsal') as batch:
        batch.add_column(sa.Column('rehearsal_type', sa.String(20), nullable=False, server_default='UNCLASSIFIED'))
        batch.create_check_constraint('ck_rehearsal_type',
                                      "rehearsal_type IN ('REGULAR', 'EXTRA', 'LIVE', 'OTHER', 'UNCLASSIFIED')")
    assert_private()


def downgrade():
    bind, assert_private = _preflight_and_lock()
    # Refuse even empty non-null request values: a downgrade must not erase
    # a recorded application decision input, irrespective of its validation.
    if bind.execute(sa.text('SELECT count(*) FROM band_membership WHERE requested_positions_json IS NOT NULL '
                            'OR requested_custom_position IS NOT NULL')).scalar_one():
        raise RuntimeError('Refusing to discard pending application positions.')
    if bind.execute(sa.text("SELECT count(*) FROM rehearsal WHERE rehearsal_type <> 'UNCLASSIFIED'")).scalar_one():
        raise RuntimeError('Refusing to discard classified event types.')
    with op.batch_alter_table('rehearsal') as batch:
        batch.drop_constraint('ck_rehearsal_type', type_='check')
        batch.drop_column('rehearsal_type')
    with op.batch_alter_table('band_membership') as batch:
        batch.drop_column('requested_custom_position')
        batch.drop_column('requested_positions_json')
    assert_private()
