# 전체 아키텍처

기준일: 2026-08-23

## 시스템 목적

Band Archive는 밴드 활동에 필요한 곡 정보, 합주 일정, 멤버별 연습 기록, 추천곡, 공지, 댓글과 미디어를 한곳에서 관리한다. 브라우저가 대부분의 업무 화면을 제공하고, Flask API가 메타데이터·권한이 필요한 작업·외부 API 연동·R2 업로드 승인을 담당한다.

## 현재 운영 구성

```text
사용자 브라우저
  │
  ├─ React/Vite 정적 화면 ─────────────► Cloudflare (Frontend)
  │
  ├─ REST API ─────────────────────────► Fly.io
  │                                      ├─ Flask / Gunicorn
  │                                      ├─ SQLite /data 볼륨
  │                                      ├─ NAVER Local Search
  │                                      └─ FFmpeg 트랜스코딩
  │
  ├─ NAVER Maps JavaScript SDK ────────► NAVER Maps
  │
  └─ Presigned PUT/GET ────────────────► Cloudflare R2
                                           ├─ 원본 미디어
                                           ├─ 720p / 480p 영상
                                           └─ 오디오 전용 파일
```

| 계층 | 현재 책임 | 상세 문서 |
| --- | --- | --- |
| 프론트엔드 | 화면, 라우팅, UI 상태, API 호출, R2 직접 업로드, 지도 표시 | [frontend.md](frontend.md) |
| 백엔드 | REST API, 데이터 모델, R2 presign, 외부 검색, 트랜스코딩 | [backend.md](backend.md) |
| 관계형 데이터 | Fly.io 볼륨의 SQLite에 서비스 메타데이터 저장 | [infrastructure.md](infrastructure.md) |
| 오브젝트 데이터 | Cloudflare R2에 원본·파생 미디어 저장 | [infrastructure.md](infrastructure.md) |
| 배포 | Cloudflare FE, Fly.io BE; k3s는 초안 | [deployment.md](deployment.md) |

## 프론트엔드 경계

`band-archive/frontend`는 React Router 기반 단일 페이지 애플리케이션이다. 곡 화면은 `SongContext`가 전역 상태를 관리하며, 합주·멤버·갤러리 등은 화면별 로컬 상태를 사용한다. 서비스 모듈은 Flask API와 통신하고, 대용량 미디어와 갤러리 파일은 백엔드가 발급한 presigned URL을 사용해 R2로 직접 전송한다.

브라우저에 포함되는 `VITE_API_URL`과 `VITE_NAVER_MAP_CLIENT_ID`는 공개 설정이다. R2 자격증명이나 NAVER Search Secret 같은 서버 비밀을 프론트엔드 빌드에 포함하면 안 된다.

## 백엔드 경계

`band-archive/backend`는 Flask 앱 팩토리와 도메인별 Blueprint로 구성된다. SQLAlchemy 모델은 곡, 미디어, 합주, 멤버, 개인 기록, 갤러리, 댓글, 공지와 추천곡을 표현한다.

백엔드는 다음과 같은 외부 경계를 가진다.

- Cloudflare R2: S3 호환 API로 객체 확인·복사·삭제와 presigned URL 발급
- NAVER Local Search: 장소명 검색을 서버 자격증명으로 호출
- Fly.io Volume: SQLite DB 영속 저장
- FFmpeg: 업로드된 영상을 720p, 480p, 오디오로 변환

## 핵심 데이터 흐름

### 일반 업무 요청

1. 브라우저가 Fly.io의 Flask API를 호출한다.
2. Blueprint가 요청을 검증하고 SQLAlchemy 모델을 조회·변경한다.
3. SQLite에 반영한 결과를 JSON으로 반환한다.
4. 프론트엔드가 Context 또는 화면 상태를 갱신한다.

### R2 직접 업로드

1. 프론트엔드가 파일 메타데이터로 presign API를 호출한다.
2. 백엔드가 UUID 기반 객체 키와 제한 시간 PUT URL을 발급한다.
3. 브라우저가 파일을 Cloudflare R2에 직접 업로드한다.
4. 프론트엔드가 완료 API를 호출한다.
5. 백엔드가 R2 객체 존재를 확인하고 DB 레코드를 생성한다.

이 흐름은 대용량 파일이 Fly API를 통과하지 않는 장점이 있지만, presign 발급 전에 사용자 권한·파일 크기·콘텐츠 정책·사용량 한도를 서버가 검증해야 한다.

### 영상 트랜스코딩

1. 영상 미디어 레코드가 생성되면 백엔드 스레드가 작업을 시작한다.
2. 원본을 R2에서 임시 디렉터리로 내려받는다.
3. FFmpeg로 720p, 480p, 오디오 전용 파일을 만든다.
4. 파생 파일을 R2에 업로드하고 DB 상태를 완료 또는 실패로 갱신한다.

현재 방식은 API 머신의 자원을 공유하고 재시작 시 작업을 잃을 수 있다. 향후에는 내구성 있는 큐와 별도 워커로 분리하는 것이 백엔드 이전보다 선행되어야 한다.

### NAVER 지도·장소 검색

- 브라우저는 NAVER Maps JavaScript SDK로 지도, 좌표 선택과 역지오코딩 UI를 제공한다.
- 장소명 검색은 백엔드 `/api/search-places`를 거쳐 NAVER Local Search API를 호출한다.
- 합주 레코드는 장소명과 위도·경도를 저장한다.

## 배포 상태의 구분

| 구분 | 상태 |
| --- | --- |
| Cloudflare 프론트엔드 | 현재 운영 |
| Fly.io 백엔드 | 현재 운영 |
| Cloudflare R2 | 현재 운영 |
| GitHub Pages 워크플로 | 현재 운영 사실과 불일치하는 과거/대체 경로 |
| k3s 매니페스트 | 실운영이 아닌 검토 초안 |
| 백엔드 Cloudflare/타 호스팅 이전 | 결정 전 로드맵 후보 |

## 구조적 우선과제

1. 공통 인증·인가와 변경 권한을 API에서 강제한다.
2. 비밀값을 Git과 manifest에서 제거하고 노출 가능 키를 회전한다.
3. 업로드 정책과 트랜스코딩 작업을 API 요청 처리에서 분리한다.
4. 정식 DB 마이그레이션과 백업·복구 체계를 만든다.
5. 테스트와 관측성을 갖춘 뒤 백엔드 호스팅 후보를 동일 조건으로 비교한다.
