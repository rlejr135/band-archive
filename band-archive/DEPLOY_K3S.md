# 🚀 Band Archive - k3s Deployment Guide

이 문서는 k3s(Kubernetes) 환경에 Band Archive 프로젝트를 배포하는 방법을 설명합니다.

## 1. 사전 준비

### 🔹 Docker 이미지 빌드 및 푸시
백엔드와 프론트엔드 이미지를 빌드하여 Docker Registry에 푸시해야 합니다.

```bash
# Backend
cd backend
docker build -t your-registry/band-archive-backend:latest .
docker push your-registry/band-archive-backend:latest

# Frontend
cd ../frontend
docker build -t your-registry/band-archive-frontend:latest .
docker push your-registry/band-archive-frontend:latest
```

### 🔹 도메인 및 클라우드 설정
- **R2 (Cloudflare)**: 버킷 생성 및 API 토큰 발급.
- **Naver API**: 지도 및 지역 검색 API 자격 증명 준비.
- **도메인**: `band.yourdomain.com` (FE), `api.yourdomain.com` (BE) 등을 k3s 노드 IP로 연결.

## 2. 매니페스트 설정 수정

`band-archive/k3s/` 폴더 내의 파일들을 실제 환경에 맞게 수정하십시오.

1. **`configmap.yaml`**: 실제 도메인 주소 수정 (`CORS_ALLOWED_ORIGINS`, `VITE_API_URL`).
2. **`secrets.yaml`**: R2 자격 증명, Flask Secret Key, Naver API 자격 증명 입력.
3. **`backend.yaml` & `frontend.yaml`**: `image` 주소를 본인의 레지스트리 주소로 수정.
4. **`ingress.yaml`**: `host` 도메인을 실제 도메인으로 수정.

## 3. k3s 배포 실행

`kubectl` 명령어를 사용하여 매니페스트를 적용합니다.

```bash
cd k3s
kubectl apply -f namespace.yaml
kubectl apply -f pvc.yaml
kubectl apply -f configmap.yaml
kubectl apply -f secrets.yaml
kubectl apply -f backend.yaml
kubectl apply -f frontend.yaml
kubectl apply -f ingress.yaml
```

## 4. 운영 및 모니터링

- **로그 확인**:
  ```bash
  kubectl logs -n band-archive deployment/backend
  kubectl logs -n band-archive deployment/frontend
  ```
- **DB 유지관리**: SQLite 파일은 `sqlite-pvc`에 저장되며 `/data/band_archive.db` 경로에 위치합니다.
- **트랜스코딩**: FFmpeg 작업은 리소스를 많이 소모하므로 `backend.yaml`의 리소스 제한(limits)을 모니터링하며 조정하십시오.

---
**주의**: `secrets.yaml`에는 민감한 정보가 포함되어 있으므로 실제 운영 환경에서는 안전하게 관리(예: SealedSecrets, Vault)하는 것을 권장합니다.
