# 가사(lyrics) 필드 삭제 - Backend 변경 계획

## 변경 대상 파일 및 내용

### 1. `backend/models.py`

| 줄 | 현재 코드 | 변경 |
|----|----------|------|
| 11 | `lyrics = db.Column(db.Text, nullable=True)` | 삭제 |
| 28 | `'lyrics': self.lyrics,` (to_dict) | 삭제 |

### 2. `backend/routes/songs.py`

| 줄 | 현재 코드 | 변경 |
|----|----------|------|
| 102 | `lyrics=data.get('lyrics'),` (POST 생성) | 삭제 |
| 140-141 | `if 'lyrics' in data:` / `song.lyrics = data['lyrics']` (PATCH 수정) | 삭제 |

### 3. DB 마이그레이션

- `lyrics` 컬럼을 DB에서 제거하는 마이그레이션 필요
- 마이그레이션 도구 사용 여부에 따라 수동 SQL 또는 Alembic 등으로 처리
- 기존 데이터에 가사가 저장되어 있다면 백업 고려

## 요약

- 변경 파일: 2개 (`models.py`, `routes/songs.py`)
- 삭제할 코드: 총 5줄
- 추가 작업: DB 마이그레이션 (lyrics 컬럼 제거)
