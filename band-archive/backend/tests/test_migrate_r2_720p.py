"""No-network coverage for immutable R2 720p migration/finalize contracts."""

from pathlib import Path
import sqlite3

import pytest

from extensions import db
from app import _run_migrations
from models import Media, Song
from media_processing import delete_original_and_audio
from tools import finalize_r2_720p_manifest as finalize
from tools import migrate_r2_720p as tool


def src_probe():
    return {'format': {'duration': '10'}, 'streams': [
        {'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080},
        {'codec_type': 'audio', 'codec_name': 'aac'},
    ]}


def out_probe():
    return {'format': {'duration': '10'}, 'streams': [
        {'codec_type': 'video', 'codec_name': 'h264', 'width': 1280, 'height': 720},
        {'codec_type': 'audio', 'codec_name': 'aac'},
    ]}


class FakeR2:
    def __init__(self, source=b'original source bytes'):
        self.objects = {'media/take.mov': source}
        self.metadata = {'media/take.mov': {}}
        self.types = {'media/take.mov': 'video/quicktime'}
        self.mutations = []
    def head(self, key):
        value = self.objects[key]
        return {'ETag': '"' + tool.hashlib.md5(value).hexdigest() + '"', 'ContentLength': len(value),
                'ContentType': self.types.get(key, 'application/octet-stream'), 'Metadata': self.metadata.get(key, {})}
    def download(self, key, path): Path(path).write_bytes(self.objects[key])
    def upload_new(self, key, path, content_type, values):
        if key in self.objects: raise tool.DestinationExists('destination_key_exists')
        self.mutations.append(('upload_new', key)); self.objects[key] = Path(path).read_bytes()
        self.metadata[key] = dict(values); self.types[key] = content_type
    def put_manifest(self, key, value):
        self.mutations.append(('manifest', key)); self.objects[key] = value; self.metadata[key] = {}; self.types[key] = 'application/json'
    def get_manifest(self, key): return self.objects[key]
    def stream_hash(self, key):
        value = self.objects[key]; return tool.hashlib.sha256(value).hexdigest(), len(value)


def make_plan(r2, path, apply=False):
    runner = tool.Runner(r2, path, apply=apply, min_scratch_bytes=0)
    runner.plan([{'media_id': 7, 'source_key': 'media/take.mov', 'filename': 'take.mov'}], run_id='run')
    return runner


def test_worker_is_api_only_and_fetch_rejects_unsafe_or_duplicate_targets():
    source = Path(tool.__file__).read_text(encoding='utf-8')
    assert 'from app import' not in source and 'from models import' not in source
    class Response:
        def raise_for_status(self): pass
        def json(self): return [{'id': 7, 'storage_filename': 'take.mov', 'file_type': 'video', 'url': 'https://signed/token'}]
    seen = {}
    assert tool.fetch_targets(request_get=lambda **kwargs: seen.update(kwargs) or Response(), migration_token='test-token') == [{'media_id': 7, 'source_key': 'media/take.mov', 'filename': 'take.mov'}]
    assert seen['headers']['X-Migration-Token'] == 'test-token'
    class Bad(Response):
        def json(self): return [{'id': 7, 'storage_filename': '../bad.mov', 'file_type': 'video'}]
    with pytest.raises(tool.MigrationError, match='invalid_filename'):
        tool.fetch_targets(request_get=lambda **kwargs: Bad(), migration_token='test-token')


def test_apply_writes_only_new_immutable_key_and_never_deletes_source(tmp_path, monkeypatch):
    r2 = FakeR2(); runner = make_plan(r2, tmp_path / 'manifest.json', apply=True)
    monkeypatch.setattr(tool, 'probe', lambda path: out_probe() if 'output' in str(path) else src_probe())
    monkeypatch.setattr(tool, 'transcode', lambda _source, output, _container: Path(output).write_bytes(b'720'))
    assert runner.run(canary=1) == 0
    item = runner.data['items'][0]
    assert r2.objects['media/take.mov'] == b'original source bytes'
    assert item['state'] == 'completed' and item['output']['key'].startswith('media/transcoded/720/7/')
    assert r2.types[item['output']['key']] == 'video/quicktime'
    assert all(action not in {'delete', 'copy'} for action, *_ in r2.mutations)


def test_equal_size_output_is_blocked_before_upload(tmp_path, monkeypatch):
    r2 = FakeR2(b'1234567890'); runner = make_plan(r2, tmp_path / 'manifest.json', apply=True)
    monkeypatch.setattr(tool, 'probe', lambda path: out_probe() if 'output' in str(path) else src_probe())
    monkeypatch.setattr(tool, 'transcode', lambda _source, output, _container: Path(output).write_bytes(b'abcdefghij'))
    assert runner.run(canary=1) == 1
    assert runner.data['items'][0]['reason'] == 'output_not_smaller'
    assert not any(action == 'upload_new' for action, *_ in r2.mutations)


def test_remote_resume_and_finalize_are_cas_checked(app, tmp_path):
    r2 = FakeR2(); runner = make_plan(r2, tmp_path / 'manifest.json', apply=True)
    item = runner.data['items'][0]
    item['source']['sha256'] = tool.hashlib.sha256(r2.objects['media/take.mov']).hexdigest()
    item['output'].update({'sha256': tool.hashlib.sha256(b'720').hexdigest(), 'size': 3,
                           'content_type': 'video/quicktime', 'profile': tool.PROFILE})
    r2.objects[item['output']['key']] = b'720'
    r2.metadata[item['output']['key']] = {'migration-profile': tool.PROFILE, 'source-etag': item['source']['etag'], 'sha256': item['output']['sha256']}
    r2.types[item['output']['key']] = 'video/quicktime'; item['state'] = 'completed'; runner.persist(remote=True)
    with app.app_context():
        song = Song(title='S', artist='A'); db.session.add(song); db.session.flush()
        media = Media(id=7, song_id=song.id, filename='take.mov', file_type='video'); db.session.add(media); db.session.commit()
        result, status = finalize.finalize_items(r2, runner.data, Media, db.session, apply=True)
        assert status == 0 and result[0]['state'] == 'finalized'
        assert db.session.get(Media, media.id).video_720_filename == item['output']['key']
        # Changed original ETag prevents a further CAS switch.
        r2.objects['media/take.mov'] = b'changed'
        assert finalize.finalize_items(r2, runner.data, Media, db.session, apply=True)[1] == 1


def test_finalizer_preflights_r2_before_short_db_transaction_and_clears_existing_transaction(app, tmp_path):
    r2 = FakeR2(); runner = make_plan(r2, tmp_path / 'manifest.json', apply=True); item = runner.data['items'][0]
    item['source']['sha256'] = tool.hashlib.sha256(r2.objects['media/take.mov']).hexdigest()
    item['output'].update({'sha256': tool.hashlib.sha256(b'720').hexdigest(), 'size': 3, 'content_type': 'video/quicktime', 'profile': tool.PROFILE})
    r2.objects[item['output']['key']] = b'720'; r2.types[item['output']['key']] = 'video/quicktime'
    r2.metadata[item['output']['key']] = {'migration-profile': tool.PROFILE, 'source-etag': item['source']['etag'], 'sha256': item['output']['sha256']}; item['state'] = 'completed'
    with app.app_context():
        song = Song(title='S', artist='A'); db.session.add(song); db.session.flush(); db.session.add(Media(id=7, song_id=song.id, filename='take.mov', file_type='video')); db.session.commit()
        # Deliberately leave a session transaction active; finalizer must roll it back.
        from sqlalchemy import text
        db.session.execute(text('SELECT 1'))
        observed = []; original_head = r2.head
        def head(key): observed.append((key, db.session().in_transaction())); return original_head(key)
        r2.head = head
        assert finalize.finalize_items(r2, runner.data, Media, db.session, apply=False)[1] == 0
        # A dry run performs all R2 checks before touching the DB.  The source
        # CAS recheck is reserved for --apply, which is the only mode allowed
        # to take SQLite's writer lock.
        source_states = [active for key, active in observed if key == 'media/take.mov']
        assert source_states == [False]
        assert all(not active for key, active in observed if key != 'media/take.mov')


def test_finalizer_rejects_source_changed_between_preflight_and_lock_stage(app, tmp_path):
    r2 = FakeR2(); runner = make_plan(r2, tmp_path / 'manifest.json', apply=True); item = runner.data['items'][0]
    item['source']['sha256'] = tool.hashlib.sha256(r2.objects['media/take.mov']).hexdigest()
    item['output'].update({'sha256': tool.hashlib.sha256(b'720').hexdigest(), 'size': 3, 'content_type': 'video/quicktime', 'profile': tool.PROFILE})
    r2.objects[item['output']['key']] = b'720'; r2.types[item['output']['key']] = 'video/quicktime'
    r2.metadata[item['output']['key']] = {'migration-profile': tool.PROFILE, 'source-etag': item['source']['etag'], 'sha256': item['output']['sha256']}; item['state'] = 'completed'
    with app.app_context():
        song = Song(title='S', artist='A'); db.session.add(song); db.session.flush()
        media = Media(id=7, song_id=song.id, filename='take.mov', file_type='video'); db.session.add(media); db.session.commit()
        original_head, source_heads = r2.head, 0
        def head(key):
            nonlocal source_heads
            result = original_head(key)
            if key == 'media/take.mov':
                source_heads += 1
                if source_heads == 1:  # after R2 preflight, before DB lock-stage HEAD
                    r2.objects[key] = b'changed-original'
            return result
        r2.head = head
        result, status = finalize.finalize_items(r2, runner.data, Media, db.session, apply=True)
        assert status == 1 and result[0]['reason'] == 'source_changed_during_finalize_lock'
        assert db.session.get(Media, media.id).video_720_filename is None


def test_no_original_prune_or_delete_feature_is_exposed():
    source = Path(tool.__file__).read_text(encoding='utf-8')
    assert '--prune-originals' not in source
    assert '.delete(' not in source


def test_normal_media_delete_cleanup_includes_linked_720_derivative(monkeypatch):
    deleted = []
    class Record:
        filename = 'original.mov'
        audio_filename = 'original_audio.m4a'
        video_720_filename = 'media/transcoded/720/7/etag.mov'
    monkeypatch.setattr('media_processing.storage.delete', deleted.append)
    delete_original_and_audio(Record(), 'media')
    assert deleted == ['media/transcoded/720/7/etag.mov', 'media/original_audio.m4a', 'media/original.mov']


def test_inventory_is_disabled_without_token_and_minimal_when_authenticated(client, app):
    assert client.get('/internal/migrations/r2-720p/inventory').status_code == 404
    app.config['R2_MIGRATION_TOKEN'] = 'inventory-token'
    assert client.get('/internal/migrations/r2-720p/inventory').status_code == 403
    with app.app_context():
        song = Song(title='S', artist='A'); db.session.add(song); db.session.flush()
        db.session.add(Media(song_id=song.id, filename='stored.mov', original_filename='display.mov', file_type='video'))
        db.session.commit()
    response = client.get('/internal/migrations/r2-720p/inventory', headers={'X-Migration-Token': 'inventory-token'})
    assert response.status_code == 200
    assert response.get_json()[0] == {'id': 1, 'storage_filename': 'stored.mov', 'file_type': 'video',
                                      'video_720_filename': None, 'video_720_source_etag': None, 'video_720_profile': None}


def test_partial_sqlite_without_personal_log_still_adds_720_columns(tmp_path):
    from flask import Flask
    path = tmp_path / 'partial.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE media (id INTEGER PRIMARY KEY, song_id INTEGER, filename VARCHAR(200), file_type VARCHAR(20))')
    connection.commit(); connection.close()
    app = Flask(__name__); app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{path}'
    _run_migrations(app); _run_migrations(app)
    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute('PRAGMA table_info(media)')}
    connection.close()
    assert {'video_720_filename', 'video_720_source_etag', 'video_720_profile', 'video_720_completed_at'} <= columns
