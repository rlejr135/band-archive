import io
import os


# ── Step 1: Model field tests ──

class TestSongModel:
    def test_create_song_has_new_fields(self, client, sample_song):
        assert 'genre' in sample_song
        assert 'difficulty' in sample_song
        assert 'created_at' in sample_song
        assert 'updated_at' in sample_song
        assert 'sheet_music' in sample_song

    def test_default_difficulty(self, client):
        resp = client.post('/songs', json={'title': 'Test', 'artist': 'A'})
        data = resp.get_json()
        assert data['difficulty'] == 3

    def test_genre_nullable(self, client):
        resp = client.post('/songs', json={'title': 'Test', 'artist': 'A'})
        data = resp.get_json()
        assert data['genre'] is None

    def test_created_at_auto(self, client, sample_song):
        assert sample_song['created_at'] is not None

    def test_updated_at_auto(self, client, sample_song):
        assert sample_song['updated_at'] is not None


# ── Step 2: CRUD tests ──

class TestCRUD:
    # POST
    def test_create_song(self, client):
        resp = client.post('/songs', json={
            'title': 'Yesterday',
            'artist': 'Beatles',
            'genre': 'Pop',
            'difficulty': 2,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['title'] == 'Yesterday'
        assert data['artist'] == 'Beatles'
        assert data['genre'] == 'Pop'
        assert data['difficulty'] == 2
        assert data['status'] == 'Practice'

    def test_create_song_missing_title(self, client):
        resp = client.post('/songs', json={'artist': 'Beatles'})
        assert resp.status_code == 400
        assert 'title' in resp.get_json()['error']

    def test_create_song_missing_artist(self, client):
        resp = client.post('/songs', json={'title': 'Yesterday'})
        assert resp.status_code == 400
        assert 'artist' in resp.get_json()['error']

    def test_create_song_invalid_status(self, client):
        resp = client.post('/songs', json={
            'title': 'Test', 'artist': 'A', 'status': 'Invalid'
        })
        assert resp.status_code == 400
        assert 'status' in resp.get_json()['error'].lower()

    def test_create_song_invalid_difficulty(self, client):
        resp = client.post('/songs', json={
            'title': 'Test', 'artist': 'A', 'difficulty': 6
        })
        assert resp.status_code == 400
        assert 'difficulty' in resp.get_json()['error']

    # GET all
    def test_get_songs_empty(self, client):
        resp = client.get('/songs')
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_get_songs(self, client, sample_song):
        resp = client.get('/songs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == 'Bohemian Rhapsody'

    # GET single
    def test_get_song(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.get(f'/songs/{song_id}')
        assert resp.status_code == 200
        assert resp.get_json()['title'] == 'Bohemian Rhapsody'

    def test_get_song_not_found(self, client):
        resp = client.get('/songs/999')
        assert resp.status_code == 404

    # PUT
    def test_update_song(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.put(f'/songs/{song_id}', json={'title': 'Updated Title'})
        assert resp.status_code == 200
        assert resp.get_json()['title'] == 'Updated Title'
        # Other fields unchanged
        assert resp.get_json()['artist'] == 'Queen'

    def test_update_song_partial(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.put(f'/songs/{song_id}', json={'genre': 'Classic Rock'})
        assert resp.status_code == 200
        assert resp.get_json()['genre'] == 'Classic Rock'

    def test_update_song_not_found(self, client):
        resp = client.put('/songs/999', json={'title': 'X'})
        assert resp.status_code == 404

    def test_update_song_invalid_status(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.put(f'/songs/{song_id}', json={'status': 'BadStatus'})
        assert resp.status_code == 400

    def test_update_song_empty_title(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.put(f'/songs/{song_id}', json={'title': ''})
        assert resp.status_code == 400

    def test_update_song_empty_artist(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.put(f'/songs/{song_id}', json={'artist': ''})
        assert resp.status_code == 400

    def test_update_song_invalid_difficulty(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.put(f'/songs/{song_id}', json={'difficulty': 0})
        assert resp.status_code == 400

    # DELETE
    def test_delete_song(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.delete(f'/songs/{song_id}')
        assert resp.status_code == 200
        # Verify it's gone
        resp = client.get(f'/songs/{song_id}')
        assert resp.status_code == 404

    def test_delete_song_not_found(self, client):
        resp = client.delete('/songs/999')
        assert resp.status_code == 404


# ── Step 3: Search & filter tests ──

class TestSearch:
    def _seed(self, client):
        songs = [
            {'title': 'Bohemian Rhapsody', 'artist': 'Queen', 'genre': 'Rock', 'status': 'Completed'},
            {'title': 'Yesterday', 'artist': 'Beatles', 'genre': 'Pop', 'status': 'Practice'},
            {'title': 'Rock You', 'artist': 'Queen', 'genre': 'Rock', 'status': 'OnHold'},
            {'title': 'Let It Be', 'artist': 'Beatles', 'genre': 'Pop', 'status': 'Practice'},
        ]
        for s in songs:
            client.post('/songs', json=s)

    def test_search_by_title(self, client):
        self._seed(client)
        resp = client.get('/songs?q=Bohemian')
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == 'Bohemian Rhapsody'

    def test_search_by_artist(self, client):
        self._seed(client)
        resp = client.get('/songs?q=Queen')
        data = resp.get_json()
        assert len(data) == 2

    def test_search_case_insensitive(self, client):
        self._seed(client)
        resp = client.get('/songs?q=queen')
        data = resp.get_json()
        assert len(data) == 2

    def test_filter_by_status(self, client):
        self._seed(client)
        resp = client.get('/songs?status=Practice')
        data = resp.get_json()
        assert len(data) == 2
        assert all(s['status'] == 'Practice' for s in data)

    def test_filter_by_genre(self, client):
        self._seed(client)
        resp = client.get('/songs?genre=Rock')
        data = resp.get_json()
        assert len(data) == 2
        assert all(s['genre'] == 'Rock' for s in data)

    def test_combined_filters(self, client):
        self._seed(client)
        resp = client.get('/songs?genre=Pop&status=Practice')
        data = resp.get_json()
        assert len(data) == 2

    def test_combined_search_and_filter(self, client):
        self._seed(client)
        resp = client.get('/songs?q=Queen&status=Completed')
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == 'Bohemian Rhapsody'

    def test_search_no_results(self, client):
        self._seed(client)
        resp = client.get('/songs?q=Nonexistent')
        data = resp.get_json()
        assert len(data) == 0


# ── Step 4: File upload tests ──

class TestFileUpload:
    def test_upload_pdf(self, client, sample_song):
        song_id = sample_song['id']
        data = {
            'file': (io.BytesIO(b'%PDF-1.4 fake pdf content'), 'sheet.pdf'),
        }
        resp = client.post(
            f'/songs/{song_id}/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result['sheet_music'] is not None
        assert 'sheet.pdf' in result['sheet_music']

    def test_upload_image(self, client, sample_song):
        song_id = sample_song['id']
        data = {
            'file': (io.BytesIO(b'\x89PNG\r\n\x1a\n fake png'), 'score.png'),
        }
        resp = client.post(
            f'/songs/{song_id}/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        assert 'score.png' in resp.get_json()['sheet_music']

    def test_upload_disallowed_extension(self, client, sample_song):
        song_id = sample_song['id']
        data = {
            'file': (io.BytesIO(b'not allowed'), 'malware.exe'),
        }
        resp = client.post(
            f'/songs/{song_id}/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400
        assert 'not allowed' in resp.get_json()['error'].lower()

    def test_upload_no_file(self, client, sample_song):
        song_id = sample_song['id']
        resp = client.post(
            f'/songs/{song_id}/upload',
            data={},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400

    def test_upload_song_not_found(self, client):
        data = {
            'file': (io.BytesIO(b'content'), 'sheet.pdf'),
        }
        resp = client.post(
            '/songs/999/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert resp.status_code == 404

    def test_download_uploaded_file(self, client, sample_song, app):
        song_id = sample_song['id']
        content = b'%PDF-1.4 test content'
        data = {
            'file': (io.BytesIO(content), 'download_test.pdf'),
        }
        resp = client.post(
            f'/songs/{song_id}/upload',
            data=data,
            content_type='multipart/form-data',
        )
        filename = resp.get_json()['sheet_music']
        resp = client.get(f'/uploads/{filename}')
        assert resp.status_code == 200
        assert resp.data == content


# ── Home route test ──

class TestHome:
    def test_home(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert 'Band Archive' in resp.get_json()['message']
