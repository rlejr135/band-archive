# 배포 운영 가이드

## 현재 배포 대상

- 프런트엔드는 Cloudflare에서 정적 사이트로 운영한다. Vite 빌드 결과물(`dist`)을 배포 대상로 삼는다.
- 백엔드는 Fly.io에서 운영한다. `backend/fly.toml`은 프로덕션 Flask 설정, HTTPS, 8080 내부 포트, `/data` 영속 볼륨을 정의한다.
- 미디어 객체는 Cloudflare R2에 보관하고, 백엔드가 S3 호환 API를 통해 접근한다.

저장소의 `.github/workflows/deploy.yml`은 `master` push 시 GitHub Pages에 프런트엔드를 배포하는 워크플로다. Cloudflare가 현재 운영 대상이라는 사실과 일치하지 않는다. 중복 배포나 잘못된 API 주소를 막기 위해 이 워크플로를 현행 파이프라인으로 사용하려면 Cloudflare 배포 워크플로로 교체하고, 그렇지 않으면 비활성화 여부를 운영 결정으로 남긴다.

## 배포 절차

### 프런트엔드 (Cloudflare)

1. 의존성을 lockfile 기준으로 설치하고 `npm run lint`, `npm run build`를 실행한다.
2. Cloudflare 프로젝트의 빌드 설정에서 프런트엔드 디렉터리와 산출물 디렉터리 `dist`를 지정한다.
3. Cloudflare의 빌드 환경변수에 아래 Vite 변수를 설정한 뒤 새 빌드를 배포한다. 변경은 반드시 재빌드가 필요하다.
4. 공개 사이트에서 SPA 새로고침, Fly API 호출, NAVER 지도 표시와 업로드 시작 동작을 확인한다.

### 백엔드 (Fly.io)

1. 프로덕션 환경변수와 R2·NAVER 서버 자격증명이 Fly 비밀 저장소에 있는지 확인한다. 값은 저장소·로그·문서에 쓰지 않는다.
2. 이미지 빌드 후 Fly 배포를 실행한다. 배포 전후 API의 읽기·쓰기 경로와 R2 presigned 업로드 경로를 점검한다.
3. 새 릴리스가 정상 응답하는지 확인한 다음, SQLite 볼륨이 의도한 앱·리전에 연결되어 있는지 확인한다.
4. 트랜스코딩을 포함한 실제 업로드 한 건을 검증하고, 생성된 R2 객체와 DB 메타데이터가 함께 남는지 확인한다.

## 환경변수 및 비밀 원칙

| 변수 | 설정 위치 | 용도·분류 |
| --- | --- | --- |
| `VITE_API_URL` | Cloudflare 빌드 환경 | 브라우저가 호출할 API 기본 URL; 빌드 타임 공개 설정 |
| `VITE_NAVER_MAP_CLIENT_ID` | Cloudflare 빌드 환경 | 지도 브라우저 SDK 식별자; 빌드 타임 공개 설정 |
| `FLASK_CONFIG` | Fly 환경 | 프로덕션 Flask 설정 선택 |
| `DATABASE_URL` | Fly 환경 | SQLite DB 위치 또는 향후 관리형 DB 연결 문자열; 비밀일 수 있음 |
| `SECRET_KEY` | Fly secret | Flask 비밀값. 샘플 환경에는 있지만 현재 `Config`가 읽지 않으므로, 세션·서명 기능에서 사용하기 전에 설정 코드에 연결해야 함 |
| `CORS_ALLOWED_ORIGINS` | Fly 환경 | 허용된 Cloudflare 프런트엔드 origin 목록 |
| `S3_ENDPOINT_URL`, `S3_BUCKET_NAME` | Fly 환경 | R2 연결 대상 설정 |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Fly secret | R2 접근 자격증명 |
| `NAVER_SEARCH_CLIENT_ID`, `NAVER_SEARCH_CLIENT_SECRET` | Fly secret | 서버 측 NAVER Search 연동 자격증명 |

`VITE_` 접두사 변수는 브라우저 번들에 노출될 수 있으므로 비밀을 넣지 않는다. 반대로 액세스 키·비밀 키·서버용 검색 비밀·Flask 비밀값은 Cloudflare 빌드 로그, Git, 이미지 레이어, 클라이언트 환경변수에 두지 않는다. CORS에는 실제 Cloudflare 공개 origin만 명시하고, 프리뷰·커스텀 도메인을 추가할 때 변경 이력을 남긴다.

## 검증, 롤백, 장애 대응

| 상황 | 즉시 확인 | 복구 방향 |
| --- | --- | --- |
| FE 배포 후 API 실패 | 빌드에 포함된 `VITE_API_URL`, Fly CORS, 브라우저 네트워크 오류 | 직전 Cloudflare 배포로 되돌리고 환경변수를 수정해 재빌드 |
| Fly API 장애·느린 응답 | 머신 상태, 재시작·cold start, CPU/메모리, 트랜스코딩 작업 | 직전 정상 Fly 릴리스로 되돌리고, 대용량 작업을 중지·분리 |
| 업로드/미디어 실패 | R2 권한·endpoint·버킷, presigned URL 만료, 객체와 DB 레코드 일치 | 자격증명을 회전·수정하고 실패한 업로드를 정리 또는 재처리 |
| SQLite 손상·볼륨 장애 | 최근 백업, 볼륨 연결 상태, 애플리케이션 오류 | 검증된 백업을 새 볼륨/DB에 복원하고 R2 객체 참조를 점검 |

릴리스마다 Cloudflare 배포 식별자와 Fly 릴리스 식별자를 남기고, 롤백 전에도 SQLite 데이터 변경의 복구 가능 여부를 확인한다. 데이터베이스 스키마 변경은 앱 롤백만으로 되돌지 않을 수 있으므로 백업과 호환성 계획을 먼저 마련한다.

## k3s 자료의 상태

`k3s/`는 실운영 선언이 아니라 배포 초안으로 취급한다. 그 근거는 예시 레지스트리·도메인 값, `latest` 태그, 수동 이미지 교체를 전제한 매니페스트, 그리고 CI가 k3s를 적용하지 않고 GitHub Pages만 배포하는 구성이다. 또한 `k3s/secrets.yaml`은 실제 비밀을 담을 수 있는 경로이므로 Git 추적 여부를 확인하고, 실제 값이 커밋된 적이 있다면 즉시 폐기·회전한다. k3s를 운영 후보로 올리려면 이미지 레지스트리와 고정 태그, TLS, probe, 백업, 비밀 관리, 배포 자동화 및 복구 훈련을 갖춘 뒤 별도 검증 환경에서 전환한다.
