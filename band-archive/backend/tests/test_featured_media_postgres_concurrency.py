"""Private PostgreSQL checks for the featured-per-song race backstop."""

from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.test_preserve_identity_refresh_postgres import private_pg17


def test_featured_index_serializes_concurrent_replacements_and_preserves_rollback(private_pg17):
    engine = create_engine(private_pg17, pool_size=4, max_overflow=0)
    suffix = uuid.uuid4().hex
    table = f'public.featured_race_{suffix}'
    index = f'uq_featured_race_{suffix}'
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f'CREATE TABLE {table} (id integer PRIMARY KEY, band_id integer NOT NULL, '
                'song_id integer NOT NULL, is_featured boolean)'
            ))
            connection.execute(text(
                f'CREATE UNIQUE INDEX {index} ON {table} (band_id, song_id) WHERE is_featured IS TRUE'
            ))
            connection.execute(text(f'INSERT INTO {table} VALUES (1, 7, 9, TRUE)'))

        # A failed replacement transaction must roll back its prior clear.
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(f'UPDATE {table} SET is_featured=FALSE WHERE id=1'))
                connection.execute(text(f'INSERT INTO {table} VALUES (2, 7, 9, TRUE)'))
                connection.execute(text(f'INSERT INTO {table} VALUES (3, 7, 9, TRUE)'))
        with engine.connect() as connection:
            assert connection.execute(text(
                f'SELECT id FROM {table} WHERE is_featured IS TRUE'
            )).scalars().all() == [1]

        from threading import Barrier
        rendezvous = Barrier(2)

        def replace(media_id):
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    rendezvous.wait(timeout=10)
                    connection.execute(text(f'UPDATE {table} SET is_featured=FALSE WHERE id=1'))
                    connection.execute(text(
                        f'INSERT INTO {table} VALUES (:id, 7, 9, TRUE)'
                    ), {'id': media_id})
                    transaction.commit()
                    return 'committed'
                except IntegrityError:
                    transaction.rollback()
                    return 'unique-conflict'
                finally:
                    connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(replace, (4, 5)))
        assert sorted(results) == ['committed', 'unique-conflict']
        with engine.connect() as connection:
            featured_ids = connection.execute(text(
                f'SELECT id FROM {table} WHERE is_featured IS TRUE'
            )).scalars().all()
            assert len(featured_ids) == 1
            assert featured_ids[0] in {4, 5}
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS {table}'))
        engine.dispose()
