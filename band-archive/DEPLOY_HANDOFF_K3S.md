# 🚀 Band Archive - Docker & k3s Deployment Handoff

이 문서는 **Band Archive** 프로젝트를 Docker 및 k3s 환경에 배포하기 위한 가이드와 정보를 담고 있습니다.

## 1. 프로젝트 개요
밴드 합주 및 연습 데이터를 관리하는 서비스입니다. 
- **Backend**: Flask (Python 3.13), FFmpeg(트랜스코딩 필수)
- **Frontend**: React (Vite, React Router v7)
- **Database**: SQLite (파일 기반, 영구 볼륨 필요)
- **Storage**: Cloudflare R2 (S3 호환)

---

## 2. Docker 빌드 전략

### 🔹 Backend (`backend/Dockerfile`)
이미 작성된 Dockerfile을 사용하되, 다음 사항을 확인하십시오:
- **FFmpeg 설치**: 비디오 트랜스코딩(720p, 480p, Audio 추출)을 위해 반드시 필요합니다. (현재 이미지에 포함됨)
- **볼륨 마운트**: `/data` 디렉토리에 SQLite DB 파일(`band_archive.db`)이 저장됩니다. k3s 배포 시 PVC 마운트가 필수입니다.
- **Port**: 기본 8080 포트를 사용합니다.

### 🔹 Frontend (Dockerfile 필요)
멀티 스테이지 빌드를 권장합니다:
1. **Stage 1**: `node:20` 이미지를 사용하여 `npm run build` 수행
2. **Stage 2**: `nginx:stable-alpine` 이미지를 사용하여 `dist` 폴더 서빙
   - SPA 라우팅을 위해 Nginx 설정(`try_files $uri $uri/ /index.html`)이 필요합니다.

---

## 3. k3s 배포 구성 (Manifests)

### 💾 Persistent Volume (Storage)
- **SQLite DB**: Backend Pod 재시작 시 데이터 유지를 위해 `/data` 경로를 위한 `PersistentVolumeClaim(PVC)` 정의가 필요합니다.

### ⚙️ Environment Variables (ConfigMap & Secret)

#### Backend (Sensitive 정보는 Secret 권장)
- `FLASK_CONFIG`: `config.ProductionConfig`
- `DATABASE_URL`: `sqlite:////data/band_archive.db`
- `SECRET_KEY`: Flask 세션용 키
- `CORS_ALLOWED_ORIGINS`: Frontend 도메인 주소 (콤마 구분)
- `S3_ENDPOINT_URL`: Cloudflare R2 엔드포인트
- `S3_ACCESS_KEY`: R2 Access Key
- `S3_SECRET_KEY`: R2 Secret Key
- `S3_BUCKET_NAME`: R2 버킷 이름

#### Frontend (빌드 타임 또는 런타임 환경변수)
- `VITE_API_URL`: Backend API 주소
- `VITE_NAVER_MAP_CLIENT_ID`: 네이버 지도 API ID

### 🌐 Ingress & Networking
- **Ingress**: Traefik (k3s 기본) 등을 사용하여 `api.yourdomain.com` (BE) 및 `band.yourdomain.com` (FE) 라우팅 설정.
- **CORS**: BE의 `CORS_ALLOWED_ORIGINS` 설정이 FE 주소와 일치해야 합니다.

---

## 4. 특이 사항 및 주의사항
1. **비디오 트랜스코딩 리소스**: FFmpeg 작업은 CPU/Memory 부하가 큽니다. k3s Deployment 정의 시 `resources.limits`를 넉넉하게 설정하는 것을 권장합니다 (최소 1Gi RAM).
2. **타임아웃**: Gunicorn 타임아웃이 300초로 설정되어 있습니다. 비디오 업로드 및 처리가 길어질 수 있으므로 Ingress의 Proxy Timeout 설정도 이에 맞춰 조정해야 합니다.
3. **M4A 호환성**: 백엔드에서 `.m4a` 파일을 `audio/mp4`로 서빙하도록 로직이 짜여 있습니다.

---

## 5. 작업 우선순위 (Next Steps for Deployment Agent)
1. Frontend용 `Dockerfile` 작성 및 이미지 빌드/푸시.
2. Backend 이미지 빌드/푸시 (기존 `Dockerfile` 활용).
3. k3s Namespace 생성.
4. SQLite용 PVC 및 StorageClass 설정.
5. ConfigMap/Secret 생성 (R2 및 API 설정).
6. Backend & Frontend Deployment/Service 배포.
7. Ingress 설정을 통한 외부 노출.

---
최근 완료된 **비디오 화질 선택 및 트랜스코딩 상태 UI** 개선 사항이 포함되어 있으므로, FFmpeg가 정상 작동하는 환경인지 반드시 확인해 주세요.
