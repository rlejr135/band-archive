# 아키텍처 리뷰 대응 리팩터링 계획

작성일: 2026-10-25 · 현행 상태 대조: **2026-11-04**.

제품 기준 **`d5cae87`**. 전체 BE **1,738 passed·978 warnings·0 failed·0 skipped**, 제품 일반 push와 **Fly v91 배포·운영 공개 응답 확인을 완료**했다. 이번 필수 추출 범위와 조건부 선택 보류 판정은 아래 추적표, 실행·실패 보정·배포 증거와 한계는 [단일 최종 보고서](../reports/2026-11-04-architecture-refactoring-final.md)에 있다. 문서 커밋은 제품 커밋과 구분한다.

아래 단계의 안전 계약과 검증 게이트는 유지한다. 현재 구현 상태는 다음 추적표로 판단하며, 과거 Wave의 진행 중/대기 문구를 현재 상태로 중복 나열하지 않는다. 원본 리뷰와 기존 Wave 보고서는 역사 기록으로 보존한다.

과거 리뷰·Wave 보고서는 로컬 보조 기록이며 Git 추적 대상이 아니면 새 clone에 없을 수 있다. 아래 역사 링크는 추가 자료이지 실행의 필수 입력이 아니다. 현재 범위·안전 계약·단계 판단은 이 계획과 보완서·루트 용어집/지도·실제 코드에 담고, 이번 작업의 최종 실행 증거는 단일 최종 보고서에 모은다. 과거 보고서를 일괄 Git 추가하는 작업은 포함하지 않는다.

## 1. 결론과 범위

리뷰의 방향에 동의한다. 기술 스택을 교체하지 않고, **현재 동작을 고정한 뒤 라우트·화면의 내부 구현에 의존하는 코드를 작은 단위로 분리**한다. 파일 수나 줄 수가 아니라 한 기능을 수정할 때 다른 도메인을 함께 수정해야 하는 정도를 줄이는 것이 목표다.

- 유지: React SPA, Flask, SQLAlchemy, Supabase PostgreSQL·Auth, R2, 기존 worker와 업로드·삭제 상태기계.
- 이번 계획에 불포함: 새 기능, 화면 재디자인, 마이크로서비스·브로커·Kubernetes 도입, 전면 Repository 도입, 전역 상태 라이브러리·TypeScript 전환, 라이브러리 업그레이드.
- DB 테이블·컬럼·제약·API 경로·JSON 응답·역할 정책은 그대로 유지한다. 초기 실행 묶음에는 migration이 없다.
- 운영 DB 변경·데이터 이전, R2 쓰기·삭제, 재변환, 비밀 설정·과금 변경, 메일 발송 활성화는 포함하지 않는다. 필요해지면 해당 단계를 중단하고 별도 승인받는다.

공통 자료:

- 원본 리뷰: [2026-10-24-software-architecture-review.md](../reports/2026-10-24-software-architecture-review.md)
- 용어 기준: [DOMAIN_GLOSSARY.md](../../DOMAIN_GLOSSARY.md) 1절·관련 도메인·7절
- 탐색 지도: [ARCHITECTURE_CONTEXT.md](../../ARCHITECTURE_CONTEXT.md)
- 작업 지침: 저장소 루트 `AGENTS.md`

이하 코드 경로는 `band-archive/` 기준이다. 표와 본문에 적힌 기존 source 행 번호는 최초 분석 당시 위치이며 현재 위치로 간주하지 않는다. 미착수 단계의 새 파일명·폴더명은 후보이며, 구현 완료 경로는 진행 기록과 현재 코드에서 대조한다.

## 현재 구현 추적표

| 계획 단계 | 현재 소유 코드·계약 | 현재 판정 |
|---|---|---|
| 0 기준선 | `AGENTS.md`, 도메인 용어·기존 HTTP/DB/인가 계약, 테스트 단위 선행 커밋 | 진행한 단위마다 기준선·독립 리뷰·검증 적용. 최종 묶음 전체 BE 1,738 passed·Fly v91 확인 완료 |
| 1 작은 역의존 | `http_request_json.py`, `band_content_predicates.py`, `content_queries.py`, `comment_rules.py`, `services/apiConfig.js`, `place_search_service.py` | 해당 소비자 전환 구현; 시작 복구의 잔여 route 의존은 `upload_recovery_service.py`로 추출해 구현·집중·전체 검증 및 Fly v91 확인 완료 |
| 2 FE 기능 경계 | workspace 하위 songs/rehearsals/members/gallery/suggestions/featured-media/announcements, 공통 context/read/feedback | 화면·폼·관리 편집기 소유자 분리와 직접 import. shell 호환 re-export는 명시적으로 유지 |
| 3 접근·재생 | `BandReadScope.band_visibility`, `content_visibility.py`, `media_sources.py`, `media_access_service.py` | graph/정책과 source/signing 조정 분리. caller 권한·ORM lazy load·legacy/storage guard는 유지 |
| 4 일반 DB 업무 | Song·Rehearsal·SongSuggestion·Comment·Announcement·FeaturedMedia·BandLogo·FeaturedGalleryImage의 도메인 서비스, `v2_vote_service.py` | 기존 잠금·actor 재조회·audit·응답 실패 의미를 단위별 보존. 댓글·투표 포함 전체 BE 1,738 passed·Fly v91 확인 완료 |
| 4 업로드 단계 | `upload_authorization_service.py`, `upload_initiation_service.py`, direct/multipart state service, `upload_materialization_service.py`, `upload_transaction.py`, `upload_recovery_service.py` | 예약·완료·외부 I/O·재인가·복구를 기존 단계대로 분리. DB/R2를 단일 transaction으로 합치지 않음 |
| 5 응답·호환 경계 | `v2_upload_serializers.py`, `frontend/src/services/uploadMediaResponse.js`, 기존 콘텐츠 serializer | envelope·HTTP status·model.to_dict 호환 의미 보존. 새 API·DB 이름으로 전면 교체하지 않음 |
| 6 실제 중복 | `content_write_values.py`, `content_write_rules.py`, `workspace/media/useDetailMedia.js` | 실제 공통 규칙·수명만 추출. 목록 append/dedupe/동일 ID 갱신의 서로 다른 정책은 유지 |
| 7 schema 선택 검토 | `migration_bootstrap.py`, `startup_guard.py`, migration chain | **분리 보류**. 추가 탐색 비용 개선 근거가 확정되지 않았고 역사 revision·security snapshot 보존 범위가 큼. 현재 계약·시작 검사 구조 유지 |

이 표는 파일 추출만으로 모든 아키텍처 문제가 사라졌다는 뜻이 아니다. read query 집계·paging 조립, legacy 모델 serializer, storage의 차단 guard 등 남은 책임은 현행 지도에 명시한다. FE 조회 hook·소형 serializer를 이름 통일만으로 다시 분해/통합하지 않는다.

역사 기록: [원본 리뷰](../reports/2026-10-24-software-architecture-review.md), [Wave 1](../reports/2026-10-25-architecture-wave1-implementation.md), [Wave 2](../reports/2026-10-25-architecture-wave2-implementation.md), [Wave 3](../reports/2026-10-25-architecture-wave3-implementation.md), [Wave 4](../reports/2026-10-25-architecture-wave4-implementation.md), [Wave 5](../reports/2026-10-26-architecture-wave5-proceed-transaction.md), [Wave 6](../reports/2026-10-26-architecture-wave6-access-boundary.md), [Wave 7](../reports/2026-10-26-architecture-wave7-media-sources.md).

## 2. 리뷰와 현재 코드의 대조

리뷰 기준은 `b2b5610`이다. 아래는 그 지적과 현재 경계의 대조이며, P0 미발견은 장애·취약점 부재나 운영 안전 보증을 의미하지 않는다.

| 리뷰 항목 | 현재 코드의 대응 | 경계·남은 판단 |
|---|---|---|
| A 라우트 내부 재사용 | JSON/SQL/트리/query와 장소 검색·시작 복구를 전용 모듈로 분리 | blueprint 조립은 app 책임. 집중·전체 검증 및 Fly v91 확인 완료 |
| B 큰 workspace | 기능별 화면·폼·편집기, context/read/feedback·공통 상세 media hook | shell은 조립과 수명 경계, 호환 re-export 유지 |
| C 공개 범위·직렬화·스토리지 | visibility/source 규칙과 playback access service | caller 인가·반복 restriction·pending 검사·저수준 guard를 제거하지 않음 |
| D commit 책임 | 도메인별 DB use case, upload authorization/reservation/materialization/recovery | Logo/FeaturedMedia의 commit 후 jsonify 등 서로 다른 실패 의미를 명시 |
| E legacy/v2 | API 설정과 v2 완료 호환 serializer, FE 응답 adapter | v1 CAS와 v2 account vote, model.to_dict 호환은 다른 계약 |
| F 반복 조회 | `useCollection`, `usePagedRead`, `useDetailMedia` | append/dedupe/update·404·abort 정책이 달라 무리한 단일 hook 통합 보류 |
| G 테스트 결합 | 기존 통합 fixture와 실제 PG 경합 coordinator 재사용, 기능별 계약·경계 테스트 추가 | PG opt-in 실행 여부·mock 범위를 별도 기록. fixture 대규모 재배치는 하지 않음 |
| H migration 결합 | 현재/역사 schema·security 계약과 읽기 전용 startup 보존 | 조건부 선택 분리 보류; schema 변경·migration 실행 없음 |
| I 내부 이름 | 용어집과 실제 서비스 진입점 지도 갱신 | DB/API·호환 export는 유지, 전면 rename 없음 |

## 3. 유지할 안전 계약

### 접근·응답

- `AppUser`, `BandMembership`, `MemberProfile`(현재 모델 `Member`)을 구분한다. `Song`, `Media`, `SongSuggestion`, 각 Vote, `FeaturedMedia`를 합치지 않는다.
- 밴드 범위·부모 공개 범위·작성자 관계·차단·삭제 대기를 모두 보존한다. scope를 전역 ID 조회나 프런트 판단으로 대체하지 않는다.
- 기존 엔드포인트별 400/401/403/404/409/413, 오류 본문, cache/CORS 헤더를 고정한다. 모든 경로의 오류를 같은 코드로 맞추지 않는다.
- owner/superadmin 특례는 현재 기능별 서버 권한대로 보존한다. 모듈 공통화가 새로운 전역 우회 권한을 만들면 안 된다.
- 공개·멤버 미디어 URL의 TTL, 기존 제목 표시, 720p 우선·원본 삭제 후 재사용 금지·M4A 라디오 재생을 보존한다.

### 상태·동시성

- FE 계정·밴드·멤버십 revision 기반 remount, request generation, abort, 늦은 응답 차단을 보존한다.
- 업로드 provider/controller는 화면보다 긴 수명을 유지한다. 조회 hook에 업로드 작업 상태를 흡수하지 않는다.
- capability, 예약 용량, 완료 시 재인가, timeout 뒤 HEAD 검증, 멱등 완료, 복구 상태를 유지한다.
- 삭제 작업의 lease·retry·영속 기록 및 실제 FK·고유 제약을 유지한다.
- SQLAlchemy의 자동 transaction 시작과 SQLite/PG 잠금 차이를 고려한다. 단순히 모든 함수를 `session.begin()`으로 감싸지 않는다.

## 4. 실행 순서와 커밋 단위

각 번호는 독립적으로 검토 가능한 커밋 경계다. 실패하면 다음 단계를 시작하지 않는다. 동작 보강이나 버그 수정이 새로 필요하면 **기계적 이동과 별도 커밋**으로 분리한다.

### 0단계 — 기준선과 변경 금지선 고정 (A~I 공통)

1. 구현 시작 시 HEAD, 관련 호출·import, route map, 해당 테스트 수를 기록하고 현재 전체 BE/FE 테스트를 실행한다. 기준선 실패가 있으면 원인을 분류하고 먼저 처리한다.
2. 이동할 함수에 대해 성공·실패 응답, 권한, 공개 범위, 잠금 순서, 부수효과를 계약 테스트로 보강한다. R2는 fake, 동시성은 로컬 소유 PG를 사용한다.
3. G의 fixture 분리는 필요한 범위에만 적용한다. PG 프로비저닝·환경 격리·명시 opt-in·정리 책임을 유지하면서 `tests/support` 또는 명시 등록 fixture로 옮긴다. 테스트를 이동한 뒤 다른 테스트 파일에 재의존시키지 않는다.

완료 기준: 기존 통합·PG 테스트를 축소하지 않고, 이후 이동을 검증할 계약과 로컬 실행 경로가 확보돼 있다.

### 1단계 — 작은 BE 역방향 의존 제거 (A, G 일부)

| 커밋 | 변경 단위 | 목적·검증 |
|---|---|---|
| BE-1 | 완료: `backend/http_request_json.py`; `routes/availability.py`와 `routes/member_profiles.py`에서 직접 import | 32KiB·bounded stream·중복 키 거절·JSON object 제한·400/413 본문·인증 후 파싱 순서를 유지; 대상 100개 통과 |
| BE-2 | 완료: `backend/band_content_predicates.py`; read/write/bands의 사용처를 교체 | 함수는 SQL 조건만 반환. SQL 실행·commit·HTTP 문맥을 넣지 않음; 대상 44개 통과 |
| BE-3 | 완료 (`479c42e`): `_assert_comment_tree_safe`, `_comment_target_kind`, `_owned_or_404` → `backend/comment_rules.py` | 일반 삭제와 플랫폼 삭제 경로의 guard를 공유. foreign-band descendant를 먼저 필터링해 숨기지 않고 방문해 검증하며, cycle/대상-kind/소유 밴드 검사를 유지 |
| BE-4 | 완료 (`e2a8732`): 7개 builder → `backend/content_queries.py`: `_song_query`, `_song_graph_sql`, `_media_playable_sql`, `_media_pending_deletes_sql`, `_gallery_query`, `_featured_gallery_rows_query`, `_announcement_query` | query 조건·SQL 원형 유지. featured gallery는 audience visibility 판정 전 같은 밴드 후보를 정렬해 `LIMIT 2`로 확인 |

특히 `_featured_gallery_rows_query`는 **공개 범위로 거르기 전 같은 밴드의 대표 사진 집합을 검사**한다. 중복 대표 데이터가 있으면 임의의 공개 사진을 반환하지 않도록 현재의 pending-delete 조건, `id ASC`, `LIMIT 2`와 뒤따르는 audience-filtered query를 함께 보존한다. 일반 갤러리 query와 억지 통합하지 않는다.

댓글 트리 검사도 특수한 보안 계약이다. 자식 ID 조회에 band 조건을 먼저 붙이지 않고, 발견한 각 descendant를 `_owned_or_404`로 다시 읽어 같은 band·같은 대상인지 확인한다. foreign-band 후손을 선필터링하면 손상된 cascade를 발견하지 못할 수 있다. 이동은 traversal·lock·조회 방식을 바꾸지 않는다.

BE-3/4의 모든 확인된 소비자는 `comment_rules` 또는 `content_queries`를 직접 참조하며, 이 범위에서 route-private helper 재수출 shim은 두지 않았다.

완료 기준: 해당 공유 함수의 실제 구현이 HTTP route 밖에 있고, 모든 기존 소비자가 명시적인 공통 모듈을 참조하며 API/SQL 결과가 같다.

### 2단계 — FE 재사용 진입점·화면 분리 (B, E 일부, I 일부)

1. **FE-1 완료**: `services/apiConfig.js`가 `API_URL`을 소유하고 `api.js`는 호환 재수출을 유지한다. v2 client가 설정 모듈을 직접 참조한다. 환경변수명·fallback·URL 처리는 보존했다.
2. **FE-2 완료**: `AnnouncementEditor`는 `workspace/announcements/AnnouncementEditor.jsx`에 있고, `BandOnboarding`과 workspace가 이를 직접 import한다. props와 policy·generation·abort·403 재검증 동작은 유지했다.
3. **FE-3 완료**: `FeaturedMediaEditor`와 필요한 presentation helper는 `workspace/featured-media/`에 있다. 관리 화면과 workspace가 편집기를 직접 import하며 일반 재생 목록과 관리 후보 목록의 권한·조회 차이를 유지했다. production build 통과.
4. **FE-4 완료 (`88ce770`)**: `WorkspaceContext.jsx`, `useCollection.js`, `usePagedRead.js`, `workspaceReadSupport.js`, `WorkspaceWriteGuidance.jsx`로 workspace 기반과 원형 기능을 추출했다. route path, provider value, slug·user·membership revision remount key는 유지했다.
5. **FE-5 완료 (`2513504`)**: `SuggestionsWorkspace`와 그 내부 suggestion form/card를 `workspace/suggestions/SuggestionsWorkspace.jsx`로 추출했다. shell에서 직접 import하고 기존 export 계약을 유지한다.
6. **Gallery 후속 화면 추출 완료 (`6b0dae6`; 이 목록의 단계 번호)**: `GalleryWorkspace`를 `workspace/gallery/GalleryWorkspace.jsx`로 분리하고 `BandWorkspace.jsx`에서 기존 export 계약을 유지했다. `BandUploadInput.jsx`는 선택적 UploadContext가 있을 때 기존 `FileUpload`를 사용하고, provider가 없는 표면에서는 inert 상태를 유지한다. root boundary test는 `workspace/GalleryWorkspace.boundary.test.js`에 둔다. 이 표기는 FE 전체 계획이 6단계에서 끝났다는 뜻이 아니다.
7. 이후 기능별 추출은 `MemberProfile`·`PersonalLog` → `FeaturedMedia` 화면 → Song·Rehearsal 순으로 한 기능씩 진행한다. 곡/일정은 마지막에 각각의 명시적 form/detail/list로 나누고 같은 입력·전송 helper만 공유한다. `Rehearsal`에는 공연과 장소검색·가능 일정 재확인·연결 곡 선택도 포함된다.

주의:

- 편집기 두 개는 `useWorkspace`를 직접 쓰지 않아 전체 context 이동보다 먼저 추출할 수 있다. 하지만 shared helper를 다시 `BandWorkspace.jsx`에서 import하면 순환이 남는다.
- 현재 관리 화면의 스타일도 `BandWorkspace.jsx`의 CSS side-effect import에 기대는 부분이 있다. 추출 시 기존 CSS 로딩을 명시하고 PC/모바일 시각 검증을 수행한다. 이 단계에서 디자인이나 CSS 전면 분할을 섞지 않는다.
- 최근 정리한 `HeaderActions`, 설정의 네 메뉴, 계정 화면 anchor, 빈 밴드 안내도 회귀 검증에 포함한다. 이미 공통화한 헤더를 다시 구현하지 않는다.
- `BandsPage` → `AccountHubPage` 같은 이름 변경은 관련 경계 이동 때 별도 작은 커밋으로만 수행한다. URL·hash·호환 redirect를 바꾸지 않는다.

완료 기준: 관리 화면이 workspace 구현 파일을 import하지 않고, shell은 밴드 해석·권한 경계·탐색·Outlet에 집중한다. 기능별 화면은 shell을 역으로 import하지 않는다.

### 3단계 — 공개 범위·재생 규칙 분리 (C, E의 선행 조건)

**고위험 단계이며 앞 단계 완료가 자동 착수 조건은 아니다. URL 발급 경로 목록과 테스트를 검토한 뒤 시작한다.**

1. 먼저 `storage.generate_url`·`processing_fields`·각 `to_dict`·v2 serializer의 호출처를 목록화한다. v1 중첩 응답, v2 목록/상세/processing/업로드 완료, 갤러리·로고·대표 사진, 개인 기록을 빠뜨리지 않는다.
2. `BandReadScope.band_visibility`를 명시 필드로 표현하고, 생성 지점과 모든 소비자를 함께 전환한다. 기존 `object.__setattr__`를 없애기 위해 기본값으로 비공개를 공개 처리하는 변경은 허용하지 않는다.
3. 후보 `content_visibility.py`, `media_sources.py`로 입력 데이터의 공개 범위·관계 유효성·재생 소스 선택을 분리한다. ORM 관계를 읽는 함수까지 무조건 순수하다고 부르지 않는다. 필요한 그래프를 준비하는 조회와 DB 없는 판단을 구분한다.
4. 후보 `media_access_service.py`가 scope·관계·차단·삭제 대기·TTL을 확인한 뒤 signing을 호출하게 한다. serializer는 허용된 결과를 현재 JSON 형태에 맞춘다.
5. **기존 storage의 차단 guard는 먼저 삭제하지 않는다.** 모든 지원 URL 경로가 보호된 서비스를 거친다는 검증과 잔여 caller 정리가 끝난 뒤에만 저수준 adapter 경계를 분리할지 판단한다. legacy와 내부 작업용 경로도 명시적으로 분류한다.

검증: SQL 목록 필터와 객체 판단의 결과 일치, 부모/자식 PUBLIC·MEMBERS 조합, 다른 밴드의 깨진 연결, 차단·삭제 대기 콘텐츠의 **signer 호출 0회**, pruned 원본의 signing 0회, 720p·원본·M4A 선택, 기존 TTL·display_name. signed URL의 랜덤 값 대신 key·TTL·허용 여부를 비교한다.

완료 기준: 새로운 인가 우회나 URL 누출 없이 규칙·조회·접근 서비스·serializer·스토리지 역할을 구분할 수 있다.

### 4단계 — 유스케이스별 트랜잭션 책임 명시 (D)

1. route/service/helper별 commit·rollback·flush와 외부 I/O를 표로 만든다. 일반 DB 변경, 영속 outbox/삭제 작업, 파일 전송 상태기계를 따로 분류한다.
2. 첫 후보는 `proceed_suggestion_v2`의 **SongSuggestion 채택 → Song 생성 → 제안 제거**다. 현재 권한·필드 매핑·잠금·audit·정책 확인과 단일 DB 원자성을 유지한 채 route callback을 명시적 유스케이스로 옮긴다.
3. 일반 DB 유스케이스는 바깥 명시적 경계가 commit/rollback을 소유하고 하위 helper는 필요한 flush만 수행한다. 이미 transaction이 시작된 Flask/SQLAlchemy session 처리, 예외 변환, auth sync와의 관계를 먼저 확인한다. 기존 `commit=` 옵션을 일괄 삭제하지 않는다.
4. 가입·역할·탈퇴·프로필 등은 caller 전부를 조사한 후 유스케이스별로 전환한다. `band_service` 전체 commit을 한꺼번에 옮기지 않는다.
5. 업로드는 별도 후속 설계로 **claim 저장 → 외부 확인 → 재인가·생성 → 완료 기록**을 명시한다. 필요한 중간 commit, 예약 잠금, 결과 재조회·복구 상태를 유지한다. R2와 DB를 단일 transaction으로 감싸거나 긴 잠금을 유지하지 않는다.

검증: 중간 실패 시 Song만 생기거나 제안만 사라지지 않음, audit/outbox 동일 원자성, 중복 요청·동시성의 기존 결과, 직렬화 실패와 commit 실패의 구분. 업로드 단계 전환은 timeout/HEAD/실패 후 재시도·탈퇴 경쟁 테스트가 선행돼야 한다.

### 5단계 — BE 기능별 경계와 legacy/v2 adapter (A 잔여, E, I)

- 공통 콘텐츠 route의 각 handler와 유스케이스를 도메인 하나씩 옮긴다. blueprint 등록 중복·endpoint 이름·decorator 순서·schema readiness·private/no-store·optional auth를 보존한다.
- 모든 도메인에 무조건 `repository/service/DTO`를 만들지 않는다. DB 조회는 기능별 query가 SQLAlchemy를 직접 사용해도 된다.
- v2 업로드 응답을 명시적 serializer/adapter로 옮기되 기존 envelope·상태 필드·HTTP status를 유지한다. direct/media multipart/personal-log/gallery의 실제 차이를 먼저 계약표로 남긴다.
- FE `normalizeMedia`의 호환 분기를 임의 삭제하지 않는다. v1·웹·남아 있는 Android/iOS 코드의 사용처를 확인하고 경계 adapter로 이동한다. 설치형 앱 구현·배포는 이 계획에 추가하지 않는다.
- `v2_remaining_read_serializers`는 기능별 책임이 드러날 때 분리한다. 거대한 파일을 `common`이나 `utils`로 이름만 바꾸지 않는다.

### 6단계 — 검증된 중복만 통합 (F, G 잔여)

- 현재 `useCollection`은 단순 append, `usePagedRead`는 처음 항목을 보존하는 dedupe, 대표 미디어 병합은 같은 ID의 기존 값을 갱신한다. 이 차이는 현재 동작이며 즉시 같은 기본값으로 맞추지 않는다.
- 기능 추출 후 반복이 확인된 요청 수명·loading/error/pagination 부분만 작은 hook으로 공유한다. 응답 해석·append/replace·dedupe/update·404 의미·서명 URL 갱신은 명시적인 정책으로 전달한다.
- StrictMode, focus 재조회, 역순 응답, 계정·밴드 전환, 동일 ID의 갱신, 업로드 완료 구독, URL 갱신 중 페이지 추가를 검증한다.
- 순수 규칙 / 서비스+DB / HTTP 계약 / PG 잠금·제약·migration 테스트를 구분하되, 기존 통합 테스트를 mock 테스트로 교체하지 않는다. 파일 재배치 전후 수집 테스트 수와 fixture cleanup도 대조한다.

### 7단계 — schema 모듈 분리 여부 재검토 (H, I 잔여)

앞 단계가 안정되고 schema 변경 시 탐색 비용이 여전히 큰 경우에만 수행한다.

- 역사 snapshot, 현재 schema/security 계약, migration 앱 구성을 파일 책임으로 구분한다.
- 과거 Alembic revision ID·내용·기대 metadata를 보존한다. 과거 snapshot이 현재 모델을 import하도록 단순화하지 않는다.
- 빈 DB 및 지원 과거 revision에서 현재까지 upgrade, 현재 schema diff, RLS/grant/index/FK, startup의 fail-closed와 읽기 전용 동작을 로컬 PG에서 확인한다.
- 운영 migration 실행이나 스키마 변경은 이 코드 정리에 필요하다고 가정하지 않는다. 필요해지면 상세 변경안과 별도 승인을 받는다.

## 5. 실행 묶음과 병렬화

사용자 지시에 따라 남은 단위는 순서대로 연속 진행한다. 각 단위는 계약 테스트 기준선 → 담당 subagent 구현 → 독립 리뷰 → 집중 검증 → 단위 커밋 순서다. 같은 파일·API·잠금·상태 수명에 영향을 주는 변경은 함께 추측해 병렬 전환하지 않는다. 전체 테스트 등 무거운 검증은 순차 실행한다.

현재 댓글 다음 투표·시작 복구 경계를 전환했으며, 두 단위를 포함한 최종 전체 BE는 1,738 passed·978 warnings·0 failed·0 skipped이며 Fly v91 배포 확인을 마쳤다. 문서와 단일 최종 보고서에는 실제 결과와 조건부 보류 판단을 반영했다. 테스트가 실패하면 결과를 보존하고 원인·수정·재검증을 구분하며, 실패를 건너뛰어 배포하지 않는다.

성능 향상을 측정 없이 주장하지 않는다. 새 기능·정책·DB/API 의미 변경은 원래 범위에 포함하지 않으며, 검증 과정에서 발견된 독립적인 보정은 별도 커밋·회귀 증거로 구분한다.

## 6. 검증·배포 게이트

각 커밋 전에 변경 diff·import graph·관련 계약 테스트·정적 검사를 확인한다. 배포할 묶음의 마지막에는 `anything-regression-validation` 스킬로 영향받는 전체 BE/FE 테스트를 실행한다.

최소 기존 검증군:

- BE 기본 HTTP/권한: `tests/test_v2_content_reads.py`, `test_v2_content_writes.py`, `test_member_profiles.py`, `test_band_availability.py`, `test_band_logo.py`, `test_content_delete_policy.py`.
- BE 접근·미디어: `test_content_restrictions.py`, `test_v2_featured_media.py`, `test_v2_gallery_featured_write.py`, `test_legacy_dashboard_visibility.py`, `test_upload_content_policy.py`.
- BE 업로드/복구: `test_upload_contracts.py`, `test_multipart_uploads.py`, `test_personal_log_uploads.py`, `test_band_upload_phase8.py`, `test_durable_deletion_tasks.py`, 관련 PG 경쟁 테스트.
- BE schema: `test_migration_bootstrap.py`, `test_postgres_startup_guard.py`, `test_postgres_startup_guard_integration.py` 및 관련 실제 migration 테스트.
- FE: `BandWorkspace.dom.integration.test.js`, `BandOnboarding.dom.test.js`, `RepresentativeMedia*.dom.test.js`, `BandContext.dom.integration.test.js`, `HeaderActions.dom.test.js`, `BandsPage.dom.test.js`, `AppRoutes.dom.integration.test.js`, `UploadPolicy.dom.test.js`, services/context 전체.

테스트 이름은 출발점이며 전체 suite의 대체 목록이 아니다. 구현 시 최신 경로와 PG 테스트 포함 여부를 재확인한다. URL signing·저장소는 fake로 확인하고 운영 R2 파일을 전송·삭제하지 않는다. PG 환경 실패를 skip으로 우회하지 않는다.

FE는 lint·production build와 PC/모바일 mock 화면을 확인한다. 특히 편집기 추출 시 stylesheet 누락·키보드 조작·오류 상태를 검증한다.

구현 승인 후 릴리스는 기존 상시 승인과 해당 배포 스킬을 따른다. FE만 변경하면 BE 재배포하지 않는다. BE 변경이면 Fly의 HTTP/CORS/로그를, FE 변경이면 Pages의 실제 새 번들과 HTTP/CORS를 확인한다. 실패하면 다음 단계·push·배포를 중단하고 원인을 보고하며 force push·자동 롤백을 하지 않는다.

## 7. 완료·중단 기준

완료:

1. 제품의 cross-module `routes.*` private helper import가 정리한 범위에서 사라진다.
2. 관리 화면이 workspace 구현을 import하지 않고 도메인 컴포넌트를 직접 사용한다.
3. 공개 범위·재생·업로드·삭제·계정 전환의 계약 테스트가 전후 동일하게 통과한다.
4. commit 책임과 외부 I/O 단계가 호출자에게 명시된다.
5. `ARCHITECTURE_CONTEXT.md`·관련 용어집 경로가 실제 코드와 일치한다. 정상적인 기능 추가에 필요한 UI/검증/모델/테스트 수정까지 억지로 줄이지 않는다.

중단·별도 결정:

- 구조 이동 중 확인된 권한 누출·원자성 결함은 일반 리팩터링에 숨기지 않고 별도 결함으로 보고한다.
- 기능별 merge·query·lock 의미가 달라 동일 계약으로 만들 수 없으면 중복을 남기고 이유를 적는다.
- 운영 schema/R2/secret/재변환 변경이 필요하거나 테스트 실패를 설명할 수 없으면 다음 단계로 진행하지 않는다.

구체적인 소요 시간·테스트 수·성능 수치는 구현 기준선과 첫 묶음 결과를 본 뒤 산정한다. 이번 정적 계획에서 전체 안전성이나 속도 개선을 보증하지 않는다.

## 8. 후속 agent 지시 틀

```text
공통 읽기: 저장소 AGENTS.md, DOMAIN_GLOSSARY.md 1·관련 항목·7절,
  ARCHITECTURE_CONTEXT.md, 이 계획의 담당 단계
대상: <BE-1 / FE-2 등 하나의 커밋 경계>
기대 결과: 실제 호출자 전체를 교체하는 동작 보존 추출 + 계약 테스트
허용 경로: <담당 소스·테스트 파일>, 공통 문서는 읽기만
제외: API/DB/권한/화면 동작 변경, 타 agent 파일 수정, env·secret,
  운영 접속·DB/R2 쓰기·재변환·commit·push·배포
보존 계약: <상태 코드/잠금/TTL/remount/merge 등 담당 항목>
보고: 이동 함수·소비자·신규 의존·검증 결과·미확인점·문서 영향
총괄 승인 후: 리뷰 → 순차 검증 → 단위 commit → 필요한 릴리스
```

## 9. 외부 근거와 적용 한계

- [SQLAlchemy: Session/transaction 생명주기](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#when-do-i-construct-a-session-when-do-i-commit-it-and-when-do-i-close-it): 데이터 처리와 transaction 수명을 분리하는 원칙을 확인했다. 기존 Flask session과 다중 단계 업로드를 전면 교체하라는 뜻은 아니다.
- [Supabase: Data API 보안](https://supabase.com/docs/guides/api/securing-your-api): grants와 RLS는 서로 다른 경계다. 이 계획은 현재 Flask 경유 접근을 유지하며 브라우저의 도메인 테이블 직접 접근이나 grant 확대를 제안하지 않는다.
- Supabase changelog도 확인했지만 SDK·프로젝트 설정을 변경하는 작업은 이 계획에 포함하지 않는다. 실제 해당 변경을 구현할 때 최신 안내를 다시 확인한다.
