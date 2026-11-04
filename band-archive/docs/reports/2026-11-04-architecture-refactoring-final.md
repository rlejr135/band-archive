# 아키텍처 리팩터링 최종 보고서

완료 기준일: **2026-11-04 KST**
최초 리뷰 기준: `b2b5610` · 원계획/보완서: 2026-10-25
최종 제품 커밋: **`d5cae8795f9916ea68ba963ad07d10c3af58cb07`**
제품 `origin/master` 일반 push 완료 · 백엔드 **Fly v91 complete**

이 보고서는 계획에 따라 진행한 코드 경계 추출, 검증, 운영 반영과 조건부 보류 판단을 한곳에 기록한다. 문서 자체의 커밋은 제품 커밋과 구분한다. 작성 시점에는 최종 문서 커밋·push를 아직 수행하지 않았으므로 그 해시를 기재하지 않는다.

## 1. 최종 판정과 범위

> 2026-11-05 후속 주석: 아래 완료 주장은 2026-11-04 작성 당시의 역사적 판정이다. [검증 보완](2026-11-05-architecture-refactoring-verification-addendum.md)에서 A runtime은 환경 대기, B FE 실행은 통과했으나 PG·시각은 미확인이며 C에도 미확인 계약이 남아 있어, 현재 무조건적인 전체 완료 결론은 유지하지 않는다. 과거 검증 수치·날짜·배포 내역은 그대로 보존한다.

**이번 계획에서 정한 필수 추출·계약 검증·문서 갱신 범위를 완료했고 제품 배포를 확인했다.** 남겨둔 선택 작업은 아래 8절의 명시적 보류 판단이며, 구현 누락을 다음 요청까지 숨겨 둔 상태가 아니다. 실제 구조는 [Architecture Context](../../ARCHITECTURE_CONTEXT.md), 도메인·호환 명칭은 [Domain Glossary](../../DOMAIN_GLOSSARY.md), 계획 계약과 판단은 [원계획](../plans/2026-10-25-architecture-refactoring-plan.md)·[보완서](../plans/2026-10-25-architecture-refactoring-plan-addendum.md)에 있다.

유지한 기술은 React/Vite SPA, Flask, SQLAlchemy, Supabase Auth/PostgreSQL, Cloudflare R2와 기존 프로세스 내 worker다. 새 기능·스택 전환·전면 Repository 계층·브로커·TypeScript 전환·설치형 앱 출시를 추가하지 않았다. DB/API 이름·역할별 허용 범위·signed URL TTL과 파일 처리 상태기계는 기존 계약을 보존했다.

구현은 담당 subagent가, 총괄은 독립 리뷰·순차 검증·단위 커밋·일반 push·배포를 맡았다. 이번 마무리에서는 댓글 → 투표/시작 복구 → 문서 순서로 진행했으며, 관련 제품 단위를 통합한 뒤 전체 BE 검증과 운영 배포를 한 번의 최종 게이트로 확인했다.

과거 리뷰·Wave 보고서는 일부가 로컬 보조 기록이며 새 clone에 없을 수 있다. 이 보고서의 판단과 아래 코드·테스트·커밋·운영 증거만으로 결과를 읽을 수 있으며, 과거 보고서의 존재를 전제로 하지 않는다. 과거 보고서를 일괄 Git 추가하거나 덮어쓰지 않았다.

## 2. 계획 대비 구현 결과

| 계획 영역 | 현재 구현과 주요 소비자 | 완료 범위 |
|---|---|---|
| 작은 route 역방향 의존 | `backend/http_request_json.py`, `band_content_predicates.py`, `content_queries.py`, `comment_rules.py`, `place_search_service.py` | parser·SQL predicate/query·트리 안전 검사·장소 검색 조정을 실제 소유 모듈에서 사용 |
| FE 화면·재사용 경계 | `frontend/src/components/workspace/`의 songs/rehearsals/members/gallery/suggestions/featured-media/announcements 및 context/read/feedback | shell은 조립과 계정·밴드 수명 경계를 유지. 각 기능·관리 편집기는 독립 진입점을 사용하며 표시된 호환 re-export는 보존 |
| 공개 범위·재생 | `backend/content_visibility.py`, `media_sources.py`, `media_access_service.py`, 두 v2 serializer | graph/visibility·source 선택·접근 준비와 URL 발급 조정·JSON 조합을 구분. 기존 caller 인가와 storage guard 유지 |
| 일반 DB 유스케이스 | `song_service.py`, `rehearsal_service.py`, `song_suggestion_service.py`, `comment_service.py`, `announcement_service.py`, `featured_media_service.py`, `band_logo_service.py`, `gallery_featured_service.py`, `v2_vote_service.py` | 도메인별 잠금·현재 권한·변경·audit·commit/rollback 소유자를 명시. 서로 다른 응답 실패 의미 보존 |
| 업로드 단계와 복구 | `upload_authorization_service.py`, `upload_initiation_service.py`, `upload_materialization_service.py`, `upload_transaction.py`, `upload_recovery_service.py`, 기존 direct/multipart state service | 예약·claim·외부 I/O·실제 크기 확인·재인가·레코드 생성·완료 결과·복구 단계 유지 |
| 응답 호환 | `backend/v2_upload_serializers.py`, `frontend/src/services/uploadMediaResponse.js` | v1/v2·media/personal log/gallery·processing 응답 차이를 명시적 adapter로 보존 |
| 실제 중복 | `content_write_scope.py`, `content_write_values.py`, `content_write_rules.py`, `workspace/media/useDetailMedia.js` | 같은 규칙과 실제 공유 수명만 추출. 서로 다른 투표 신원·목록 병합 정책은 합치지 않음 |
| schema 선택 검토 | `migration_bootstrap.py`, `startup_guard.py`, migration chain | 현재 계약·읽기 전용 시작 검증을 보존하고 추가 파일 분리는 조건부 보류 |

## 3. 중요한 책임·실패 경계

### 일반 콘텐츠와 선택 관계

- 곡·합주·제안·댓글·공지·대표 사진·v2 투표의 해당 유스케이스는 기존 위치에서 응답 직렬화와 `jsonify` callback을 **commit 전에** 실행한다. callback·audit flush·commit 오류가 발생하면 업무 변경을 rollback한다.
- 곡·합주 부모 삭제는 미디어·악보·진행 중 업로드·오염된 교차 밴드 관계를 먼저 검사한다. 결과 없는 terminal 세션·파트 정리, secondary 관계 해제, 부모 삭제와 감사 기록이 같은 transaction에 속한다. 파일 삭제 worker를 대신 실행하는 경로가 아니다.
- 댓글·답글은 `comment_rules.py`의 같은 밴드·대상 종류·트리 안전 검사를 공유한다. media와 personal log를 하나의 모델로 합치지 않았다. 현재 actor를 잠금 재조회하는 삭제와 일반 생성·수정의 기존 차이도 유지했다.
- 밴드 로고는 직렬화 예외를 선택 ID와 `url:null`로 처리해 commit하고, route의 JSON 생성은 commit 후다. 곡 대표 미디어도 서비스의 dict 준비·commit 후 JSON을 생성한다. 이 두 경로를 일반 callback-before-commit 방식으로 임의 통일하지 않았다.
- owner와 전역 superadmin은 같은 멤버십 역할이 아니다. 현재 기능별로 존재하는 비가입 운영자 특례만 보존했으며, 리팩터링을 이유로 일반 밴드 쓰기 scope를 우회하지 않았다.

### 투표·접근·업로드

- `v2_vote_service.py`는 기존 알고리즘을 추출했다. MediaVote는 membership ID와 namespaced hash, SongSuggestionVote는 user ID를 신원으로 쓰고 현재 membership을 감사 문맥으로 유지한다. legacy의 낙관적 CAS는 `vote_service.py`의 별도 계약이다.
- `content_visibility.py`는 유효 공개 범위 규칙, `media_sources.py`는 원본/720p/M4A key 선택, `media_access_service.py`는 접근 준비와 URL 발급 조정을 소유한다. 공개 3,600초·멤버 300초 TTL과 반복 restriction/pending 검사를 유지한다. 준비 데이터는 새 capability가 아니며 ORM 속성 읽기의 lazy load 가능성도 남는다.
- `v2_upload_serializers.py`는 기존 `model.to_dict()`·completion payload의 envelope를 보존하는 호환 adapter다. tenant-aware 읽기 serializer와 같다고 설명하지 않는다. 완료를 승인하는 책임은 현재 세션·소유자·밴드·실제 크기 검증 경로에 있다.
- 업로드는 DB와 R2가 하나의 원자적 transaction이 아니다. 기존 중간 commit·capability·용량 예약·timeout 후 HEAD·재인가·멱등 완료와 복구 상태를 유지했다.
- `app.py`는 schema/flag gate 뒤 `upload_recovery_service.py::recover_multipart_upload_sessions`를 호출한다. 이 서비스는 multipart 복구 후 direct 테이블 존재 여부에 따라 기존 direct 복구를 호출하며 upload HTTP route에 의존하지 않는다. 구 schema에서 미래 컬럼을 조회하지 않는 gate도 보존했다.

## 4. 주요 커밋 추적

각 행은 관련 경계를 찾는 대표 커밋이며 전체 Git 이력을 대체하지 않는다.

| 제품 커밋 | 변경 |
|---|---|
| `21d2beb`, `6b0dae6` | 공통 parser/predicate/query/tree·API 설정 및 초기 FE 경계 |
| `282d9f1`, `f15bdf7`, `a919ffc`, `29b0c0e` | Member/PersonalLog/FeaturedMedia 및 Song/Rehearsal 화면·폼과 실제 소비 경계 검증 |
| `3edfc5a`, `033e5be` | 제안 채택 transaction·active scope, 명시적 band visibility snapshot |
| `3c7cdad`, `888bde4`, `6d3a2ae`, `3b5b5ce` | source·visibility·재생 URL 조정·접근 준비 경계 |
| `5d909aa`, `28a4d17`, `3287626`, `a83d443` | 업로드 완료 adapter·materialization·인가·예약 시작 유스케이스 |
| `7350270`, `d950285`, `f1a4b2f` | 장소 검색, 공통 상세 media 조회 수명, 순수 업로드 응답 adapter |
| `93f1b3a`, `3d3f1e1`, `564b36d`, `0861ad1` | 대표 미디어·제안 CRUD·공지·로고/대표 사진 |
| `6e15ce4`, `56c1b1e`, `bbb1dac` | 곡·합주 생성/수정·곡 연결 및 부모 삭제 |
| `184e472` | 댓글·답글 생성/수정/삭제 |
| `d5cae87` | v2 투표 transaction과 시작 업로드 복구 조정 |

마지막 두 단위의 계약 선행 커밋은 댓글 `2d4e1da`, 투표·복구 `6d1158d`다. 최종 제품 HEAD와 push 대상은 `d5cae8795f9916ea68ba963ad07d10c3af58cb07`로 일치한다.

## 5. 검증 결과와 재현 경계

최종 전체 BE: **1,738 passed · 978 warnings · 0 failed · 0 skipped**. pytest 1,610.87초, 격리 runner 경과 1,616.453초다. 이 숫자는 최종 제품 전체 실행 결과이며 과거 일부 통과 수를 합산하지 않았다. 별도 collect-only 실행의 수치를 새로 주장하지 않는다.

실제 최종 명령은 다음과 같다. 이는 검증 당시 준비된 로컬 가상환경·private runner·소유 PostgreSQL 환경을 사용한 기록이며, 새 clone에 로컬 `.state` runner·바이너리·가상환경이 포함된다는 뜻은 아니다. 재현 시 프로젝트 회귀 검증 skill에 따라 동일하게 격리 환경을 준비해야 한다.

```powershell
# working directory: E:\Anything\band-archive\backend
& '.venv-test/Scripts/python.exe' 'tools/.state/run_private_validation_20261017.py'
```

| 최종 실행 증거 | 값 |
|---|---|
| 로컬 출력 디렉터리 | `backend/tools/.state/validation-20261017-b2a15693e904465ab17e3d1e18d375f6` |
| stdout SHA-256 | `0506285aa93c062cba99962898d3329dc8721b899ac5410265262e1cc7642f76` |
| stderr SHA-256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`(빈 출력) |
| 로컬 DB | 소유 localhost PostgreSQL opt-in 통합 테스트 실행, 종료 후 해당 실행 소유 프로세스 0개 확인 |
| 로컬 runtime | Python 3.12.14 · SQLAlchemy 2.0.52 |
| 운영 build runtime | 기존 Python 3.13-slim · SQLAlchemy 2.1.6 · Gunicorn 26.2.0 |

검증 범위의 주요 진입점:

- 접근·재생: `test_band_read_scope_snapshot.py`, `test_content_visibility_contract.py`, `test_media_sources_contract.py`, `test_media_access_contract.py`, `test_access_preparation_contract.py`와 대응 boundary.
- 일반 쓰기·삭제: `test_{song_write,rehearsal_write,parent_delete,comment_write}_transaction_contract.py`와 대응 `*_postgres_contract.py`, `test_content_delete_policy.py`, `test_deletion_retry_contracts.py`.
- 투표·시작 복구: `test_v2_vote_transaction_contract.py`, `test_v2_vote_postgres_contract.py`, `test_v2_vote_service_boundary.py`, `test_upload_recovery_boundary.py`, `test_upload_materialization_recovery.py`와 startup guard 테스트.
- 업로드: `test_upload_authorization_contract.py`, `test_upload_initiation_contract.py`, `test_upload_initiation_postgres.py`, `test_upload_quota_postgres.py`, `test_upload_materialization_contract.py`, `test_upload_materialization_postgres.py`, `test_v2_upload_response_contract.py`.
- FE 경계·수명: 각 workspace boundary/DOM 테스트, `DetailMediaLifecycle.dom.test.js`, `services/uploadMediaResponse.test.js`. 기존 화면 추출 때 수행한 검증과 이번 최종 BE 전체 검증은 구분한다.

실제 PG 경합은 독립 연결과 `pg_blocking_pids`로 첫 Band 잠금 뒤 두 번째 요청의 대기를 확인한다. Python 스레드가 우연히 실행된 순서만으로 직렬화를 판정하지 않는다. 일부 PG 테스트의 immutable synthetic principal은 JWT 해결만 대체하며 실제 HTTP·DB 잠금·저장·audit·commit은 유지한다. 실제 JWT는 별도 HTTP/JWT 계약으로 확인한다. R2/signing·NAVER 등 외부 동작은 테스트별 fake/sentinel 범위를 명시하고 운영 서비스를 능동 악용하거나 파일을 변경하지 않았다.

### 발견한 실패와 보정

실패를 통과 결과에 숨기지 않았고, assertions·timeout·skip 조건을 약화하여 배포하지 않았다.

| 발견 | 원인·조치 | 확인 |
|---|---|---|
| 기존 오프라인 이전 도구의 전체 검증 1건 실패 | Windows에서 snapshot SQLite 연결이 GC까지 남아 임시 파일 삭제가 실패. `with sqlite3.connect`의 transaction context에 `closing()`을 추가해 명시 종료 | 별도 `9082831`, 강한 참조를 유지하는 연결 수명 테스트 추가. 관련 109개 재검증과 이후 전체 검증 통과 |
| 부모 삭제 집중 검증 3건 실패 | 새 test module alias `songs`가 fixture의 곡 목록 변수와 충돌 | alias를 `song_writes`로만 수정, assertions 유지. 실패 3개와 boundary 3개 재검증 및 전체 1,666개 통과 후 v90 배포 |
| 댓글 기준선 4건 실패 | PG personal-log fixture가 Member를 직접 생성해 현행 tenant guard와 충돌 | guard를 끄지 않고 Band→membership 잠금 후 `ensure_member_profile()` 정규 경로 사용. 제품 변경 전 기준선 재검증 |
| 투표·복구 집중 검증의 오래된 참조 9건 | 공통 값 검증이 이동했는데 suggestion 계약 테스트가 제거된 route namespace를 참조 | patch 대상과 직접 호출이 `content_write_values`의 실제 소유 모듈을 참조하도록 정렬. 기존 검증 의미 유지, 재검증 47개 통과 |
| 경계 추출 뒤 일부 정적 소비자 기대 불일치 | 실제 소비 서비스가 이동했으나 route 소비를 계속 기대 | 실제 소유 모듈의 import와 역의존 금지 검증으로 정렬. 허위 import를 되살려 테스트만 맞추지 않음 |

중간 집중 결과에는 181 passed 묶음, 마지막 투표·복구 집중의 117 passed·9 failed와 수정 후 47 passed가 있다. 최종 완료 판정은 이들 일부 결과가 아니라 모든 변경이 반영된 전체 1,738 passed와 아래 운영 확인을 기준으로 한다.

## 6. 최종 배포와 운영 확인

최종 제품 일반 push 뒤, 커밋된 backend만 LF archive로 준비해 Fly에 배포했다. 주요 12개 runtime/config/CA 파일의 bytes가 제품 커밋과 일치함을 확인했고, 기존 Fly 볼륨·리소스 선언과 strict config 검증을 통과했다.

| 항목 | 최종 확인 |
|---|---|
| 릴리스 | **v91 complete** |
| 이미지 | `registry.fly.io/band-archive:deployment-01M6K9R0A7RRYDCGVQBFGZX6E4` |
| 머신 | `080e444f9643d8`, app 1대, started·host ok·cordoned false |
| 리전·리소스 | NRT · shared CPU 2개 · 2,048MB |
| 머신 갱신 | 2026-11-04 21:51:06 KST (`2026-11-04T12:51:06Z`) |
| 배포 검사 | smoke·machine·DNS 통과 |
| `/members`, `/api/v2/public/bands` | 모두 HTTP 200, ACAO `https://band-archive.pages.dev` |
| Pages | HTML·`assets/index-DaEGnbzE.js`·`assets/index-B3J8fdwy.css` 모두 200, JS의 Fly API 대상 확인 |
| 새 시작 로그 | 해당 구간 18줄, error 0, Gunicorn 26.2.0·8080·worker 정상 |
| archive ZIP SHA-256 | `0E4B7A5813EF643832C2BFAECAE32F083088137F09F7C924CC678E1548F36C46` |

별도 Fly health check는 설정되어 있지 않으므로 배포 smoke와 공개 HTTP 응답을 상태 증거로 사용했다. 단일 머신 업데이트에 재시작 구간이 있을 수 있으며 무중단이라고 주장하지 않는다. 이번 최종 제품은 BE 변경이며 FE를 명시적으로 재배포한 결과가 아니다. Pages는 기존 라이브 번들 응답을 확인했다.

`apt`/`pip` build 단계는 cache를 사용했다. 기존 공개 CAPTCHA site key에 대한 Fly의 일반 경고가 있었지만 해당 설정을 변경하거나 비밀값을 출력하지 않았다. 운영에서 전체 pytest를 실행한 것은 아니며 로컬/운영 Python·SQLAlchemy runtime 차이는 남는다.

## 7. 보존한 운영 경계와 확인의 한계

- 운영 DB 데이터 이전·schema 변경·R2 원본 삭제·재변환·secret 변경·스케일/과금 변경·메일 발송 활성화를 수행하지 않았다. 이번 자동 배포 권한을 이런 작업의 승인으로 확대하지 않았다.
- 기존 사용자 변경 `AGENTS.md`와 `k3s/secrets.yaml`을 수정·커밋·덮어쓰지 않았다. force push·자동 rollback도 하지 않았다.
- 실제 이용자 계정으로 privileged mutation E2E를 실행해 운영 콘텐츠를 변경한 증거는 아니다. 로컬 HTTP/JWT/DB 회귀와 운영 공개 읽기 smoke를 구분한다.
- 전체 통과는 운영 공격·모든 보안/법적 준수·성능 향상을 보증하지 않는다. 요청 지연이나 메모리 감소는 따로 측정하지 않았다.
- native 코드가 저장소에 남아 있어도 TestFlight/Play Store 배포나 웹의 OS 백그라운드 업로드 보장을 이번 계획에서 추가하지 않았다.

## 8. 조건부 보류와 현재 남은 구조

> 2026-11-05 후속 주석: 아래 선택 보류 목록과 ‘미이행 필수 단계는 없다’는 문장은 작성 당시 판정이다. [검증 보완 C절](2026-11-05-architecture-refactoring-verification-addendum.md)의 미확인 사항은 이 선택 목록으로 옮겨 완료 처리하지 않는다. A runtime 환경 대기·B FE 통과/PG·시각 미확인·C 부분 해소 상태에서 최종 필수 충족 결론은 보류한다.

다음은 이번 필수 구현의 미완료 목록이 아니라 원계획의 조건부 선택·호환성 판단이다. 별도 필요와 승인이 생기기 전 자동 개발을 시작하지 않는다.

1. **schema 모듈 추가 분리 보류**: 현재/역사 metadata·RLS·grant·trigger·FK와 startup 검증이 연결되어 있다. 추가 탐색 비용 개선 근거가 확정되지 않은 상황에서 역사 revision 의미를 바꾸는 재배치를 하지 않았다. 원계획 7단계의 재검토 조건과 검증 게이트를 유지한다.
2. **단일 조회 hook 강제 통합 보류**: `useCollection`의 append, `usePagedRead`의 first-wins dedupe·404·abort, FeaturedMedia의 same-ID update·URL 갱신은 다른 정책이다. 실제로 같은 수명인 곡/합주 상세 조회만 `useDetailMedia`로 공유했다.
3. **소형 serializer 전면 분할·이름 변경 보류**: `v2_remaining_read_serializers.py`의 명시적 함수와 현재 DTO·API를 유지했다. 파일 이름을 맞추기 위해 도메인별 빈 파일이나 공통 DTO 체계를 만들지 않았다.
4. **정상적인 협력·호환 경계 유지**: `content_queries.py`의 다중 도메인 조회·집계, read route의 paging 조립, legacy 모델 serializer와 storage guard, shell의 표시된 호환 export가 남는다. 전 제품이 엄격한 순수 계층이 되었다고 표현하지 않는다.

따라서 이번 계획의 필수 범위는 구현·검증·제품 배포와 문서 정리까지 마쳤으며, 위 선택 판단을 다시 열기 위해 사용자의 다음 요청을 기다리는 미이행 필수 단계는 없다. 향후 실제 schema 탐색 비용·새 UI 정책·기능 계약이 달라지면 해당 경계만 별도 작업으로 검토할 수 있다.
