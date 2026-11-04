# Band Archive — Architecture Context

현행 코드 지도 갱신: **2026-11-04**. 최초 조사 `b2b5610` 이후 경계 추출을 반영했다. 제품 기준 **`d5cae87`**, 전체 BE **1,738 passed·978 warnings·0 failed·0 skipped**, 제품 일반 push와 **Fly v91 배포 확인 완료**다. 공개 HTTP/CORS·시작 로그·기존 Pages 번들 확인, runtime 차이와 선택 보류는 [단일 최종 보고서](docs/reports/2026-11-04-architecture-refactoring-final.md)를 따른다. 문서 커밋은 제품과 구분한다.

이 문서는 현재 독서 경로를 안내한다. 과거 Wave 1~7의 시행 과정과 당시 검증 결과는 `docs/reports/`의 기존 아키텍처 Wave 보고서에 보존하며, 오래된 대기 문구를 현재 상태로 다시 나열하지 않는다. 줄 번호보다 아래 파일·함수 이름을 우선한다.

과거 리뷰·Wave 보고서는 로컬 보조 기록이며 Git 추적 대상이 아니면 새 clone에 없을 수 있다. 해당 파일의 존재를 독서 전제로 삼지 않는다. 현재 구조·계약은 이 지도와 용어집·계획/보완서·실제 코드로 파악할 수 있도록 적었으며, 이번 작업의 최종 실행 증거는 별도 단일 최종 보고서에 모은다.

## 0. 이 지도의 범위

목적은 코드를 대체하는 설명서가 아니라 **무엇을 이해하려면 어디부터 읽고, 어떤 연결을 따라가야 하는지 알려주는 지도**다. 도메인 의미는 같은 디렉터리의 `DOMAIN_GLOSSARY.md`를 기준으로 한다. 개선안·재설계·rename 계획은 포함하지 않는다.

파일 경로는 별도 표시가 없으면 `band-archive/` 기준이다. `저장소 루트`는 현재 `E:\Anything`, 앱 루트는 `E:\Anything\band-archive`다. 표와 본문에 있는 기존 줄 번호는 최초 분석 기준 커밋의 탐색 시작점이며 이후 변경될 수 있다.

최초 정적 조사와 이번 지도 갱신은 실행 소스·설정·테스트 본문과 도구를 읽는 방식으로 수행했다. 비밀 설정, 의존성/빌드 산출물, 운영 데이터, private evidence는 읽지 않았다. 최초 조사에서는 앱 실행·테스트·운영 API 호출·DB 접속·배포를 수행하지 않았다. 구현 검증·배포 진행 기록은 `docs/plans/2026-10-25-architecture-refactoring-plan.md`를 참조한다. 따라서 **저장소에서 확인한 구조와 실제 운영 환경의 현재 상태를 구분**한다.

## 1. 시스템을 한눈에 보기

Band Archive는 계정이 여러 밴드에 가입하여 밴드별 곡, 합주·공연 일정, 멤버 프로필, 영상·음원, 개인 연습 기록, 갤러리, 공지, 다음 곡 제안과 가능 일정을 사용하는 웹 시스템이다. 데이터 접근·권한·공개 범위의 중심 단위는 `Band`다.

먼저 혼동하지 말아야 할 개념:

- `AppUser`는 계정, `BandMembership`은 밴드 가입·권한 관계, 현재 모델 `Member`는 `MemberProfile`이다.
- `Song`은 레퍼토리 정보, `Media`는 곡에 연결된 콘텐츠다. 추출 M4A와 720p는 같은 콘텐츠의 재생 소스일 수 있다.
- `SongSuggestion`, 대상별 Vote, owner가 고르는 `FeaturedMedia`는 다른 기능이다.
- `Rehearsal`에는 `LIVE` 공연도 들어간다. `MemberAvailability`는 확정 일정이 아닌 시간대별 가능 여부다.
- 커뮤니티 구인·공연 교류·후기 화면은 FE 준비중 틀이다. 게시글 BE/DB 모델이 구현된 상태는 아니다.

근거: `backend/models.py:9`, `:72`, `:105`, `:505`, `:585`, `:810`, `:956`; `frontend/src/App.jsx:96`; `frontend/src/components/community/CommunityBoardPage.jsx:5`.

```text
브라우저 React SPA
  ├─ Supabase JS Auth ───────────→ Supabase Auth
  ├─ Bearer API 요청 ────────────→ Flask /api/v2
  │                                ├─ SQLAlchemy → 관계형 DB
  │                                ├─ boto3 → R2 객체/서명 URL
  │                                ├─ NAVER Local Search
  │                                └─ 프로세스 내 worker → DB 상태 + R2 + FFmpeg
  └─ 발급받은 서명 URL로 파일 PUT/GET ─────────────→ R2

별도 운영 경로: migration_runner / tools → 명시적 DB·객체 이전/검증 작업
별도 호환 경로: 기존 v1 API → 검증된 legacy import 밴드 범위
```

관계형 DB는 `DATABASE_URL`로 선택된다. 코드는 PostgreSQL/Supabase 전환 경로와 legacy SQLite 경로를 함께 포함한다. R2는 영상·음원·사진 저장소이며 관계형 콘텐츠 DB와 별개다. 연결 대상의 현재 운영 값은 이 조사에서 확인하지 않았다 (`backend/config.py:190`, `backend/app.py:546`, `backend/storage.py:6`).

## 2. 처음 읽는 사람을 위한 독서 경로

| 알아볼 것 | 우선 읽을 위치 | 여기서 확인할 내용 |
|---|---|---|
| 업무 개념과 ID 경계 | `DOMAIN_GLOSSARY.md` 1·2·4절 | 계정/가입/프로필, 곡/미디어, 공개 범위, 기존 명칭 |
| 화면 진입과 기능 범위 | `frontend/src/main.jsx:7`, `frontend/src/App.jsx:96` | 부팅, provider 순서, 실제 활성 라우트, 과거 URL 별칭 |
| API의 조립 방식 | `backend/app.py:369`, `:525` | factory, 공통 gate, blueprint 등록, DB·worker 시작 순서 |
| 인증과 밴드 접근 | `backend/auth_service.py:239`, `backend/band_access.py:79` | JWT 검증, 계정 동기화, scope, 공개/멤버 읽기 |
| 데이터 구조와 응답 | `backend/models.py`, 두 `v2_*serializers.py` | 관계·제약과 공개 응답은 서로 다른 경계 |
| 특정 기능의 실제 호출 | 아래 6절 도메인 지도 → 10절 흐름 | FE 화면/adapter에서 해당 route/service와 테스트까지 추적 |
| 스키마/운영 작업 | `backend/startup_guard.py:16`, `backend/migration_runner.py:25`, `backend/tools/` | 읽기 전용 부트 검증과 명시적 운영 변경의 분리 |

처음부터 `backend/app.py` 전체를 읽으면 앞부분의 긴 SQLite 호환 migration에 먼저 들어간다. 앱 조립을 이해하려면 **`create_app()`부터** 읽고, SQLite 호환 동작이 필요할 때 앞부분으로 돌아간다.

## 3. 기술 스택과 저장소 배치

### 기술 스택

| 영역 | 저장소에서 확인한 구성 | 근거 |
|---|---|---|
| 웹 | React/React DOM `^19.2.0`, React Router `^7.13.0`, JS/JSX, CSS, react-calendar | `frontend/package.json:22`, `frontend/src/App.jsx`, `frontend/src/index.css` |
| 빌드 | Node `>=22.12.0`, Vite `^7.3.7`, React plugin, ESLint | `frontend/package.json:6`, `:35`; `frontend/vite.config.js:4` |
| 웹 인증 | `@supabase/supabase-js ^2.117.2`, Turnstile 위젯 | `frontend/package.json:26`, `frontend/src/services/supabase.js:46`, `turnstile.js:1` |
| API | Flask `3.0.0`, Flask-SQLAlchemy `3.1.1`, Flask-Migrate `4.0.5`, Flask-Cors `6.0.2`, PyJWT `2.10.1` | `backend/requirements.txt:2` |
| DB | SQLAlchemy ORM, PostgreSQL 드라이버 psycopg `3.2.10`, Alembic; SQLite 호환·테스트 경로 | `backend/requirements.txt:15`, `backend/extensions.py:3`, `backend/config.py:190` |
| 파일·추출 | boto3/S3 호환 R2 client, FFmpeg, DB 상태 기반 프로세스 내 thread | `backend/storage.py:6`, `backend/media_processing.py:45`, `backend/Dockerfile:6` |
| BE 실행 | Python `3.13-slim` 컨테이너, Gunicorn factory | `backend/Dockerfile:1`, `:20` |
| 테스트 | pytest, Node 내장 test runner, jsdom, Testing Library; 일부 로컬 PostgreSQL 통합 테스트 | `backend/pytest.ini`, `backend/tests/conftest.py`; `frontend/package.json:12`, `:35` |
| 네이티브 코드 | Capacitor `^8.5.0`, Android Java, iOS Swift 업로드 wrapper | `frontend/capacitor.config.ts`, `frontend/android/`, `frontend/ios/` |

`^` 버전은 package에 선언된 범위다. 설치·배포된 exact 버전을 의미하지 않는다. TypeScript 의존성과 `capacitor.config.ts`가 있어도 웹 도메인 전체가 TS interface 기반인 것은 아니다.

### 최상위 디렉터리 지도

| 위치 | 책임·자료 성격 |
|---|---|
| 저장소 루트 `AGENTS.md` | 작업·권한·배포 경계와 도메인 문서 참조 규칙. 사람/Agent의 공통 작업 지침 |
| 저장소 루트 `README.md`, `docs/` | 프로젝트 소개와 이전 시점의 아키텍처/운영 문서. 일부 설명은 현재 코드와 다름; 15절 참조 |
| 저장소 루트 `package.json` | 현재 `{}`. FE 앱의 실제 package나 npm workspace 정의가 아님 |
| `frontend/src/` | React 앱. 화면·context·hooks·API/전송 adapter·공용 UI·동일 위치 테스트 |
| `frontend/android/`, `frontend/ios/` | 네이티브 wrapper와 업로드·보안 저장소 구현/테스트. 웹과 실행 경로가 다름 |
| `backend/` | Flask 앱, flat Python 서비스 모듈, ORM·serializer·스토리지·worker |
| `backend/routes/` | v2와 legacy endpoint. 등록은 빈 `routes/__init__.py`가 아니라 `app.py`가 담당 |
| `backend/migrations/` | Alembic env와 revision chain. 현행 코드의 schema/security 계약과 연결 |
| `backend/tests/` | API·서비스·권한·업로드·migration·PostgreSQL 통합 테스트 |
| `backend/tools/` | 명시적 backfill·백업/복구 리허설·스키마 전환·R2 이관 도구. 일반 request path와 구분 |
| `docs/auth`, `docs/plans`, `docs/policies`, `docs/reports`, `docs/runbooks` | 앱 하위 설계·계획·정책·검증 기록·운영 자료. 계획과 구현 완료를 혼동하지 않음 |
| `k3s/` | placeholder 이미지/도메인·SQLite PVC를 포함하는 배포 초안. 현행 운영 정의로 단정 불가 |
| `.github/workflows/` (앱 내부) | 과거/대체 GitHub Pages workflow. Git 저장소 루트의 workflow 위치와 다름 |
| `.state/`, `backend/tools/.state/`, `.pytest-*`, 가상환경·빌드 캐시 | 로컬 실행/테스트/운영 증거. 제품 모듈이나 일반 온보딩 읽기 자료로 취급하지 않음 |

디렉터리 역할 근거: 실제 source/import, `frontend/package.json`, `backend/Dockerfile`, `backend/.dockerignore`, `k3s/{backend,frontend,configmap,pvc,ingress}.yaml`, 앱 내부 `.github/workflows/deploy.yml`.

## 4. 실행 Entry Point와 생명주기

### Frontend

`frontend/index.html` → `src/main.jsx:7`의 `initializeInviteUrlContext()` → `createRoot` → `StrictMode` → `BrowserRouter` → `App`.

`App.jsx:134`의 provider 순서는 다음과 같다.

```text
ServicePolicyProvider → AuthProvider → BandProvider → UploadProvider → AppRoutes
```

- 초대 URL 처리가 먼저 실행된다. 초대 fragment/복귀 context 처리 순서를 이해할 때 `services/inviteLinks.js`와 함께 본다.
- `GlobalShell.jsx`는 서비스 포털·안내·커뮤니티의 공통 shell이다. `/account`의 `BandsPage`는 자체 `bands-shell`/header를 사용한다. 이 두 shell과 `BandWorkspace`는 `components/layout/HeaderActions.jsx`의 밴드 전환·설정·계정 메뉴를 공유한다.
- `BandWorkspace.jsx`는 밴드 이름·로고·밴드 메뉴와 `Outlet`을 갖는 별도 shell이다. 밴드 관리·로고·대표 관리 화면은 모두 이 Outlet의 자식인 구조가 아니다.
- `/`는 비로그인 시작 화면이고 로그인하면 `/portal`, `/account`는 현재 `BandsPage`다. `/bands`와 `/band/:slug/*`에는 호환 redirect가 있다.
- 밴드 작업공간 browser route는 현재 `RequireAuth` 안에 있다. BE의 공개 GET 존재만으로 모든 FE route가 비로그인 허용된다고 해석하지 않는다.

근거: `frontend/src/App.jsx:96`, `components/layout/{GlobalShell,RequireAuth}.jsx`, `components/workspace/BandWorkspace.jsx:54`.

### Backend

컨테이너: `backend/Dockerfile:20` → `gunicorn ... app:create_app()` (8080, timeout 300). 직접 실행 경로: `backend/app.py:589` → factory → Flask 개발 서버.

`create_app()`의 독서 순서 (`backend/app.py:369`):

1. `FLASK_CONFIG` 클래스 선택 → 정책/Auth 설정 검증 → 장소 검색 guard·CORS 구성.
2. legacy·정책 before/after request hook 등록 → `db.init_app`·Flask-Migrate·오류 handler → blueprint 등록.
3. DB dialect 확인. PostgreSQL이면 `startup_guard`의 읽기 전용 검증을 **스토리지 초기화·자동화보다 먼저** 수행.
4. R2 client 초기화. PostgreSQL은 ready flag만 설정; SQLite만 baseline 생성·호환 migration 수행.
5. 설정에 따라 multipart 복구, Auth schema 검증, 음원 추출/삭제 worker 시작.

`PG_AUDIO_WORKER_ENABLED`, `PG_MULTIPART_RECOVERY_ENABLED`, `PG_R2_DELETION_WORKER_ENABLED`는 서로 독립적이다. 코드나 worker 클래스가 존재한다고 실행 중인 것은 아니다 (`backend/config.py:137`, `backend/app.py:570`).

## 5. API 지도와 응답 경계

`B = /api/v2/bands/{bandId}`. FE slug는 먼저 해석하고 콘텐츠 요청에는 정수 bandId를 사용한다.

| API 영역 | 읽을 파일 | 주요 경로와 역할 |
|---|---|---|
| 계정·정책 | `backend/routes/auth.py` | `/api/v2/me`, `/policy`, `/me/policy-acceptance`, `/me/upload-policy-acceptance` |
| 밴드·가입·관리 | `backend/routes/bands.py` | 내 밴드, 공개 검색, by-slug, introduction, 생성 요청/platform 승인, memberships, join/leave/remove, ownership-transfer, profile, 관리 조회 |
| 콘텐츠 읽기 | `backend/routes/content_reads.py:33` | `B/songs`, `/rehearsals`, 하위 media/comments, `/featured-media`, `/members`, `/personal-logs/...`, `/gallery`, `/announcement`, `/suggestions`, `/dashboard` |
| 콘텐츠 쓰기 | `backend/routes/content_writes.py` → 아래 도메인 서비스 | 요청 파싱·인증 진입·기존 envelope/상태·private 헤더를 소유하고 업무 트랜잭션에 위임 |
| 자기 프로필 | `backend/routes/member_profiles.py:7` | `GET/PATCH B/members/me` |
| 가능 일정 | `backend/routes/availability.py:10` | `B/availability`, `/me/batch`, 날짜 초기화·메모·전체 reset |
| 초대 링크 | `backend/routes/invite_links.py:14` | `/api/v2/invite-links/{preview,resume-context,resume,accept}`, `B/invite-links` 발급·목록·폐기 |
| 업로드 | `backend/routes/uploads.py` | v2 direct·multipart 세션과 gallery direct; 같은 파일에 legacy 업로드 경로도 있음 |
| 삭제·작업 조회 | `backend/routes/deletion_tasks.py:213` | `DELETE B/media/{id}`, `/personal-logs/{id}`, `/gallery/{id}`; deletion-task 조회·platform 작업 |
| 장소 검색 | `backend/routes/bands.py` → `place_search_service.py::search_places_for_member` | `B/search-places`; 활성 밴드 멤버 검사·요청 예산·NAVER 검색 조정; `place_search.py`의 provider guard 재사용 |
| legacy·내부 조회 | 개별 `routes/{songs,members,...}.py`, `routes/migrations.py:13` | `/songs`, `/members`, `/uploads/...` 등 기존 계약; 별도 `/internal/migrations/r2-720p/inventory` |

표는 탐색용 그룹이다. 예를 들어 `B/media/{id}`와 `B/personal-logs/{id}`에 단독 GET detail이 있다고 가정하면 안 된다. 목록·processing·comments와 DELETE 경로가 각각 별도로 있다.

- API base URL 설정은 `services/apiConfig.js`에 있고, `services/api.js`는 legacy import 호환을 위해 `API_URL`을 재수출한다. v2 전송 client, 업로드·초대·정책 등 공통 소비자는 설정 모듈을 직접 참조한다. 환경변수와 fallback은 이곳에서 확인한다.
- FE의 주된 v2 전송 경계는 `services/authApiClient.js:40`. Bearer 부착, 선택적 인증, 401 시 공유 refresh 1회, AbortSignal, `ApiRequestError`를 담당한다.
- 경로와 payload의 지도는 `services/bandWorkspaceApi.js:5`. 업로드·초대·로고는 해당 전용 adapter도 확인한다.
- 응답은 주로 Python dict → `jsonify`다. 전역 DTO 타입이나 모든 응답을 검증하는 단일 FE schema layer는 없다.
- v2 콘텐츠는 `backend/v2_content_serializers.py`, `v2_remaining_read_serializers.py`, 밴드/가입 관계는 `band_service.py:129` 이후 serializer를 읽는다. 목록 paging, 단일 항목 envelope, mutation 응답은 endpoint별로 다르다.
- 공통 오류는 `backend/errors.py`를 보되 Auth error 등 별도 handler도 있다. 모든 오류가 한 JSON 형태라고 단정하지 않는다.
- v1은 `/api/v1` prefix가 아니라 **버전 prefix 없는 경로**다. `legacy_scope.py`, `legacy_read_visibility.py`가 단일 import 밴드에 바인딩한다. v2의 URL만 바꾼 alias가 아니다.
- v1 쓰기 중단은 `LEGACY_V1_WRITES_ENABLED`와 정책 gate에 연결된다 (`backend/app.py:410`). 구 초대 코드와 공개 장소 검색처럼 별도로 410 처리하는 경로도 있다. 현재 환경의 flag 값은 확인 필요다.

## 6. 도메인별로 따라갈 코드

이 표는 모델 필드를 재설명하지 않는다. 관계·업무 정의는 `DOMAIN_GLOSSARY.md`, 실제 조회·쓰기 경로는 다음 파일을 읽는다.

| 도메인 | FE 시작점 / 공통 adapter | BE 시작점 / 서비스 / 모델 |
|---|---|---|
| 계정·정책 | `components/auth/{AuthPages,PolicyPages,PolicyAcceptanceGate}.jsx`, `context/{AuthContext,ServicePolicyContext}.jsx` | `routes/auth.py`, `auth_service.py`, `policy_service.py`; AppUser·PolicyAcceptanceReceipt |
| 밴드 탐색·생성·관리 | `components/bands/{BandsPage,BandOnboarding,BandCreationRequestsAdmin}.jsx`, `BandSwitcher.jsx` | `routes/bands.py`, `band_service.py`; Band·BandCreationRequest·BandMembership |
| 가입·초대·추방·탈퇴 | `BandIntroduction.jsx`, `BandJoinRequest.jsx`, `InviteLink.jsx`, `MemberRemoval.jsx`, `services/inviteLinks.js` | `routes/{bands,invite_links}.py`, `band_service.py`, `invite_link_service.py`, `invite_introduction.py` |
| 멤버 프로필·포지션 | `workspace/MemberProfile.jsx`, `MemberPositionPicker.jsx`, `memberPositions.js` | `routes/member_profiles.py`, `member_profile_service.py`, `voc_validation.py`, `join_name.py`; Member |
| 밴드 대시보드 | `dashboard/BandActivityDashboard.jsx`, `workspace/bandDashboardState.js` | v2 `routes/content_reads.py`의 dashboard 조립; 별도 legacy `routes/dashboard.py`와 구분 |
| 곡·미디어·대표 미디어 | `workspace/songs/{SongWorkspace,SongDetail,SongForm}.jsx`, `workspace/media/PlaybackMedia.jsx`, `workspace/featured-media/{FeaturedMediaWorkspace,FeaturedMediaEditor}.jsx`, `bands/RepresentativeMediaManagement.jsx`; 편집기 표시 helper는 `workspace/featured-media/featuredMediaPresentation.js` | `routes/content_reads.py`, `song_service.py`, `featured_media_service.py`, `media_access_service.py`; Song·Media; 두 v2 serializer |
| 투표·댓글 | `workspace/MediaEngagement.jsx`, `services/{mediaEngagement,mediaVoting}.js` | v2 route → `v2_vote_service.py`·`comment_service.py`; `comment_rules.py`의 공통 scoped target/tree guard; MediaVote·SongSuggestionVote·Comment |
| 다음 곡 제안·채택 | `workspace/suggestions/SuggestionsWorkspace.jsx` (`BandWorkspace.jsx`에서 import·재수출) | v2 route → `song_suggestion_service.py`의 CRUD·`proceed_song_suggestion`; `content_write_scope.py`의 활성 가입 scope; 채택 시 SongSuggestion → Song |
| 합주·공연 일정·장소 | `workspace/rehearsals/{RehearsalWorkspace,RehearsalDetail,RehearsalForm}.jsx`, `calendar/RehearsalTypeBadge.jsx`, `workspace/BandPlacePicker.jsx` | v2 read → `content_queries.py`; write → `rehearsal_service.py`; `voc_validation.py`, `place_search_service.py`, `place_search.py`; Rehearsal·rehearsal_songs |
| 가능 일정·개인 일정 메모 | `workspace/AvailabilityCalendar.jsx`, `workspace/availabilityState.js` | `routes/availability.py`, `availability_service.py`; BandAvailabilityDay/State·BandPersonalScheduleNote |
| 개인 연습 기록 | `workspace/members/{MemberDetailWorkspace,PersonalLogPlayback}.jsx`, 업로드 공용 계층 | v2 read/write·uploads, `v2_remaining_read_serializers.py`; PersonalLog |
| 사진·대표 사진·로고·공지 | `workspace/gallery/GalleryWorkspace.jsx` (`BandWorkspace.jsx`에서 import·재수출), `BandUploadInput.jsx`, `BandOnboarding.jsx`의 gallery manager, `BandLogoManagement.jsx`, `BandIntroduction.jsx`, `workspace/announcements/AnnouncementEditor.jsx`, `bandLogoApi.js` | v2 read·bands 관리 조회; `gallery_featured_service.py`·`band_logo_service.py`·`announcement_service.py`; GalleryImage·BandLogo·Announcement |
| 업로드·추출·재생 | `common/{FileUpload,UploadReview,UploadCenter,MediaPlayer}.jsx`, `hooks/useMediaUpload.js` | `routes/uploads.py`, `upload_authorization_service.py`, `upload_initiation_service.py`, direct/multipart state service, `upload_materialization_service.py`, `v2_upload_serializers.py`, `upload_record_service.py`, `upload_recovery_service.py`, `media_access_service.py`, `media_processing.py`, `storage.py` |
| 삭제·권리 차단 | `workspace/BandDeleteAction.jsx`, `bands/PlatformContentDeletion.jsx`, `services/deletionTasks.js` | 부모·댓글 삭제는 `song_service.py`·`rehearsal_service.py`·`comment_service.py`; 파일/플랫폼 삭제는 `routes/deletion_tasks.py`, `comment_rules.py`의 공유 comment tree guard, `deletion_service.py`, `deletion_worker.py`, `content_restrictions.py`, `content_restriction_cli.py` |

표의 `workspace/`, `bands/` 등 컴포넌트 경로는 `frontend/src/components/`, 서비스 경로는 `frontend/src/services/` 기준이다. 예를 들어 `AvailabilityCalendar`는 `.jsx`, `availabilityState`는 `.js`다. 이는 반복을 줄인 표기이며 별도 모듈 디렉터리가 있다는 뜻이 아니다.

`BandWorkspace.jsx`는 `AnnouncementEditor`, `FeaturedMediaEditor`, `SuggestionsWorkspace`, `GalleryWorkspace`, `SongWorkspace`, `RehearsalWorkspace`를 도메인 모듈에서 import하고 기존 export 이름도 재수출한다. `App.jsx`와 관련 테스트는 Song·Rehearsal 진입점을 직접 사용한다. 관리 화면은 편집기를 도메인 모듈에서 직접 import한다. `BandUploadInput.jsx`는 UploadContext가 있을 때만 `FileUpload`를 렌더링하는 leaf 경계로, provider가 없는 표면에서는 비활성 상태를 유지한다. Gallery 경계 회귀 테스트는 `components/workspace/GalleryWorkspace.boundary.test.js`에 있다. `WorkspaceContext.jsx`는 `WorkspaceContext/useWorkspace`를 소유하며 `useCollection.js`, `usePagedRead.js`, `WorkspaceWriteGuidance.jsx`는 workspace 본체와 분리돼 있다. `WorkspaceFeedback.jsx`의 `ErrorState`는 workspace와 대표 미디어 편집기가 공유하는 leaf UI다. 추출 모듈은 `BandWorkspace.jsx`를 역으로 import하지 않는다.

현행 `BandActivityDashboard`는 react-calendar를 직접 사용한다. `calendar/RehearsalCalendar.jsx`와 `RehearsalModal.jsx`는 legacy Dashboard 호출 경로에 있으므로 현행 workspace가 재사용하는 일정 폼으로 해석하지 않는다.

## 7. 데이터 접근·DB·마이그레이션

### 실제 계층

`backend/extensions.py:3`은 공유 `db = SQLAlchemy()`를 정의한다. `models.py`에는 **ORM 모델 36개와 연결 테이블 `rehearsal_songs`**가 있다. route와 service가 `Model.query`, `db.session`, SQLAlchemy select/text를 직접 사용한다. 독립적인 Repository 클래스·패키지 계층은 확인하지 못했으므로 구조도에 존재하지 않는 Repository를 추가하지 않는다.

- 공유 band content SQL query builder: `backend/content_queries.py`의 `_song_query`, `_song_graph_sql`, `_media_playable_sql`, `_media_pending_deletes_sql`, `_gallery_query`, `_featured_gallery_rows_query`, `_announcement_query`. Routes가 이를 import하고 route file은 endpoint 조립·pagination·serialization을 맡는다.
- 댓글 cascade 안전 검사: `backend/comment_rules.py`의 `_assert_comment_tree_safe`·`_comment_target_kind`·`_owned_or_404`. 일반 콘텐츠 삭제와 platform deletion route가 같이 사용한다. descendant 조회는 foreign-band 후손을 사전 필터링하지 않고 각 노드에서 소유 밴드·대상 일치를 검증한다.
- route별 페이지네이션·일부 eager loading 조립은 `routes/content_reads.py`에 남아 있다.
- 일반 v2 콘텐츠 쓰기의 트랜잭션은 각 도메인 서비스가 소유한다. 공통 active membership 재조회는 `content_write_scope.py::active_member_write_scope`, 값 검증은 `content_write_values.py`, 작성자/manager 수정 판단은 `content_write_rules.py`다. 밴드·프로필·가능 일정 서비스도 각자의 기존 트랜잭션을 유지한다. route의 인증 동기화와 업무 commit은 같은 작업으로 해석하지 않는다.
- HTTP JSON object parsing은 `backend/http_request_json.py:payload`에 있다. `routes/availability.py`와 `routes/member_profiles.py`가 이를 공유하며 route 간 import는 없다. 32 KiB 제한·중복 키 거절·object 형식·기존 400/413 응답은 이 경계의 계약이다.
- `_creator_is_scoped_sql`과 `_not_pending_delete`는 `backend/band_content_predicates.py`의 SQL 조건 helper다. `content_queries.py`, `band_logo_service.py`, 일부 read/management route가 직접 import한다. 이 모듈은 SQL 조건만 만들며 실행·commit·HTTP 응답 책임을 갖지 않는다.
- 소속 관계의 before-insert guard: `models.py:1144` 이후 `_assign_legacy_scope`와 `event.listen`. 명시적 membership/부모의 동일 밴드 검사와 legacy dual-write에 관여하므로 route만으로 쓰기 동작을 판단하지 않는다.
- FK·CHECK·고유/부분 인덱스도 동작의 일부다. owner 유일성, 계정에 연결된 프로필 유일성, 곡당 대표 미디어 하나, membership당 한 표 등의 제약이 있다.

### 업무 트랜잭션과 응답의 소유자

| 업무 | 현재 소유 서비스와 읽을 공개 함수 | 응답·실패 경계 |
|---|---|---|
| 곡·합주 | `song_service.py`·`rehearsal_service.py`의 create/update/delete | serialize/jsonify callback이 commit 전 실행. 부모 삭제의 terminal 세션·파트 정리와 audit도 같은 rollback 범위 |
| 댓글·답글 | `comment_service.py`의 create/update/delete, `comment_rules.py`의 tree/target 조회 | 대상·부모·후손의 밴드/종류 검증 후 변경; 삭제는 현재 actor를 잠금 재조회 |
| 제안·채택 | `song_suggestion_service.py`의 CRUD·`proceed_song_suggestion` | 채택의 Song 생성·제안 삭제·audit는 하나의 DB 트랜잭션 |
| v2 투표 | `v2_vote_service.py::set_media_vote/set_song_suggestion_vote` | target 잠금·savepoint·집계·audit 후 응답 callback, commit/rollback. legacy CAS는 `vote_service.py`에 별도 유지 |
| 공지·대표 사진 | `announcement_service.py`·`gallery_featured_service.py` | 응답 callback 실패 시 rollback; 대표 사진 교체는 기존 featured clear를 먼저 반영 |
| 곡 대표 미디어 | `featured_media_service.py::set_song_featured_media/clear_song_featured_media` | 서비스의 dict 준비·commit 후 route에서 jsonify. 응답에 private 재생 URL을 넣지 않음 |
| 밴드 로고 | `band_logo_service.py::set_band_logo` | 직렬화 실패는 선택 ID+URL null로 처리해 commit, route jsonify는 commit 후. 다른 서비스와 실패 의미가 다름 |
| 업로드 | authorization → initiation → direct/multipart → materialization → completion adapter | 외부 R2를 포함한 단계별 commit·재인가·HEAD·멱등 복구를 유지. 하나의 장기 DB 트랜잭션으로 묶지 않음 |
| 시작 복구 | `upload_recovery_service.py::recover_multipart_upload_sessions` | 앱 factory가 schema/flag gate 후 호출. multipart → 존재하는 direct table 순서로 기존 recover 함수를 조정 |

`media_access_service.py`의 prepare 함수는 이미 호출자가 인가한 레코드의 visibility/graph/restriction 또는 gallery URL 입력을 준비한다. 준비 결과 자체가 capability나 새 인가 증명은 아니다. metadata/기존 재검사/signing 위치와 3600초·300초 TTL, storage의 추가 guard를 유지한다. ORM 속성 접근은 lazy load를 일으킬 수 있다.

### PostgreSQL 시작 검증과 스키마 변경의 분리

`startup_guard.py:16`은 읽기 전용 snapshot으로 정확한 Alembic head, 테이블 구성, schema/security 계약, backfill 표식, 관계 일관성, owner와 브라우저 역할 격리 등을 검사한다. 실패하면 시작을 중단하며 자동 repair·stamp·upgrade하지 않는다.

변경 진입점은 `migration_runner.py:25`다. `migration_bootstrap.py:2042`의 `create_migration_app`을 사용해 DB/Migrate만 조립하고 routes·storage·worker는 시작하지 않는다. `upgrade`, `stamp-existing`, `tenant-backfill`은 서로 다른 명령이다.

`migration_bootstrap.py`는 과거 단계의 metadata와 현행 schema/security 비교를 함께 정의한다. `migrations/env.py`는 Flask-Migrate engine을 사용하고 `migrations/versions/`는 revision chain이다. 분석 시점 head는 `20261024_24`다. 모델 변경은 현행 ORM뿐 아니라 과거 계약·시작 검사·마이그레이션 테스트에도 영향을 준다.

SQLite 경로는 `app.py:129`의 호환 `_run_migrations`에 남아 있다. 테스트 fixture가 현행 metadata로 실행하는 `create_all()`과 제품의 PostgreSQL 시작은 다른 흐름이다 (`tests/conftest.py:48`).

## 8. 상태 관리와 갱신 경계

곡·합주 상세의 미디어 조회는 `components/workspace/media/useDetailMedia.js`가 공유하며, 소유 상세 컴포넌트의 workspace·band·resource key가 재생/댓글 상태와 재시도 예산을 함께 초기화한다. `useCollection`의 append, `usePagedRead`의 first-wins dedupe·404·abort, FeaturedMedia의 same-ID update는 별도 정책이다.

현재 웹은 Redux/Zustand/React Query 전역 store 구조가 아니라 **Context + 페이지별 state/ref + 명시적 reload/refresh**를 사용한다.

| 상태 경계 | 읽을 위치 | 저장·갱신하는 내용 |
|---|---|---|
| 로그인 세션 | `context/AuthContext.jsx:25`, `services/supabase.js:46` | session/user/loading/authGeneration, SDK auth change. 웹 로그인 상태는 localStorage에 저장 |
| 공통 정책 설정 | `context/ServicePolicyContext.jsx:8` | `/api/v2/policy`; focus/visibility 변경 시 시간 조건에 따라 재조회 |
| 내 밴드와 선택 | `context/BandContext.jsx:26` | activeBands/pendingMemberships/selectedBandId/membershipRevisions, 계정별 localStorage와 탭 간 signal, 진행 중 목록 요청 병합 |
| 현재 밴드 작업공간 | `workspace/BandWorkspace.jsx`, `WorkspaceContext.jsx`, `workspaceRouteState.js` | slug + user + membershipRevision의 remount key, provider value, AbortController, 요청 generation/paint gate |
| 콘텐츠 목록·상세 | `workspace/useCollection.js`, `workspace/usePagedRead.js`; Song 상세 상태는 `workspace/songs/`, Rehearsal 상세 상태는 `workspace/rehearsals/` | `useCollection`은 append 때 단순 결합; `usePagedRead`는 ID dedupe, generation gate, abort, 404 notFound를 유지. 정책을 하나로 합치지 않음 |
| 대시보드 | `BandActivityDashboard.jsx:125`, `bandDashboardState.js` | 월/선택일/페이지 집계, 응답 band_id/month 검증과 epoch/abort |
| 업로드 작업 | `context/UploadContext.jsx:21`, `services/{uploadJobRegistry,uploadCompletionStore,bandUploadTaskStore}.js` | route 밖에서 유지되는 controller, 계정별 작업 격리, 대상별 완료 알림. 작업 기록이 브라우저 File 저장을 뜻하지 않음 |

`AuthContext.user`는 Supabase session user이며 Flask AppUser DTO가 아니다. 내부 정수 AppUser ID, UUID, membership ID, 프로필 ID의 차이는 용어집 4.1절을 참고한다.

계정이 바뀌면 하위 band/upload 상태를 다시 만들고, band/membership이 바뀌면 workspace를 다시 만든다. 콘텐츠 목록 캐시와 선택한 밴드의 영속화는 같은 메커니즘이 아니다. 동시 token refresh는 `authApiClient.js:16`의 WeakMap으로 공유하며 갱신 후 요청은 최대 한 번 재시도한다.

## 9. 인증·인가·공개 범위의 독서 순서

1. **인증**: `auth_service.py:239`에서 Bearer, JWKS 공개키와 서명/issuer/audience/exp/sub/email을 확인한다. 이메일 확인에 필요하면 Supabase Auth user endpoint를 호출한다. 이후 `synchronize_user:287`이 AppUser를 동기화하고 `authenticate_request:321`이 요청 안에서 신원을 캐시한다.
2. **선택적 인증**: `band_access.py:79`에서는 Authorization이 없을 때만 방문자다. 잘못된 token을 방문자로 낮추지 않는다. token이 있는 GET도 AppUser 동기화를 수행할 수 있으므로 DB 쓰기가 전혀 없다고 가정하지 않는다.
3. **밴드 scope**: `RequestAudience/MembershipAudience/BandReadScope`와 `read_scope_for_band/visible_query`가 밴드·활성 membership·공개 범위·restriction을 쿼리에 반영한다. 다른 밴드의 객체나 접근할 수 없는 밴드는 정보 노출을 막는 404 경로로 처리한다.
4. **쓰기 권한**: `band_service.require_band_role`, `content_write_scope`와 도메인 서비스의 scope/lock, 기능별 최신 actor 검사가 함께 작동한다. `can_edit/can_delete/can_manage/can_feature`는 서버가 계산하며 UI가 신원만으로 추정하지 않는다.
5. **실제 공개 범위와 서명**: `content_visibility.py`가 부모 콘텐츠·연결 일정·밴드의 유효 범위 규칙을 소유한다. `media_access_service.py::prepare_media_access/prepare_personal_log_access/prepare_gallery_access`가 기존 접근 준비를 조정하고, 같은 모듈의 `media_playback/personal_log_playback/gallery_image_url`이 URL 발급을 조정한다. 공개/멤버 TTL 상수는 이 접근 서비스의 `PUBLIC_URL_TTL=3600`·`MEMBERS_URL_TTL=300`이다. 두 v2 serializer는 준비된 범위·재생 결과를 기존 JSON에 조합하며 `storage.generate_url`의 추가 차단 guard도 유지한다. caller의 인가·조회 조건과 이 접근 준비를 함께 읽는다.
6. **정책 gate**: `policy_service.py`와 `app.py:437`은 정책 준비 여부·활성 여부·계정 receipt·업로드 rights assertion을 구분한다. FE의 `RequireAuth → PolicyAcceptanceGate`는 해당 flag가 유효할 때만 제약을 적용한다.

전역 PlatformAdmin과 밴드 role admin은 다른 권한이다. 비가입 superadmin의 지정 관리 작업에는 독립적인 scope/endpoint가 있으며 일반 밴드 조회에서 자동으로 멤버 취급한다는 뜻이 아니다. 예: `routes/content_reads.py`의 featured management scope, `routes/bands.py:271`의 관리 catalog, `routes/content_writes.py:69`의 로고 작업.

## 10. 주요 데이터 흐름: 사용자 동작에서 UI까지

### A. 곡 조회와 미디어 투표

```text
사용자가 /bands/:slug/songs 진입
→ BandWorkspace GET by-slug로 밴드와 권한 확인
→ SongWorkspace / useCollection → authenticatedApi → GET B/songs
→ content_reads → BandReadScope + ORM query → serialize_song → JSON
→ entries/pagination state → 곡 펼치기 → GET B/songs/{id}/media → MediaCard
→ 추천/아쉬워요 클릭 → MediaEngagement → PUT B/media/{id}/vote {value}
→ content_writes의 요청 검증 → v2_vote_service.set_media_vote
→ 최신 scope, 부모 미디어 잠금, MediaVote CRUD
→ 집계 증감 + audit + serialize + commit
→ FE viewer_vote/media 반영 → 서버 목록 재조회 → 정렬 갱신
```

읽을 위치: `frontend/src/components/workspace/{songs/SongWorkspace,songs/SongDetail,media/PlaybackMedia,MediaEngagement}.jsx`, `workspace/media/useDetailMedia.js`; `backend/routes/content_reads.py`, `routes/content_writes.py`, `v2_vote_service.py`, `media_access_service.py`.

**실제 호출의 차이**: `backend/vote_service.py:25`는 legacy Song/Media의 optimistic CAS 공통 구현이다. v2는 `v2_vote_service.py`에서 처리한다. MediaVote는 membership ID와 그 namespaced hash를 사용하고 SongSuggestionVote는 user ID를 투표 신원으로 사용하며 현재 membership을 감사 문맥으로 보관한다. 두 신원 정책을 합치지 않는다. 항상 legacy service를 호출하는 구조로 그리지 않는다.

### B. 밴드 가입 신청·승인·멤버 프로필 생성

```text
사용자가 /bands/:slug/join 진입
→ BandIntroductionRoute → GET by-slug/:slug/introduction
→ 소개 + BandJoinRequest 폼 → name/introduction/positions
→ POST B/join-requests → routes/bands → band_service.join_request
→ BandMembership pending → JSON membership/created
→ submitted state + BandContext.reload → /api/v2/bands → 승인 대기 목록

관리자 심사 → PATCH /api/v2/memberships/{id}
→ approve_membership → active/rejected
→ active이면 activate_member_profile → Member 생성/연결 프로필 갱신
→ 임시 신청 자료 정리 → 응답 → 해당 화면 명시적 reload
```

읽을 위치: `frontend/src/components/bands/{BandIntroduction,BandJoinRequest}.jsx`; `backend/routes/bands.py:405`, `:448`; `band_service.py:303`, `:355`; `member_profile_service.py:67`. 초대 링크 경로는 `InviteLink.jsx → inviteLinks.js → routes/invite_links.py → invite_link_service.py`를 따로 읽는다. 인증 자체는 Supabase Auth가 처리한다.

### C. 파일 업로드·레코드 생성·음원 추출·재생

```text
파일 선택 / UploadReview
→ FileUpload → UploadContext / useMediaUpload → mediaUploadManager
→ bandUploadTransport → POST B/uploads/{direct|multipart}/initiate
→ uploads route의 입력 검증 → upload_initiation_service
→ upload_authorization_service의 활성 멤버·target 검사 + 사용/예약 quota + policy assertion
→ DirectUploadSession 또는 대상별 multipart session + capability + signed PUT URL
→ 브라우저 XHR로 R2에 직접 바이트 업로드(영상 전체를 Flask로 POST하지 않음)
→ direct complete 또는 parts/ack → multipart complete
→ capability/band/owner 재검사 + R2 HEAD 크기 검증 + 현재 membership 재인가
→ upload_materialization_service: 현재 소유자 재인가 + Media / PersonalLog 생성
→ direct/multipart state service: 완료 결과 기록(멱등) → v2_upload_serializers 호환 envelope
→ worker가 켜져 있으면: DB queued → 조건부 claim → 다운로드 → FFmpeg M4A → R2 → completed
→ FE processing poll → completion store → 대상 목록 갱신
→ media playback 조회 → media_access_service의 짧은 수명 signed GET URL → 영상 또는 RadioMode 음원 재생
```

읽을 위치: `frontend/src/context/UploadContext.jsx`, `hooks/useMediaUpload.js`, `services/{mediaUploadManager,bandUploadTransport,uploadTransport,uploadMediaResponse}.js`; `backend/routes/uploads.py`, `upload_authorization_service.py`, `upload_initiation_service.py`, `direct_upload_service.py`, `multipart_upload_service.py`, `upload_materialization_service.py`, `v2_upload_serializers.py`, `upload_recovery_service.py`, `media_processing.py`, `storage.py`.

지도에서 구분해야 할 경계:

- FE 정책은 영상 최대 1GiB이며 100MiB를 기준으로 direct/multipart를 선택한다 (`mediaUploadManager.js:6`). Flask 호환 요청의 `MAX_CONTENT_LENGTH=200MiB`는 같은 전송 경로의 제한이 아니다.
- multipart service는 `MultipartTargetSpec`으로 상태기계를 공유하지만 media/personal_log 테이블은 다르다. gallery에도 별도의 direct 대상 구조가 있다.
- DB와 R2는 하나의 원자적 트랜잭션이 아니므로 completing, 멱등 완료, timeout 후 HEAD 확인, cleanup 상태 등의 복구 로직이 있다.
- 전송 완료·음원 추출 완료·720p 이관은 다른 작업이다. 일반 추출 worker가 전체 자료의 720p 재변환 서비스는 아니다.
- UploadProvider가 route 밖에 있어 SPA 이동 중 controller를 유지할 수 있다. 웹 transport는 명시적으로 `supportsBackground:false`이며 새로고침 후 File을 복원할 수 없다. 창 닫기·OS 백그라운드 실행 보장이 아니다.

### D. 콘텐츠 삭제와 권리 차단

삭제 버튼 → `BandDeleteAction`/platform 화면 → DELETE/작업 API → `routes/deletion_tasks.py` → `deletion_service.py`에서 actor/대상/key 재검사 및 `R2DeletionTask` 생성 → 활성화된 `deletion_worker.py`가 lease/R2 삭제/최종 DB 정리 → 작업 조회와 UI 갱신.

위 작업 흐름은 파일이 연결된 Media·PersonalLog·GalleryImage 삭제에 해당한다. 곡·일정·댓글·곡 제안·공지·멤버 프로필에는 동기 DB 삭제 경로도 있으므로 모든 삭제가 R2DeletionTask worker를 거친다고 해석하지 않는다.

이는 `ContentRestriction`과 다른 흐름이다. restriction은 predicate·serializer·signing을 통해 차단된 콘텐츠를 숨기며 물리적 삭제를 뜻하지 않는다. 목록 조회와 추출 작업도 삭제 대기 상태를 확인하므로 삭제 모듈을 제거하면 다른 기능의 필터에도 영향을 준다. 근거: `backend/deletion_service.py:46`, `deletion_worker.py:21`, `content_restrictions.py:89`; FE `services/deletionTasks.js`.

## 11. 모듈 의존 관계와 공통 코드

```text
FE routes/components
  → Auth/Band/Upload/Policy Context
  → authenticatedApi + paths/payload adapters
  → workspace guards / presentation / merge helpers

Flask app factory
  → routes → domain services / ORM / serializers
           → auth_service / band_access / policy / restrictions
           → storage(R2)
           → DB의 upload/processing/deletion 상태
  → 조건부 audio/deletion thread 시작

migration_runner → migration_bootstrap + migrations + db
  (전체 웹 factory를 호출해서 DB를 올리는 구조가 아님)
```

의존 관계가 엄격한 단방향 계층으로만 구성되지는 않는다. route에 업무 로직과 ORM이 있고 service에도 트랜잭션이 있으며 model의 legacy serializer에는 지연 import가 있다. `storage.generate_url`도 내부에서 restriction 검사에 의존한다. 코드를 이해하거나 제거할 때 실제 호출과 동적 import를 추적하며 파일 접미사만으로 계층을 가정하지 않는다.

| 공통 코드 위치 | 공유하는 동작 |
|---|---|
| `frontend/src/services/apiConfig.js`, `api.js` | API_URL의 소유자는 apiConfig. authApiClient는 설정 모듈을 직접 import하고 api.js는 기존 소비자를 위한 호환 재수출 유지 |
| `authApiClient.js`, `bandWorkspaceApi.js` | Bearer/refresh/abort, v2 경로와 payload |
| `workspaceRouteState.js`, `bandDashboardState.js`, `availabilityState.js` | 요청/paint gate, 페이지 병합, 가능 여부 집계. 일반적인 서버 캐시가 아님 |
| `common/MediaPlayer.jsx`, `FileUpload.jsx`, `UploadCenter.jsx`, `UploadReview.jsx` | 여러 화면의 재생·업로드·업로드 정책 확인 |
| `MemberProfile.jsx`, `MemberPositionPicker.jsx`, `rehearsalTypes.js`, `RehearsalTypeBadge.jsx` | 프로필·가입 신청·달력/대시보드의 공통 표현 |
| `HeaderActions.jsx`, `BandSwitcher.jsx`, `BandDeleteAction.jsx`, `safeExternalUrl.js` | 공통 헤더 메뉴·shell 간 이동·삭제 동작·안전한 외부 링크 |
| `backend/band_access.py`, `legacy_scope.py`, `content_restrictions.py` | 서로 대체할 수 없는 종류별 scope와 제한 |
| `backend/validators.py`, `voc_validation.py`, `join_name.py` | 파일/요청·포지션/일정 유형·가입 이름 검증 |
| `backend/multipart_upload_service.py`, `direct_upload_service.py`, `upload_record_service.py` | 대상별 상태기계·검증된 레코드 생성의 공통 기반 |
| `backend/content_write_scope.py`, `content_write_rules.py`, `content_write_values.py` | 활성 가입 scope 재조회·작성자/manager 수정 규칙·공통 값 검증 |
| `workspace/media/useDetailMedia.js`, `services/uploadMediaResponse.js` | 곡/합주 상세의 미디어 조회 수명·여러 업로드 응답 형식의 순수 변환 |
| `backend/content_visibility.py`, `media_access_service.py`, `v2_*serializers.py`, `storage.py` | 유효 범위 규칙 → 접근 준비·TTL/signing 조정 → JSON 조합, storage 추가 guard |

## 12. 기능 추가·제거 시 확인할 위치

이 절은 리팩터링 제안이 아니라 현재 의존 관계를 따라가는 탐색 목록이다.

| 변경 대상 | 일반적으로 확인할 현재 위치 |
|---|---|
| 페이지·내비게이션 추가 | `App.jsx` route → `GlobalShell` 또는 `BandWorkspace` 메뉴 → 해당 컴포넌트, RequireAuth/PolicyGate 적용 여부 |
| 특정 도메인의 필드·동작 추가 | 해당 workspace/form과 payload → route 허용 필드/검증 → 도메인 service/ORM → serializer → 관련 테스트 |
| DB 필드·제약 추가 | `models.py`, revision, `migration_bootstrap.py`의 과거/현재 계약, `startup_guard.py` 검사, backfill/마이그레이션 테스트 |
| 권한·공개 범위 동작 | FE Auth/Band 경계, 서버 scope/capability, 읽기·쓰기 handler, 부모/자식 visibility, restriction, signing, 직접 관리 route |
| 업로드·파일 형식·크기·표시 | file picker → UploadContext → manager/transport → uploads → session/record → worker/storage → serializer/player → 프로토콜에 영향이 있으면 native 계약 |
| 가능 일정과 확정 일정 | `AvailabilityCalendar/availabilityState`와 `RehearsalWorkspace/rehearsalTypes`를 구분해서 조회. 두 도메인은 같은 테이블을 쓰지 않음 |
| 기능 제거 | UI 진입점/route/import와 공유 컴포넌트 → FE adapter/context 영속 key → BE 등록/route/service → worker/도구 → serializer 필터 → FK/인덱스/과거 migration → 테스트 |
| 파일 콘텐츠·밴드 관계 삭제 | 버튼 숨김·탈퇴·접근 차단·soft delete·R2 물리 삭제는 다른 동작. 용어집과 delete/leave/remove 관련 service 확인 |
| legacy API 중단 | `app.py` hook, `legacy_scope/read_visibility`, 구 serializer, api.js 호출부, native protocol, 로그·호환 테스트 |

삭제·이동 전에는 함수/컴포넌트 export의 호출자와 App/blueprint 등록 지점부터 역으로 검색한다. 현재 App이 직접 import하지 않는다는 이유만으로 미사용이라고 판단하지 않는다. 공유 UI·지연 import·worker·운영 도구가 참조할 수 있다.

### 변경 영향도가 큰 핵심 파일

- `backend/models.py`: ORM·관계·제약·before-insert·legacy serializer·migration metadata의 공통 의존점.
- `backend/app.py`, `config.py`, `startup_guard.py`, `migration_bootstrap.py`: 시작 순서·DB 보안 계약·환경 flag·과거 schema.
- `backend/auth_service.py`, `band_access.py`, 두 v2 serializer, `content_restrictions.py`: 신원·밴드 간 격리·부모/자식 공개 범위·URL 발급.
- `backend/upload_authorization_service.py`, `upload_initiation_service.py`, `upload_materialization_service.py`, direct/multipart state service, `storage.py`: 재인가·quota·DB/R2 부수효과·timeout·복구가 만나는 지점.
- `frontend/src/App.jsx`, 네 Context, `BandWorkspace.jsx`, `authApiClient.js`: provider 수명·route·밴드/계정 경계·동시 요청·갱신.
- `frontend/src/hooks/useMediaUpload.js`와 UploadContext: 전송 controller 수명과 페이지를 넘나드는 cancel/poll/completion 상태.

## 13. 외부 시스템과 실행 경계

| 외부 시스템 | 연결 코드·설정 | 이번에 확인한 범위 |
|---|---|---|
| Supabase Auth | FE `services/supabase.js`와 AuthPages; BE `auth_service.py`, `config.py:17` | 로그인/이메일 확인/refresh는 SDK와 Auth가 처리하고 Flask가 JWT 검증·내부 계정 동기화. FE가 `.from()`/`.rpc()`로 도메인 테이블에 직접 접근하는 경로는 확인하지 못함 |
| PostgreSQL/Supabase DB | `config.py` DATABASE_URL, `extensions.py`, startup/migration 코드 | Flask가 SQLAlchemy로 접근. 브라우저 DB 역할 격리는 schema/startup 계약으로 검사. 실제 DSN·현재 schema는 승인된 운영 확인 필요 |
| Cloudflare R2 | `storage.py`, upload/delete/processing 서비스, S3_* 설정명 | boto3 S3v4, GET/PUT 서명·HEAD·multipart·다운로드/삭제. 공개 URL은 DB의 영구 식별자가 아님 |
| Cloudflare Turnstile | `turnstile.js`, `TurnstileChallenge.jsx`, `AuthPages.jsx` | widget token을 Auth 요청에 전달. 실제 콘솔 설정은 확인하지 않음 |
| NAVER Local Search / Maps | `place_search_service.py`, `routes/search.py`, `place_search.py`, `BandPlacePicker.jsx`, `calendar/LocationPicker.jsx` | 검색 proxy와 구 지도 컴포넌트가 함께 존재. 현행 밴드 폼 검색 진입점은 확인했으나 운영 key/허용 도메인은 미확인 |
| YouTube/외부 링크 | `services/youtube.js`, `safeExternalUrl.js`; legacy `songs/SongDetail.jsx:184`, `SongSuggestion.jsx:162` | 파싱·안전한 URL 처리와 legacy iframe 삽입은 저장 미디어와 다른 경로. 공용 `MediaPlayer`에는 iframe이 없으며 모든 링크를 R2에 내려받는 파이프라인도 아님 |
| 메일 | Supabase Auth 흐름, `BandRequestEmailOutbox`와 설정/정책 출처 | Auth 메일과 밴드 생성 요청 결정 메일 outbox는 다름. factory가 후자의 발송 polling worker를 등록하지 않으므로 테이블 존재만으로 발송 중이라고 볼 수 없음 |
| Fly.io | `backend/fly.toml`, Dockerfile | Flask/Gunicorn, nrt, shared CPU2/2GB, /data, HTTPS, autostop off 선언. 이번 실시간 조회 결과가 아님 |
| Cloudflare Pages | Vite 산출물, `AGENTS.md`의 기존 배포 약정 | 정적 SPA 배포 대상. 현재 Pages root/build/env·Git 연결은 외부 설정이며 저장소에 전체 콘솔 상태는 없음 |

`frontend/android/`와 `frontend/ios/`에는 durable 업로드·보안 session 저장 코드가 있지만 설치형 제품의 출시·운영 여부는 파일 존재만으로 증명되지 않는다. 현재 사용자 사용 범위는 웹이며 백그라운드 전송은 웹 transport의 실제 능력을 기준으로 판단한다.

## 14. 테스트 지도와 실행 경계

### Backend

- `backend/pytest.ini`: 기본 pytest 설정과 명시적 private PG integration marker.
- `backend/tests/conftest.py:16`: autouse R2 fake로 upload/download/HEAD/presign/multipart mock. `:48`의 app fixture는 TestingConfig SQLite memory·현행 metadata·legacy marker를 사용하며 운영 DB에 연결하지 않음.
- 인증/권한: `tests/test_auth.py`의 로컬 서명 key와 fake JWKS/Auth 응답. 밴드 access/read/write 등 테스트는 관련 도메인 이름으로 구분.
- 업로드: `tests/test_upload_contracts.py`, `test_multipart_uploads.py`, `test_band_upload_phase8.py`, `test_personal_log_uploads.py`, `test_upload_content_policy.py`.
- 실제 PostgreSQL fixture: `tests/test_preserve_identity_refresh_postgres.py:34`. 명시적 `RUN_PRIVATE_PG_INTEGRATION=1`과 준비된 PostgreSQL17 바이너리가 필요. 자체 임시 cluster·무작위 localhost port·종료 정리를 사용. 바이너리와 과거 private evidence는 일반 소스 읽기 범위가 아님.
- 시작/마이그레이션: `test_postgres_startup_guard.py`, `test_postgres_startup_guard_integration.py`, `test_admission_event_migration.py`. 마지막 테스트는 실제 PG의 rev23→24 마이그레이션·데이터/security·읽기 전용 시작 계약도 포함.
- 동시성: `test_band_availability_postgres.py`는 실제 service save/reset, `test_member_removal_postgres.py`, `test_band_leave_postgres.py`, `test_member_profiles_postgres.py`는 관련 도메인을 검사. `test_featured_media_postgres_concurrency.py`는 임시 테이블의 partial unique index backstop 경쟁 테스트이며 **전체 HTTP 대표 교체 동시성 테스트는 아님**.
- 곡 제안 채택: `test_suggestion_proceed_transaction.py`는 SQLite 실패 주입 5개·응답 폐기 후 재요청 1개와 실제 로컬 PG 17.11의 독립 연결 동시 proceed 1개·권한 변경 대기 경쟁 3개를 포함한다. PG 4개는 opt-in 격리 runner에서 실행해야 하며 compile-only `test_suggestion_announcement_p2.py`의 잠금 SQL 검사와 구별한다. 실행 수치와 fixture 한계는 Wave 5 기록을 따른다.
- operator: `test_featured_media_upgrade_tool.py`는 작업 gate·inventory 제약·로컬 PG의 실제 inventory 형태를 검사. 일반 API 테스트와 목적이 다름.

현재 추출 계약의 진입점은 `tests/test_{song_write,rehearsal_write,parent_delete,comment_write}_transaction_contract.py`와 대응 `*_postgres_contract.py`, `test_v2_vote_transaction_contract.py`, `test_v2_vote_postgres_contract.py`, `test_upload_recovery_boundary.py`다. 실제 파일명은 tests 목록과 함께 확인한다. HTTP/JWT·SQLite 실패 주입과 실제 PG row-lock 경합을 구별하며, PG의 frozen principal은 JWT 검증 증거가 아니다. `test_legacy_rehearsal_connection_lifecycle.py`는 오프라인 SQLite 연결이 GC 없이 닫히는지 검사한다.

### Frontend / Native

- `frontend/package.json:12`의 `npm test`는 `node --test --test-concurrency=1`을 사용하며 `src/services`, `src/context`, `src/components/{workspace,bands,app}`에 지정된 glob을 대상으로 함.
- `src/components/app/AppRoutes.dom.integration.test.js`는 Node+jsdom+Testing Library+MemoryRouter를 사용. Vite middleware/SSR로 컴포넌트를 로드하고 provider/fetch를 mock하며 운영 브라우저 E2E가 아님.
- `src/services/bandUploadTransport.test.js` 등은 요청 경로·capability·target 연결을 검사. 순수 함수 계약과 DOM integration 테스트가 함께 존재.
- Android `frontend/android/app/src/test/`, `src/androidTest/`와 iOS `frontend/ios/App/AppTests/`는 각각 wrapper/업로드 기술 계층 테스트이며 `npm test`가 자동 포함하는 suite가 아님.
- 개발/빌드/정적 검사의 진입점은 `frontend/package.json`. Vite에 backend dev proxy 정의가 없으며 API host는 `VITE_API_URL` 또는 localhost fallback에서 결정.

이 절은 테스트 위치와 방식을 기록한다. 이전 테스트 통과 수치를 이번 분석의 검증 결과로 재사용하지 않는다. 외부 설정이 필요한 전체 테스트 환경과 로컬 바이너리의 사용 가능 여부는 별도 확인이 필요하다.

## 15. 오해하기 쉬운 구조와 확인 필요 항목

### 코드에서 확인했지만 처음 읽을 때 오해하기 쉬운 부분

1. **한 저장소에 두 schema/runtime 경로가 있다**: PostgreSQL의 읽기 전용 시작 검증과 SQLite의 과거 확장 경로다. 시작할 때마다 운영 DB를 자동 변경하는 구조가 아니다.
2. **v1/v2와 serializer가 함께 존재한다**: 업무명이 같다는 이유만으로 공개 범위·신원·URL 계약을 합치지 않는다.
3. **이름만으로 계층을 추정하지 않는다**: 일반 v2 콘텐츠 쓰기는 서비스로 분리됐지만 read route의 pagination/집계 조립, 다른 기능 route의 일부 업무, 모델 legacy serializer와 storage guard는 남아 있다. 별도 Repository 계층은 없다.
4. **상태 경계는 remount/request generation/abort에 의존한다**: 중앙 캐시가 모든 무효화를 맡지 않는다. 목록 mutation 후 명시적으로 재조회하는 경우가 많다.
5. **R2와 DB는 함께 commit하지 않는다**: 업로드 완료/만료 복구·삭제 lease 작업이 시스템 간 상태를 다룬다.
6. **worker는 API 프로세스 안에서 flag에 따라 시작한다**: DB 행이 영속 상태이며 별도 Celery/Redis broker를 설정한 구조가 아니다.
7. **owner와 superadmin은 같은 membership 역할이 아니다**: 독립 관리 route와 일반 읽기 scope의 경계가 다르다.
8. **운영 도구의 rehearsal/staging은 제품 도메인명이 아니다**: backup rehearsal은 복구 연습이며 Rehearsal 일정이 아니다. staging 파일명은 현재 연결 대상이 테스트 전용임을 증명하지 않는다.

### 자료·환경상 확인이 필요한 내용

| 항목 | 이번 소스 조사만으로 확정할 수 없는 이유 |
|---|---|
| 운영 DB URL·migration head·worker/Auth/policy 설정값 | runtime 환경과 DB 상태에 의존. 비밀을 읽거나 운영에 접속하지 않음 |
| Fly release·실제 머신 수·리소스·volume | `fly.toml`은 선언이며 실시간 inventory가 아님 |
| Cloudflare Pages root/build/output/env·Git 연결 | 콘솔/외부 설정. 중첩 GitHub Pages workflow가 Pages 배포 정의는 아님 |
| SMTP·결정 알림의 실제 발송 상태 | outbox·설정 존재가 발송 활성화를 뜻하지 않음. 메일/콘솔 확인 미실시 |
| 설치형 앱 출시·실제 OS 백그라운드 능력 | native 코드·테스트는 구현 파일 존재만 증명 |
| 과거 도구가 다음 변경에도 유효한지 | operator는 특정 revision/evidence/pin에 묶이며 일반 migration 명령이 아님 |

기존 자료의 시점 차이: 루트 `README.md:9`, `:62`, `:65`에는 Fly SQLite·공통 인증 부재·migration 구축 필요라는 과거 설명이 남아 있다. `backend/requirements.txt`의 psycopg 용도 주석에도 과거 단계의 정보가 있다. 현재 구조는 `app.py`, `auth_service.py`, `startup_guard.py`, `migration_runner.py`의 실제 동작을 기준으로 읽는다. README·운영 문서·의존성 파일은 수정하지 않았다. `DOMAIN_GLOSSARY.md`와 이 지도의 현재 서비스 경로는 실제 소스와 대조했으며 DB/API·역할 이름의 호환성을 유지한다.

앱 내부 `.github/workflows/deploy.yml`은 Node20/GitHub Pages 절차를 포함하지만 현행 FE는 Node>=22.12를 요구하며 저장소 루트에는 `.github`가 없다. `k3s/`에도 placeholder·SQLite PVC가 남아 있다. 이를 과거/초안 자료와 구분하며 이번에 확인한 운영 orchestration으로 취급하지 않는다.

Architect Agent에게는 이 문서와 `DOMAIN_GLOSSARY.md`, 루트 `AGENTS.md`를 함께 제공한다. 이 지도에서 필요한 구현·테스트를 선택해 깊이 읽고 실행 환경을 별도로 확인한다. 이 문서 자체는 코드·DB·R2·권한·인프라 변경을 승인하지 않는다.
