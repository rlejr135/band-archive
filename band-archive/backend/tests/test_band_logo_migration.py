import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from migration_bootstrap import phase18_metadata, phase19_metadata


def test_revision_18_snapshot_stays_frozen_and_revision_19_has_only_logo_additions(app):
    with app.app_context():
        before = phase18_metadata()
        after = phase19_metadata()
        assert 'band_logo' not in before.tables
        assert not any(index.name == 'uq_gallery_image_band_id'
                       for index in before.tables['gallery_image'].indexes)
        assert 'band_logo' in after.tables
        assert any(index.name == 'uq_gallery_image_band_id'
                   for index in after.tables['gallery_image'].indexes)
        assert set(after.tables) == set(before.tables) | {'band_logo'}


def test_logo_migration_enforces_one_same_band_selection_and_safe_downgrade(tmp_path):
    path = Path(__file__).parents[1] / 'migrations/versions/20261018_19_band_logo.py'
    spec = importlib.util.spec_from_file_location('band_logo_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f'sqlite:///{(tmp_path / "logo.db").as_posix()}')
    metadata = sa.MetaData()
    band = sa.Table('band', metadata, sa.Column('id', sa.Integer, primary_key=True))
    gallery = sa.Table('gallery_image', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('band_id', sa.Integer, sa.ForeignKey('band.id'), nullable=False),
    )
    with engine.begin() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        metadata.create_all(connection)
        connection.execute(band.insert(), [{'id': 1}, {'id': 2}])
        connection.execute(gallery.insert(), [
            {'id': 10, 'band_id': 1}, {'id': 11, 'band_id': 1}, {'id': 20, 'band_id': 2},
        ])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(sa.text('SELECT count(*) FROM band_logo')).scalar_one() == 0
        assert connection.execute(sa.text('SELECT count(*) FROM gallery_image')).scalar_one() == 3
        connection.execute(sa.text('INSERT INTO band_logo(band_id,gallery_image_id) VALUES (1,10)'))
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.execute(sa.text('INSERT INTO band_logo(band_id,gallery_image_id) VALUES (2,10)'))
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.execute(sa.text('INSERT INTO band_logo(band_id,gallery_image_id) VALUES (1,11)'))

    # A fresh transaction proves a selection cannot be dropped by downgrade,
    # and that deleting the selected photo cascades only the association.
    with engine.begin() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='Refusing to discard selected band logos'):
                migration.downgrade()
        connection.execute(sa.text('DELETE FROM gallery_image WHERE id=10'))
        assert connection.execute(sa.text('SELECT count(*) FROM band_logo')).scalar_one() == 0
        assert connection.execute(sa.text('SELECT count(*) FROM gallery_image')).scalar_one() == 2
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(sa.text('SELECT count(*) FROM gallery_image')).scalar_one() == 2
    engine.dispose()
