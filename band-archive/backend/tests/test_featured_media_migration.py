import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


revision = importlib.import_module('migrations.versions.20261024_24_featured_media_per_song')


def _connection():
    engine = create_engine('sqlite:///:memory:')
    connection = engine.connect()
    connection.exec_driver_sql('CREATE TABLE band (id INTEGER PRIMARY KEY)')
    connection.exec_driver_sql('CREATE TABLE song (id INTEGER PRIMARY KEY, band_id INTEGER)')
    connection.exec_driver_sql('CREATE TABLE rehearsal (id INTEGER PRIMARY KEY, band_id INTEGER)')
    connection.exec_driver_sql('CREATE TABLE rehearsal_songs (song_id INTEGER, rehearsal_id INTEGER)')
    connection.exec_driver_sql(
        'CREATE TABLE content_restriction (band_id INTEGER, resource_type TEXT, resource_id INTEGER, state TEXT)')
    connection.exec_driver_sql(
        'CREATE TABLE r2_deletion_task (band_id INTEGER, resource_type TEXT, resource_id INTEGER, status TEXT)')
    connection.exec_driver_sql(
        'CREATE TABLE media (id INTEGER PRIMARY KEY, band_id INTEGER, song_id INTEGER, rehearsal_id INTEGER, '
        'is_featured BOOLEAN, file_type TEXT, original_pruned_at TEXT, object_key TEXT, filename TEXT, '
        'video_720_object_key TEXT, video_720_filename TEXT)')
    connection.exec_driver_sql('INSERT INTO band(id) VALUES (1), (2)')
    connection.exec_driver_sql('INSERT INTO song(id, band_id) VALUES (11, 1), (12, 1), (21, 2)')
    return engine, connection


def _upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        revision.upgrade()


def test_featured_media_migration_adds_band_song_partial_unique_index_and_preserves_nulls():
    engine, connection = _connection()
    try:
        connection.execute(text(
            "INSERT INTO media(id,band_id,song_id,is_featured,file_type,filename) "
            "VALUES (1,1,11,1,'audio','one.mp3'), (2,1,12,1,'video','two.mp4'), "
            "(3,2,21,NULL,'audio','legacy.mp3')"
        ))
        connection.commit()
        _upgrade(connection)
        connection.commit()
        indexes = {index['name']: index for index in inspect(connection).get_indexes('media')}
        assert bool(indexes['uq_media_featured_band_song']['unique']) is True
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO media(id,band_id,song_id,is_featured,file_type,filename) "
                "VALUES (4,1,11,1,'audio','duplicate.mp3')"
            ))
        connection.rollback()
        assert connection.execute(text('SELECT is_featured FROM media WHERE id=3')).scalar_one() is None
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize(
    ('media_values', 'diagnostic'),
    [
        ("(1,1,11,1,'audio','one.mp3'), (2,1,11,1,'video','two.mp4')", 'duplicate_band_song_groups=1'),
        ("(1,1,11,1,NULL,'broken.mp3')", 'unplayable_or_unsupported_rows=1'),
        ("(1,1,NULL,1,'audio','broken.mp3')", 'invalid_or_cross_band_parent_rows=1'),
        ("(1,1,11,1,'audio',NULL)", 'unplayable_or_unsupported_rows=1'),
    ],
)
def test_featured_media_migration_refuses_ambiguous_flagged_rows_without_rewriting_them(
    media_values, diagnostic,
):
    engine, connection = _connection()
    try:
        connection.execute(text(
            'INSERT INTO media(id,band_id,song_id,is_featured,file_type,filename) VALUES ' + media_values
        ))
        connection.commit()
        with pytest.raises(RuntimeError, match=diagnostic):
            _upgrade(connection)
        assert inspect(connection).get_indexes('media') == []
        assert connection.execute(text('SELECT count(*) FROM media WHERE is_featured IS TRUE')).scalar_one() > 0
    finally:
        connection.close()
        engine.dispose()
