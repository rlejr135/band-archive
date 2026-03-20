# Config & Deployment

---

## Configuration (`backend/config.py`)

| 환경 | DB | Debug | Upload 경로 |
|------|-----|-------|-------------|
| Development | `sqlite:///band_archive.db` (instance/) | True | `backend/uploads/` |
| Testing | `sqlite:///:memory:` | - | temp dir |
| Production | `sqlite:////data/band_archive.db` | False | `/data/uploads` |

공통: `MAX_CONTENT_LENGTH = 200MB`, `SQLALCHEMY_TRACK_MODIFICATIONS = False`

Config 선택: 환경변수 `FLASK_CONFIG` (기본값 `config.DevelopmentConfig`)

---

## Environment Variables

### 로컬 (`backend/.env`)
```
FLASK_APP=app.py
FLASK_ENV=development
DATABASE_URL=sqlite:///band_archive.db
SECRET_KEY=dev-secret-key
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
```

### Production (`fly.toml`)
```
FLASK_CONFIG=config.ProductionConfig
CORS_ALLOWED_ORIGINS=https://rlejr135.github.io,https://band-archive.pages.dev
```

---

## Fly.io Deployment

### 인프라

| 항목 | 값 |
|------|-----|
| App | `band-archive` |
| URL | https://band-archive.fly.dev |
| Region | `nrt` (Tokyo) |
| VM | shared-cpu-1x, 1GB RAM |
| Volume | `/data` (2GB, persistent) |
| Port | 8080 (internal) → HTTPS (external) |
| Auto-stop | 활성 (트래픽 없으면 자동 정지) |

### 배포 명령

```bash
cd band-archive/backend
flyctl deploy          # 빌드 + 배포
flyctl status          # 상태 확인
flyctl logs            # 로그 확인
flyctl ssh console     # SSH 접속
flyctl machine restart # 재시작
```

### Docker (`backend/Dockerfile`)

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data/uploads
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "120", "app:create_app()"]
```

---

## Frontend Deployment

- Cloudflare Pages (Git 연동, 자동 빌드/배포)
- URL: `https://band-archive.pages.dev`
- Trigger: push to `master` branch
- Build command: `cd band-archive/frontend && npm ci && npm run build`
- Build output: `band-archive/frontend/dist`
- Env: `VITE_API_URL=https://band-archive.fly.dev`, `NODE_VERSION=20`
- SPA fallback: Cloudflare Pages 네이티브 지원 (404.html 불필요)

---

## Startup Migration (`app.py: _run_migrations`)

`db.create_all()`은 기존 테이블에 새 컬럼을 추가하지 않으므로, 앱 시작 시 수동으로 누락된 컬럼을 체크하고 추가.

현재 마이그레이션 (SQLite 환경에서만 실행):
- `media` 테이블에 `original_filename` 컬럼 추가 (없으면)
- `personal_log` 테이블에 `original_filename` 컬럼 추가 (없으면)
- `personal_log` 테이블에 `file_size` 컬럼 추가 (없으면)
- `rehearsal` 테이블에 `location` 컬럼 추가 (없으면)
- `rehearsal` 테이블에 `latitude` 컬럼 추가 (없으면)
- `rehearsal` 테이블에 `longitude` 컬럼 추가 (없으면)

새 컬럼 추가 시 이 함수에 체크 로직 추가 필요.
