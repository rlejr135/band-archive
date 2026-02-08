# 🚀 GitHub Pages 배포 가이드

## 📋 사전 요구사항

1. GitHub 계정
2. 백엔드 API 서버 (별도 호스팅 필요)

## 🔧 배포 설정 단계

### 1. GitHub Repository 생성

1. GitHub에서 새 repository 생성 (예: `band-archive`)
2. 로컬 프로젝트를 push:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/band-archive.git
   git branch -M main
   git push -u origin main
   ```

### 2. GitHub Pages 활성화

1. Repository → **Settings** → **Pages**
2. **Source**: "GitHub Actions" 선택

### 3. Secrets 설정 (API URL)

1. Repository → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret** 클릭
3. Name: `VITE_API_URL`
4. Value: 백엔드 API URL (예: `https://your-backend.herokuapp.com`)

### 4. 배포 실행

- `main` 브랜치에 push하면 자동 배포
- 또는 **Actions** 탭에서 수동 실행 가능

## 🌐 백엔드 호스팅 옵션

GitHub Pages는 정적 사이트만 호스팅하므로, Flask 백엔드는 별도 서비스에서 운영해야 합니다:

| 서비스 | 무료 티어 | 특징 |
|--------|----------|------|
| **Railway** | 월 $5 크레딧 | 간편한 배포 |
| **Render** | 무료 (sleep) | Flask 지원 |
| **Fly.io** | 무료 티어 | 빠른 성능 |
| **PythonAnywhere** | 무료 | Python 특화 |

### Render 배포 예시

1. [render.com](https://render.com) 가입
2. New → Web Service → GitHub 연결
3. Root Directory: `band-archive/backend`
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `gunicorn app:app`
6. Environment Variables 설정:
   - `FLASK_ENV=production`
   - `DATABASE_URL` (필요시)

## 📁 파일 구조

```
band-archive/
├── .github/
│   └── workflows/
│       └── deploy.yml    # GitHub Actions 워크플로우
├── frontend/
│   ├── .env.development  # 개발 환경 변수
│   ├── .env.production   # 프로덕션 환경 변수 (템플릿)
│   ├── public/
│   │   └── 404.html      # SPA 라우팅 폴백
│   └── vite.config.js    # base 경로 설정됨
└── backend/
    └── ...               # 별도 배포 필요
```

## ⚠️ 주의사항

1. **CORS 설정**: 백엔드에서 GitHub Pages 도메인 허용 필요
   ```python
   # backend/app.py
   CORS(app, origins=["https://YOUR_USERNAME.github.io"])
   ```

2. **HTTPS**: GitHub Pages는 HTTPS 사용. 백엔드도 HTTPS 필요

3. **Repository 이름 변경 시**: `vite.config.js`의 `base` 경로 수정 필요

## 🔗 관련 링크

- [Vite 배포 가이드](https://vite.dev/guide/static-deploy.html#github-pages)
- [GitHub Pages 문서](https://docs.github.com/en/pages)
