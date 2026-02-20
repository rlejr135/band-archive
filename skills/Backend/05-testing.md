# Testing

파일 위치: `backend/tests/`

---

## 실행

```bash
cd band-archive/backend
pytest                    # 전체 테스트
pytest -v                 # 상세 출력
pytest tests/test_songs.py # 특정 파일만
```

---

## Fixtures (`tests/conftest.py`)

| Fixture | 설명 |
|---------|------|
| `app` | TestingConfig(in-memory DB) + temp upload 폴더로 Flask 앱 생성 |
| `client` | Flask test client (HTTP 요청용) |
| `sample_song` | 테스트용 Song 레코드 1개 미리 생성 |

```python
@pytest.fixture
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
```

---

## 테스트 파일

| 파일 | 대상 |
|------|------|
| `test_songs.py` | 곡 CRUD, 검색, 파일 업로드 |
| `test_announcements.py` | 공지사항 CRUD |
| `test_rehearsals.py` | 합주 일정 CRUD |

## 테스트 구조 (`tests/test_songs.py`)

### TestSongModel
- 모델 필드 존재 확인 (genre, difficulty, timestamps)
- 기본값 검증
- NULL 허용 검증

### TestCRUD
- `POST /songs` 생성 (전체/부분 데이터, 검증 실패)
- `GET /songs`, `GET /songs/<id>` 조회
- `PUT /songs/<id>` 수정 (전체/부분, 검증 실패)
- `DELETE /songs/<id>` 삭제

### TestSearch
- 제목/아티스트 검색 (`?q=`)
- 상태 필터 (`?status=`)
- 장르 필터 (`?genre=`)
- 복합 조건

### TestFileUpload
- PDF, 이미지 업로드 성공
- 허용되지 않은 확장자 거부
- 파일 누락 처리
- 업로드된 파일 다운로드 확인

### TestHome
- `GET /` 응답 확인

---

## 테스트 헬퍼

```python
def _seed(client):
    """테스트용 곡 2개 생성"""
    client.post('/songs', json={"title": "곡A", "artist": "아티스트A", "genre": "Rock"})
    client.post('/songs', json={"title": "곡B", "artist": "아티스트B", "status": "Completed"})
```

---

## 주의사항

- 테스트 DB는 in-memory SQLite (매 테스트마다 초기화)
- 파일 업로드 테스트는 `tempfile` 디렉토리 사용
- `conftest.py`의 `sample_song` fixture와 `_seed()` 중복 주의
