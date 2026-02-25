# Band Archive Backend - Overview

## Tech Stack

| 항목 | 기술 |
|------|------|
| Framework | Flask 3.0.0 |
| ORM | Flask-SQLAlchemy 3.1.1 |
| DB (dev) | SQLite (`instance/band_archive.db`) |
| DB (prod) | SQLite (`/data/band_archive.db` on Fly.io volume) |
| File Storage | Cloudflare R2 (S3 호환, boto3) |
| Server (prod) | Gunicorn on Docker |
| Deploy | Fly.io (region: nrt, auto-stop enabled) |
| Frontend | Vite React (GitHub Pages) |

## Project Structure

```
backend/
├── app.py                 # App factory, CORS, startup migration
├── config.py              # Dev / Test / Prod configs + S3 설정
├── extensions.py          # db = SQLAlchemy()
├── storage.py             # S3 호환 스토리지 추상화 (R2/B2)
├── models.py              # Song, Media, SongSuggestion, Member, PersonalLog, Rehearsal, Announcement
├── errors.py              # ValidationError(400), NotFoundError(404)
├── validators.py          # Input validation + secure filename generation
├── routes/
│   ├── songs.py           # Song CRUD + Media CRUD + R2 파일 관리
│   ├── suggestions.py     # Song suggestion + voting
│   ├── members.py         # Member CRUD
│   ├── personal_logs.py   # Member personal log upload/delete
│   ├── announcements.py   # 공지사항 (단일 레코드 upsert)
│   ├── rehearsals.py      # 합주 일정 CRUD (달력 기능)
│   └── dashboard.py       # Aggregated stats
├── tests/
│   ├── conftest.py        # Fixtures (app, client, sample_song)
│   ├── test_songs.py      # Song endpoint tests
│   ├── test_announcements.py # 공지사항 테스트
│   └── test_rehearsals.py    # 합주 일정 테스트
├── Dockerfile             # python:3.13-slim + gunicorn
└── fly.toml               # Fly.io deployment config
```

## App Factory Flow (`create_app`)

1. Config 로드 (env `FLASK_CONFIG` or `DevelopmentConfig`)
2. CORS 설정 (debug: `*`, prod: `CORS_ALLOWED_ORIGINS`)
3. Extensions init (db, migrate, error handlers)
4. Blueprint 등록 (7개: songs, dashboard, suggestions, members, personal_logs, announcements, rehearsals)
5. StorageClient 초기화 (R2 연결)
6. `db.create_all()` + `_run_migrations()`

## Key Conventions

- **Blueprint 패턴**: 기능별 분리 (7개), 모두 복수형 이름
- **`_get_*_or_404(id)`**: 각 blueprint의 공통 헬퍼, 없으면 `NotFoundError` raise
- **`to_dict()`**: 모든 모델에 JSON 직렬화 메서드, nested relationship 포함
- **파일 저장**: Cloudflare R2 (S3 호환), UUID 기반 파일명, presigned URL로 서빙
- **Timestamp**: 모두 UTC, ISO format으로 직렬화
- **Cascade delete**: 부모 삭제 시 자식 자동 삭제 (Media, PersonalLog)
- **HTTP 상태코드**: 200(성공), 201(생성), 400(검증실패), 404(미존재), 500(서버에러)
