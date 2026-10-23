# 밴드 가입 프로필·일정 유형 VOC 계약

상태: Phase 1은 `origin/master`의 `7772534`, 백엔드 Fly.io v66 및 라이브 프런트엔드 확인까지 운영 완료했다. Phase 2 기능은 `9f28caa`에서 구현·검증됐고, 운영자 도구 보완은 `e0b8a456e2b0aea8adced7a4bbffe6ded2561b83`에 반영됐다. revision 23의 백업·로컬 복원·운영 적용, 백엔드 Fly.io v67 및 프런트엔드 Pages 라이브 검증까지 완료됐다. 최종 `origin/master` 일반 push 뒤 HTML·JS·CSS HTTP 200과 기능 마커를 확인했다. 라이브 JS는 `/assets/index-C1JfAthe.js`(SHA-256 `ef1151ee7dc6369de5c71848562576a9834d4fd037c44678e68d961e6840806a`)다.

## 승인된 제품 계약

1. 밴드별 표시 이름은 해당 프로필의 본인만 수정할 수 있다. 밴드 관리자, owner, 플랫폼 superadmin도 다른 사람의 이름을 수정할 수 없다. 서버는 인증된 활성 멤버십과 본인 소유 프로필을 확인한다.
2. 밴드 공간의 canonical 경로는 `/bands/{slug}`다. 가입 전 소개의 기존 경로는 `/bands/{slug}/about`이며, 이 경로를 변경하지 않는다. 가입 요청은 기존 소개 화면에서 이어진다.
3. 공개 밴드 탐색과 요청 흐름은 예약 경로 `/bands/discover`, `/bands/request`를 사용한다. 라우트 매칭 시 예약 경로를 일반 slug보다 먼저 처리하고 기존 탐색 동작을 유지한다.
4. 가입 대기 중인 밴드 생성 요청은 slug를 예약하지 않는다. 승인되어 실제 밴드가 생성될 때 중복 여부를 다시 검사한다.
5. 한국어 표시 밴드 이름은 중복을 허용하고, 영어 slug만 중복 검사한다. slug availability 조회는 입력한 slug와 사용 가능 여부 boolean만 반환할 수 있다. 그 응답에서 다른 밴드의 이름·설명·소유자·멤버, 대기 중인 생성 요청의 정보는 노출하지 않는다. 공개 밴드 탐색 결과는 기존 공개 조회 계약을 따른다. 생성 URL은 현재 origin을 기준으로 `${window.location.origin}/bands/{slug}` 형태로 만든다.

가입 이름 수정 권한은 프로필 자체 권한과 동일하게 처리한다. 승인 요청에 적힌 이름은 심사 중 미리보기 데이터이며, 관리자 편집 기능으로 바뀌지 않는다. 기존 join name 정책과 공개 범위를 유지한다.

## 다음 구현 범위: 프로필 포지션과 일정 유형

아래 항목은 사용자가 구현을 승인한 VOC 계약이다. Phase 1 배포 뒤 Phase 2로 넘어가는 시점은 총괄이 작업 순서를 관리하기 위한 내부 단계이며, 새 사용자 구현 승인을 뜻하지 않는다.

### 가입 시 포지션

- 일반 가입 요청과 초대 가입에서 기존 Member 프로필의 복수 포지션 선택 UI를 재사용한다. VOCAL, GUITAR, BASS, DRUMS, KEYBOARD, OTHER 목록과 기존 정렬·중복 검증을 공유한다.
- OTHER를 선택한 경우에만 기존 `custom_position` 규칙을 적용한다: 문자열 최대 50자, OTHER 미선택 시 값은 비운다.
- 요청 선택값은 `band_membership.requested_positions_json` nullable TEXT와 `requested_custom_position` nullable VARCHAR(50)에 보관한다. 관리자는 pending 요청 미리보기에서, 요청자는 본인의 pending membership 응답에서만 확인할 수 있다.
- 선택 없음과 빈 배열은 같은 의미다. 재가입 시 기존 Member의 포지션, custom position, instrument를 유지하며 가입 과정에 포지션 초기화 기능을 추가하지 않는다. 새 프로필은 기존 기본값인 빈 포지션으로 생성한다.
- 승인은 요청 포지션을 기존 프로필에 적용하고 pending 포지션 데이터를 제거한다. 거절도 pending 포지션 데이터를 제거한다. 요청이 pending인 동안 이름·소개·포지션을 다시 제출해도 기존 idempotency 계약에 따라 저장된 요청을 덮어쓰지 않는다.
- 초대 수락에서는 선택한 포지션을 같은 검증으로 적용한다. 이미 활성 멤버인 초대 재시도는 이름·프로필·포지션을 변경하지 않는다.
- Member의 이름·소개·포지션 변경 API는 계속 본인만 사용할 수 있다. 요청 포지션을 처리하는 서버 내부 코드는 관리자 프로필 편집 API로 우회하지 않는다.

### 합주 일정 유형

- `rehearsal.rehearsal_type`은 VARCHAR(20) NOT NULL, 기본값 `UNCLASSIFIED`로 추가하고 다음 값만 허용한다: `REGULAR`, `EXTRA`, `LIVE`, `OTHER`, `UNCLASSIFIED`.
- 기존 rehearsal은 모두 `UNCLASSIFIED`로 둔다. 제목·메모·과거 관행으로 유형을 추측하지 않는다.
- 신규 일정 생성 UI는 기본 선택 `REGULAR`를 명시적으로 요청에 포함한다. 요청에서 유형을 생략하는 레거시 API 생성은 DB 기본값 `UNCLASSIFIED`를 유지한다. 기존 `UNCLASSIFIED` 일정을 편집할 때는 기존 값을 그대로 보여 주고, 사용자가 유형을 선택하도록 안내한다. 저장만으로 임의 분류하지 않는다.
- `Rehearsal.to_dict`와 v2 serializer 모두 `rehearsal_type`을 additive 응답 필드로 제공한다. 달력, 일정 목록, 상세, 대시보드에서 유형을 표시한다.
- 수정 요청에서 `rehearsal_type`을 생략하면 기존 값을 유지한다. 기존 `UNCLASSIFIED` 일정은 계속 미분류로 남으며 UI에서 유형 선택을 유도한다.
- 일정 생성·수정 권한은 현재 권한 검사를 그대로 유지한다.

## 데이터베이스·권한·호환성 계약

- Phase 2에서 다음 순번의 Flask-Migrate/Alembic additive migration을 작성한다. PostgreSQL과 SQLite를 지원하고, 운영 DB에는 이 계획서만으로 적용하지 않는다.
- `band_membership`과 `rehearsal`은 기존 Flask 서버 소유 테이블이다. 브라우저 Data API 쓰기나 직접 DB 접근을 추가하지 않는다. RLS, grants, 정책, service role 경로를 넓히지 않는다.
- Alembic 역사 스냅샷은 revision별로 정확해야 한다. 새 revision은 기존 revision 22 구조에서 가입 요청 필드와 rehearsal 유형·check 제약을 더한 구조로 정의하고, 과거 스냅샷은 신형 ORM 필드를 포함하지 않게 보존한다. 런타임 시작 readiness는 exact head 및 schema contract 검증을 통과해야 한다.
- 요청 포지션은 기존 `requested_name`과 같은 권한으로만 반환한다: 관리자에게는 pending 가입 요청 미리보기에서, 요청자에게는 본인의 pending membership 응답에서 제공한다. 일반 roster 및 제3자 응답에는 포함하지 않는다. 공개 밴드 소개·검색에도 노출하지 않는다. Rehearsal v2 목록·상세와 대시보드 요약 serializer도 유형을 전달한다.

## Phase 1과 Phase 2의 운영 경계

- Phase 1과 Phase 2의 승인된 코드 변경은 로컬 테스트·리뷰·단위 커밋 뒤, 프로젝트 상시 승인에 따라 필요한 배포와 `origin/master` 일반 push를 진행한다. Phase 1에는 Phase 2 데이터베이스 변경이 포함되지 않는다.
- Phase 1 배포 뒤 Phase 2로 넘어가는 순서는 총괄의 내부 작업 순서다. 이는 사용자에게서 새 구현 승인을 받는 조건이 아니다.
- 운영 DB 변경은 백업·로컬 복원 검증 뒤 별도 승인 절차에 따라 완료했다. 적용·보존 검증은 아래 결과를 참조한다.
- 최종 프런트엔드 커밋 `e0b8a456e2b0aea8adced7a4bbffe6ded2561b83`를 `origin/master`에 일반 push했고 Pages 자동 배포가 완료됐다. 최종 HTTP 응답과 라이브 번들 확인 결과는 아래에 기록한다.
- 기존 rehearsal 자료의 유형은 `UNCLASSIFIED`로만 채운다. 데이터 이전이나 의미 추론을 하지 않는다.

## Snapshot·digest·운영 도구 영향

- 새 rehearsal 컬럼은 rehearsal을 refresh 도메인으로 다루는 tracked legacy rehearsal/refresh 코드의 schema 계약, column mapping, row digest, baseline proof 및 import 검증에 영향을 준다. 구형 snapshot에 필드가 없으면 명시적으로 `UNCLASSIFIED`를 채워야 하며, 누락 필드가 있는 데이터는 추측해 분류하지 않는다.
- `band_membership`은 preserve-identity 경로에서 보호되는 테이블이다. pending 필드 추가는 구조·보호 digest를 바꾸므로 해당 경로가 쓰일 경우 새 스키마에 맞춰 baseline/profile evidence 계약을 갱신하고 다시 검토한다. refresh가 membership 요청 데이터를 복사하거나 지우지 않도록 한다.
- revision별 snapshot builder, startup schema guard, v2 rehearsal list/detail/dashboard serializer, legacy rehearsal 응답, calendar/detail UI, refresh proof와 digest 계약을 코드 검색으로 빠짐없이 확인한다.
- 이 단계의 operator 영향 검토는 git tracked 코드만 대상으로 한다. 개인 운영 도구, 비밀 파일, `.env*`, 사용자 상태 데이터는 읽거나 수정하지 않는다.

## 구현 완료 시 필요한 로컬 검증

- 예약 경로와 임의 slug의 우선순위, pending 요청이 slug를 차지하지 않는 동시 승인 처리, 개인정보를 노출하지 않는 중복 응답을 검증한다.
- 본인 프로필 이름 수정은 허용하고 관리자·owner·가입하지 않은 superadmin의 타인 이름 수정은 거부되는지 검증한다.
- 일반 가입·초대 가입의 포지션 검증, pending 관리자 미리보기, 승인 적용 및 승인/거절 후 pending 데이터 제거를 검증한다. 재가입의 미선택/빈 배열 보존과 새 프로필 기본값을 확인한다.
- 활성 멤버의 초대 재시도가 이름과 Member의 모든 프로필 필드를 바꾸지 않는지 검증한다.
- 기존 rehearsal이 `UNCLASSIFIED`로 보이는지, 신규 UI 기본이 `REGULAR`인지, 미분류 편집이 자동 변경되지 않는지, 허용되지 않은 enum이 거부되는지 검증한다.
- v2 및 `Rehearsal.to_dict` 목록·상세·달력·대시보드의 additive 유형 표시, 그리고 생성·수정 요청에서 유형을 생략했을 때 기본값/기존 값 유지 동작을 확인한다.
- SQLite/PostgreSQL migration 업그레이드·보호된 downgrade 동작, revision 22 historical snapshot, head startup guard, RLS/ACL 불변 계약 및 refresh digest/mapping 호환성을 검증한다.

## Phase 2 운영 DB 변경 및 릴리즈 기록

이 절은 revision 23 운영 적용까지의 증거와 상태를 기록한다. Phase 1의 여섯 커밋은 `origin/master`의 `7772534`까지 반영됐고, Phase 2 애플리케이션 기능은 `9f28caa`에서 검증됐다. 운영 적용 직전 로컬 복원 검증에서 public schema ACL의 `pg_database_owner` → `PUBLIC` `USAGE` 권한 1개가 복원본에서 빠지는 불일치가 발견됐다. 운영자 도구는 그 정확한 캡처 항목만 owned local restore에서 재생하고 나머지 ACL을 엄격 비교하도록 보완했다. 이 보완 뒤 새 읽기 전용 백업·복원 리허설과 운영 적용이 모두 성공했다. 최초 실패 증거는 보존했다.

Phase 2 애플리케이션 회귀 검증은 백엔드 전체 **1,136 passed / 0 failed / 0 skipped**(986.78초), 프런트엔드 전체 **377 passed / 0 failed / 0 skipped**(166.23초)다. 변경 JavaScript 23개 파일 lint와 production build도 통과했다(209 modules, 2.70초). 앞선 백엔드 전체 검증의 8개 실패는 frozen revision 05 fixture가 신규 컬럼을 가진 최신 Rehearsal ORM mapping으로 삽입·조회하던 테스트 호환 문제였다. 기존 revision 05 스키마·검증과 cross-tenant assertions를 유지하며 fixture를 당시 컬럼 SQL로 보완하고, 현재 head의 실제 ORM 자동 scope·PUBLIC·관계 저장 검증과 backfilled v05의 `/rehearsals` HTTP 503 보호 검증을 추가했다. 해당 파일 focused **24 passed / 0 failed / 0 skipped** 후 위 전체 1,136개가 통과했다. 스키마 영향 재검증 8개와 비즈니스 영향 검증 128개 통과 기록도 유지한다. 이 결과는 운영자 도구의 별도 focused **11 passed / 0 failed / 0 skipped**와 구분한다.

### 백업·복원·운영 적용 완료 결과

- 읽기 전용 백업과 owned local PostgreSQL 17 복원 리허설은 `admission-event22-23-20261023T090115Z-abf89e79f7d6`에서 성공했다. 38개 public table, 기존 37개 애플리케이션 테이블 데이터 projection과 Alembic ledger, security 및 key 보존을 확인했고 owned PostgreSQL은 정상 종료했다. `report.json` SHA-256은 `09fc0eb262473ba28d7d784d6c34a018fd1b09b699a1d6f2d9ff650aa5046894`, `manifest.json`은 `68487a84de6e212e272f396989fd82b2ac6a64e07025e17c7c180ed7200081ef`, dump는 `269e440b9b9005238ae0ad08b33a85252e2d546642ac558412b5db7c19ed3c30`이다.
- 운영 적용은 `admission-event22-23-20261023T090316Z-0ccc0ec83533`에서 완료됐다. report SHA-256 `c414bd278af03ec0a10abd375f08b38e497f8ae350a3a76d06f4a19da8d44ef6`; revision 23, 38개 테이블, `committed: true`, fresh read-only postcheck 통과를 기록한다.
- 백엔드는 Fly.io release **v67**이 NRT에서 시작 완료됐다(기존 app/sizing 유지, deployment `01M5KYH2B1FN2YK5VF7Y84DN19`). startup critical log는 없었고 Gunicorn/worker boot를 확인했다. API 확인은 members 200 및 정확한 CORS, slug guest 401, v1/v2 public rehearsal 각각 11개 항목·유형 필드를 포함한다. policy flags `ready`, `acceptance`, `upload`는 모두 false로 유지됐고 실제 R2 연결·객체 변경은 없었다.
- 로컬 rehearsal은 Supabase provider/system role membership 24개 edge를 재현하지 않았고 `anon`/`authenticated` role closure만 재현했다. 그 전체 remote role/membership graph는 운영 적용 전후 정확히 보존됐는지 비교했다.
- 최초 로컬 복원 실패 증거는 그대로 보존했다. 관리형 Supabase Auth는 이번 public-schema backup 범위가 아니며 이 backup은 Auth 관리 데이터의 백업을 뜻하지 않는다.
- 프런트엔드 소스의 `origin/master` 일반 push 후 Pages가 배포됐다. HTML·JS·CSS 모두 HTTPS HTTP 200을 반환했고, live JS에서 API origin `https://band-archive.fly.dev`, `rehearsal_type`, `합주 유형`, `포지션 · 여러 개 선택 가능` 마커와 CSS의 `.member-position-options input`을 확인했다. 기능 준비 상태 `feature_ready`는 true였다. JS asset은 `/assets/index-C1JfAthe.js`, SHA-256 `ef1151ee7dc6369de5c71848562576a9834d4fd037c44678e68d961e6840806a`다.

### 정확한 적용 대상과 데이터 영향

- 기존 Flask-Migrate/Alembic revision `20261021_22`에서 다음 단일 revision `20261023_23`으로 업그레이드한다. Supabase CLI의 별도 migration history를 만들거나 기존 revision을 수정하지 않는다.
- 변경은 기존 테이블에 세 컬럼과 named CHECK 하나를 추가하는 것뿐이다. 새 테이블·인덱스를 만들지 않고 테이블/컬럼/sequence ACL, RLS 활성화·FORCE 상태, 정책, 소유권 및 default privileges를 변경하지 않는다.
- 기존 가입 요청 두 필드는 모두 `NULL`, 기존 rehearsal 유형은 모두 `UNCLASSIFIED`가 된다. 제목·메모·instrument에서 포지션이나 유형을 추론하지 않는다. 기존 tenant/user/member ID, 모든 기존 컬럼 값, 객체 키 및 관계를 보존한다.

아래는 적용된 revision의 DDL 의미를 설명한다. 운영 적용은 검토된 migration runner로 수행했으며, 아래 블록은 라이브 DB에서 추출한 SQL이 아니다.

```sql
ALTER TABLE band_membership ADD COLUMN requested_positions_json TEXT NULL;
ALTER TABLE band_membership ADD COLUMN requested_custom_position VARCHAR(50) NULL;
ALTER TABLE rehearsal ADD COLUMN rehearsal_type VARCHAR(20) DEFAULT 'UNCLASSIFIED' NOT NULL;
ALTER TABLE rehearsal ADD CONSTRAINT ck_rehearsal_type
  CHECK (rehearsal_type IN ('REGULAR', 'EXTRA', 'LIVE', 'OTHER', 'UNCLASSIFIED'));
```

마이그레이션은 PostgreSQL `pg_settings`의 밀리초 값을 읽고, 미설정(`0`)이거나 상한보다 큰 경우에만 `SET LOCAL lock_timeout = '5000ms'`, `SET LOCAL statement_timeout = '30000ms'`를 설정한다. 이미 더 짧은 값은 늘리지 않는다. 이 설정은 해당 트랜잭션이 끝나면 사라지며 글로벌·세션 영구 설정을 변경하지 않는다. 기존 private-schema 검증 뒤 `band_membership`, `rehearsal`에 필요한 잠금을 얻고 DDL을 수행한다. 잠금 대기나 문장 실행 한도를 넘으면 실패로 중단하며 한도를 늘려 재시도하지 않는다.

초대 수락에 적용할 포지션은 **그 수락 요청에서 현재 명시한 비어 있지 않은 선택값만** 사용한다. 초대 요청의 선택값이 없거나 빈 배열이면 기존 프로필을 유지하며, 과거 pending 가입 요청 포지션을 fallback으로 적용하지 않는다. 일반 가입 승인에서 저장된 pending 신청을 처리하는 계약과 혼동하지 않는다.

### 적용 전 증거와 승인 게이트 완료 기록

적용 전에 Phase 2 백엔드·프런트엔드 전체 회귀 검증과 보안 검토를 마치고 source commit, migration 및 operator evidence pin을 고정했다. 대상은 revision `20261021_22`였고, drift가 없는지 비교한 뒤 일관된 public-schema 백업을 확보했다. 이 백업은 관리형 Auth를 포함하지 않는다.

백업은 owned local PostgreSQL 17에 복원해 38개 테이블, 37개 애플리케이션 테이블의 기존 컬럼 projection과 Alembic ledger, security 및 key manifest를 비교했다. 첫 로컬 복원에서 `PUBLIC USAGE` ACL 항목 하나가 누락되어 gate가 중단됐다. source/local ACL 차이가 그 캡처 항목 하나일 때만 local restore에서 정확히 재생하는 수정을 추가했고, unexpected ACL delta는 여전히 거부하도록 테스트했다. 수정 후의 새 읽기 전용 rehearsal, 데이터 보존 검사와 owned-server clean stop은 위 evidence에 기록돼 있다.

별도 승인 후 runner로 `22 → 23`을 적용했다. report는 commit 성공과 revision 23/38 tables를 기록하고, fresh read-only postcheck가 통과했다. 기존 데이터 projection, security와 key 보존 검증 결과는 해당 report와 위 rehearsal evidence에 보존돼 있다.

### 런타임 전환과 현재 상태

전환 당시 기존 v66은 revision 22를, Phase 2 새 런타임은 revision 23 exact head와 전체 schema contract를 요구했다. 승인된 순서로 revision 23 적용 후 Fly.io v67을 시작·검증했다. 현재 DB는 revision 23이고 v67 백엔드가 운영 중이다. 자동 migration이나 startup `create_all`로 schema 간격을 메우지 않는다.

운영 적용 report는 DB preservation 및 fresh read-only postcheck 통과를 기록한다. 백엔드 릴리즈 후 members·slug·v1/v2 rehearsal HTTP/CORS 검증도 완료했고, 프런트엔드 Pages의 HTTP 응답·라이브 번들·기능 마커도 위와 같이 확인했다. 운영 smoke 검증을 위해 콘텐츠나 신청 데이터를 새로 쓰지 않았다.

### 실패와 중단 경계

사전 drift, 백업·복원 불일치, 검증 실패, 잠금/문장 timeout 또는 배포 실패가 발생하면 다음 mutation을 중단하고 증거와 현재 revision·런타임 상태를 보고한다. 실패 검증을 우회하거나 timeout을 늘리지 않고 자동 롤백·강제 push를 하지 않는다. PostgreSQL의 실패한 DDL 트랜잭션이 원자적으로 취소되는 것과 운영 버전을 임의로 되돌리는 자동 롤백은 구별한다. 성공적으로 커밋된 migration 이후의 복원·downgrade는 현재 데이터와 백업을 다시 검토해 별도 방향을 정한다.

revision 23 downgrade는 두 requested 필드 중 하나라도 non-NULL이거나 `UNCLASSIFIED` 외 일정 유형이 하나라도 있으면 데이터 폐기를 거부한다. 이 보호를 무력화하기 위해 해당 값을 삭제·초기화하지 않는다. 이번 변경은 R2 원본·콘텐츠 쓰기/삭제·데이터 이전, 비밀 설정, 스케일·과금 인프라 및 실제 메일 발송 활성화를 포함하지 않았다. 추가적인 운영 DB 변경은 별도 승인 대상이다.
