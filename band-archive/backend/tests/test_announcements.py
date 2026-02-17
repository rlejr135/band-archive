class TestAnnouncement:
    """공지사항 API 테스트."""

    def test_get_announcement_empty(self, client):
        """공지가 없을 때 빈 응답 반환."""
        resp = client.get('/announcement')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['id'] is None
        assert data['content'] == ''

    def test_create_announcement_via_put(self, client):
        """PUT으로 최초 공지 생성."""
        resp = client.put('/announcement', json={'content': '연습 시간 변경: 토요일 오후 3시'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['id'] == 1
        assert data['content'] == '연습 시간 변경: 토요일 오후 3시'
        assert data['updated_at'] is not None

    def test_get_announcement_after_create(self, client):
        """생성 후 GET으로 조회."""
        client.put('/announcement', json={'content': '테스트 공지'})
        resp = client.get('/announcement')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['content'] == '테스트 공지'

    def test_update_existing_announcement(self, client):
        """기존 공지 수정."""
        client.put('/announcement', json={'content': '원본'})
        resp = client.put('/announcement', json={'content': '수정됨'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['id'] == 1
        assert data['content'] == '수정됨'

    def test_update_announcement_empty_content(self, client):
        """빈 내용으로 수정 시 400 에러."""
        resp = client.put('/announcement', json={'content': ''})
        assert resp.status_code == 400

    def test_update_announcement_missing_content(self, client):
        """content 필드 누락 시 400 에러."""
        resp = client.put('/announcement', json={})
        assert resp.status_code == 400

    def test_update_announcement_no_body(self, client):
        """요청 본문 없을 시 400 에러."""
        resp = client.put('/announcement', content_type='application/json')
        assert resp.status_code == 400

    def test_upsert_keeps_single_record(self, client):
        """여러 번 PUT해도 레코드는 1개만 유지."""
        client.put('/announcement', json={'content': '첫 번째'})
        client.put('/announcement', json={'content': '두 번째'})
        client.put('/announcement', json={'content': '세 번째'})

        resp = client.get('/announcement')
        data = resp.get_json()
        assert data['id'] == 1
        assert data['content'] == '세 번째'
