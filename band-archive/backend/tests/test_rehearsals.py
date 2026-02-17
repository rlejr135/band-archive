class TestRehearsalCRUD:
    # POST
    def test_create_rehearsal(self, client):
        resp = client.post('/rehearsals', json={
            'title': '정기 합주',
            'date': '2026-02-20',
            'time': '19:00',
            'memo': '신곡 연습',
            'color': '#ff5e57',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['title'] == '정기 합주'
        assert data['date'] == '2026-02-20'
        assert data['time'] == '19:00'
        assert data['memo'] == '신곡 연습'
        assert data['color'] == '#ff5e57'
        assert data['songs'] == []

    def test_create_rehearsal_with_songs(self, client, sample_song):
        resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': '2026-02-20',
            'song_ids': [sample_song['id']],
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert len(data['songs']) == 1
        assert data['songs'][0]['title'] == 'Bohemian Rhapsody'

    def test_create_rehearsal_with_period(self, client):
        resp = client.post('/rehearsals', json={
            'title': '공연 준비 기간',
            'date': '2026-03-01',
            'start_date': '2026-03-01',
            'end_date': '2026-03-15',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['start_date'] == '2026-03-01'
        assert data['end_date'] == '2026-03-15'

    def test_create_rehearsal_missing_title(self, client):
        resp = client.post('/rehearsals', json={'date': '2026-02-20'})
        assert resp.status_code == 400

    def test_create_rehearsal_missing_date(self, client):
        resp = client.post('/rehearsals', json={'title': '합주'})
        assert resp.status_code == 400

    def test_create_rehearsal_invalid_date(self, client):
        resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': 'not-a-date',
        })
        assert resp.status_code == 400

    def test_create_rehearsal_invalid_period(self, client):
        resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': '2026-03-15',
            'start_date': '2026-03-15',
            'end_date': '2026-03-01',
        })
        assert resp.status_code == 400

    def test_create_rehearsal_default_color(self, client):
        resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': '2026-02-20',
        })
        assert resp.status_code == 201
        assert resp.get_json()['color'] == '#ffd32a'

    # GET all
    def test_get_rehearsals_empty(self, client):
        resp = client.get('/rehearsals')
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_get_rehearsals(self, client):
        client.post('/rehearsals', json={'title': 'A', 'date': '2026-02-10'})
        client.post('/rehearsals', json={'title': 'B', 'date': '2026-02-20'})
        resp = client.get('/rehearsals')
        assert resp.status_code == 200
        assert len(resp.get_json()) == 2

    def test_get_rehearsals_filter_by_month(self, client):
        client.post('/rehearsals', json={'title': '2월', 'date': '2026-02-15'})
        client.post('/rehearsals', json={'title': '3월', 'date': '2026-03-15'})
        resp = client.get('/rehearsals?year=2026&month=2')
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == '2월'

    def test_get_rehearsals_period_overlap(self, client):
        # 기간 일정이 조회 월과 겹치는 경우
        client.post('/rehearsals', json={
            'title': '장기 연습',
            'date': '2026-01-15',
            'start_date': '2026-01-15',
            'end_date': '2026-02-15',
        })
        resp = client.get('/rehearsals?year=2026&month=2')
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == '장기 연습'

    # GET single
    def test_get_rehearsal(self, client):
        create_resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': '2026-02-20',
        })
        rid = create_resp.get_json()['id']
        resp = client.get(f'/rehearsals/{rid}')
        assert resp.status_code == 200
        assert resp.get_json()['title'] == '합주'

    def test_get_rehearsal_not_found(self, client):
        resp = client.get('/rehearsals/999')
        assert resp.status_code == 404

    # PUT
    def test_update_rehearsal(self, client):
        create_resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': '2026-02-20',
        })
        rid = create_resp.get_json()['id']
        resp = client.put(f'/rehearsals/{rid}', json={
            'title': '정기 합주',
            'time': '20:00',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['title'] == '정기 합주'
        assert data['time'] == '20:00'
        assert data['date'] == '2026-02-20'  # 변경 안 된 필드 유지

    def test_update_rehearsal_songs(self, client, sample_song):
        create_resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': '2026-02-20',
        })
        rid = create_resp.get_json()['id']
        resp = client.put(f'/rehearsals/{rid}', json={
            'song_ids': [sample_song['id']],
        })
        assert resp.status_code == 200
        assert len(resp.get_json()['songs']) == 1

    def test_update_rehearsal_not_found(self, client):
        resp = client.put('/rehearsals/999', json={'title': 'X'})
        assert resp.status_code == 404

    # DELETE
    def test_delete_rehearsal(self, client):
        create_resp = client.post('/rehearsals', json={
            'title': '합주',
            'date': '2026-02-20',
        })
        rid = create_resp.get_json()['id']
        resp = client.delete(f'/rehearsals/{rid}')
        assert resp.status_code == 200
        # 삭제 확인
        resp = client.get(f'/rehearsals/{rid}')
        assert resp.status_code == 404

    def test_delete_rehearsal_not_found(self, client):
        resp = client.delete('/rehearsals/999')
        assert resp.status_code == 404
