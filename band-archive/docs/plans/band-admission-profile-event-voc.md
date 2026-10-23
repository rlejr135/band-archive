# 밴드 가입 프로필·일정 유형 VOC 계약

상태: Phase 1은 `origin/master`의 `7772534` 및 백엔드 Fly.io v66 배포와 라이브 프런트엔드 확인까지 운영 완료했다. Phase 2는 로컬 구현·전체 검증을 완료했으며, 운영 DB 변경은 별도 승인 전으로 미승인·미적용이고 배포 대기 중이다. 아래 실행안의 백업·로컬 복원·운영 적용 및 운영 검증이 완료됐다는 의미는 아니다.

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
- 운영 DB에 Phase 2 migration을 적용하기 전에는 migration SQL, 기존 데이터 영향, 백업·복원 검증, startup/RLS 검증, 적용·검증·중단 계획을 담은 구체적인 실행안을 작성해 별도 승인을 받는다. AGENTS.md의 별도 운영 DB 변경·데이터 이전 확인 경계를 따른다. 운영 DB 승인 경계는 코드 구현·배포 및 일반 push의 상시 승인과 별개다.
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

## Phase 2 운영 DB 별도 승인용 실행안 — 미승인·미적용

이 절은 실행 계획이며 운영 DB 변경 승인이나 적용 기록이 아니다. 운영 DB migration 별도 승인은 아직 대기 중이다. Phase 1의 여섯 커밋은 `origin/master`의 `7772534`까지 반영됐고, 백엔드 Fly.io v66 및 라이브 프런트엔드 확인을 마쳤다. Phase 2는 로컬 구현과 전체 검증을 완료했으며 운영 DB 변경은 미승인·미적용, 배포는 대기 상태다. Phase 2 코드가 이미 커밋·배포됐거나 아래 백업·로컬 복원·운영 적용·운영 검증이 성공했다고 주장하지 않는다.

Phase 2 최종 로컬 검증은 백엔드 전체 **1,136 passed / 0 failed / 0 skipped**(986.78초), 프런트엔드 전체 **377 passed / 0 failed / 0 skipped**(166.23초)다. 변경 JavaScript 23개 파일 lint와 production build도 통과했다(209 modules, 2.70초). 앞선 백엔드 전체 검증의 8개 실패는 frozen revision 05 fixture가 신규 컬럼을 가진 최신 Rehearsal ORM mapping으로 삽입·조회하던 테스트 호환 문제였다. 기존 revision 05 스키마·검증과 cross-tenant assertions를 유지하며 fixture를 당시 컬럼 SQL로 보완하고, 현재 head의 실제 ORM 자동 scope·PUBLIC·관계 저장 검증과 backfilled v05의 `/rehearsals` HTTP 503 보호 검증을 추가했다. 해당 파일 focused **24 passed / 0 failed / 0 skipped** 후 위 전체 1,136개가 통과했다. 스키마 영향 재검증 8개와 비즈니스 영향 검증 128개 통과 기록도 유지한다. 이 로컬 결과는 아래 운영 백업·복원 전제나 별도 승인 게이트를 대체하지 않는다.

### 정확한 적용 대상과 데이터 영향

- 기존 Flask-Migrate/Alembic revision `20261021_22`에서 다음 단일 revision `20261023_23`으로 업그레이드한다. Supabase CLI의 별도 migration history를 만들거나 기존 revision을 수정하지 않는다.
- 변경은 기존 테이블에 세 컬럼과 named CHECK 하나를 추가하는 것뿐이다. 새 테이블·인덱스를 만들지 않고 테이블/컬럼/sequence ACL, RLS 활성화·FORCE 상태, 정책, 소유권 및 default privileges를 변경하지 않는다.
- 기존 가입 요청 두 필드는 모두 `NULL`, 기존 rehearsal 유형은 모두 `UNCLASSIFIED`가 된다. 제목·메모·instrument에서 포지션이나 유형을 추론하지 않는다. 기존 tenant/user/member ID, 모든 기존 컬럼 값, 객체 키 및 관계를 보존한다.

검토 대상 DDL의 의미는 아래와 같다. 이는 체크인된 revision의 정적 설명이며 라이브 DB에서 생성하거나 실행한 SQL이 아니다. 실제 적용은 검토된 migration runner로 수행한다.

```sql
ALTER TABLE band_membership ADD COLUMN requested_positions_json TEXT NULL;
ALTER TABLE band_membership ADD COLUMN requested_custom_position VARCHAR(50) NULL;
ALTER TABLE rehearsal ADD COLUMN rehearsal_type VARCHAR(20) DEFAULT 'UNCLASSIFIED' NOT NULL;
ALTER TABLE rehearsal ADD CONSTRAINT ck_rehearsal_type
  CHECK (rehearsal_type IN ('REGULAR', 'EXTRA', 'LIVE', 'OTHER', 'UNCLASSIFIED'));
```

마이그레이션은 PostgreSQL `pg_settings`의 밀리초 값을 읽고, 미설정(`0`)이거나 상한보다 큰 경우에만 `SET LOCAL lock_timeout = '5000ms'`, `SET LOCAL statement_timeout = '30000ms'`를 설정한다. 이미 더 짧은 값은 늘리지 않는다. 이 설정은 해당 트랜잭션이 끝나면 사라지며 글로벌·세션 영구 설정을 변경하지 않는다. 기존 private-schema 검증 뒤 `band_membership`, `rehearsal`에 필요한 잠금을 얻고 DDL을 수행한다. 잠금 대기나 문장 실행 한도를 넘으면 실패로 중단하며 한도를 늘려 재시도하지 않는다.

초대 수락에 적용할 포지션은 **그 수락 요청에서 현재 명시한 비어 있지 않은 선택값만** 사용한다. 초대 요청의 선택값이 없거나 빈 배열이면 기존 프로필을 유지하며, 과거 pending 가입 요청 포지션을 fallback으로 적용하지 않는다. 일반 가입 승인에서 저장된 pending 신청을 처리하는 계약과 혼동하지 않는다.

### 적용 전 증거와 승인 게이트

1. Phase 2 전체 백엔드·프런트엔드 회귀 검증과 보안 리뷰를 완료하고 정확한 커밋, migration 파일 및 배포 산출물의 해시를 고정한다. 현재의 focused 통과 기록을 전체 검증 완료로 대신하지 않는다.
2. 운영 대상의 정확한 환경·DB identity·revision `20261021_22`와 migration owner를 읽기 전용으로 확인한다. 모든 테이블의 컬럼·타입·default·NOT NULL·PK·unique·CHECK·FK·인덱스, tenant 관계와 backfill marker, audit append-only 계약 및 의도하지 않은 public relation 유무를 엄격 비교한다. table/column/sequence ACL과 PUBLIC·브라우저 role의 유효 권한, RLS·FORCE·policy, owner 및 schema/global default privileges도 사전 증거로 고정한다. drift가 있으면 적용하지 않는다.
3. 승인받을 쓰기 중단·배포 전환 구간과 백업 대상·보관 위치·복원 절차를 명시하고, 일관된 전체 DB 백업을 확보한다. 이 계획서 작성으로 백업이나 운영 쓰기 중단이 이미 수행된 것은 아니다. 비밀이나 DB URL을 보고서에 노출하지 않는다.
4. 그 백업을 격리된 로컬 PostgreSQL에 복원하고 원본 증거와 전체 테이블의 행 수·기존 컬럼 값 digest·ID·관계·FK 및 ACL/RLS 계약을 비교한다. 복원본에서만 동일 revision을 적용해 기존 컬럼 값 전부 불변, 새 요청 필드 전부 NULL, 기존 일정 유형 전부 UNCLASSIFIED, exact-head/schema·startup 계약 및 보안 상태 불변을 확인한다. 새 컬럼 때문에 달라지는 전체-row digest는 기존 컬럼 projection과 새 컬럼의 기대값을 나누어 검증한다.
5. 위 증거, 정적 DDL, 기존 데이터 영향, 정확한 운영 대상·버전·배포 전환 순서, 백업·복원 결과 및 아래 중단 계획을 사용자에게 제시하고 **운영 DB 변경에 대한 별도 명시적 승인**을 받는다. 승인 전에는 운영 DB migration을 실행하지 않는다.

### 런타임 호환 구간과 승인 후 실행 순서

현재 구버전 런타임의 SQL은 새 nullable 필드와 DB 기본값을 사용하는 additive schema와 호환되는 범위가 있지만, 이것이 구버전의 재시작 안전성을 보장하지는 않는다. 기존 v66의 startup은 revision 22를, Phase 2 새 런타임 startup은 revision 23 exact head와 전체 schema contract를 요구한다. 따라서 새 런타임은 DB가 22인 동안 시작될 수 없고, DB를 23으로 바꾼 뒤 구버전을 새로 시작하는 순서도 허용되지 않는다. 이 짧은 전환 구간에는 사전에 정한 쓰기 중단과 배포 순서를 따르고, 잠깐 실행 중인 구버전 프로세스를 전체 전환 검증의 대체 근거로 삼지 않는다. 자동 migration이나 startup `create_all`로 간격을 메우지 않는다.

별도 승인 후에도 먼저 정확한 backup/복원·대상·revision/보안 증거와 고정 산출물을 재확인한다. 승인된 구간에 검토된 runner로 `22 → 23` migration을 한 번 실행하고, 성공 확인 뒤 사전에 준비한 새 백엔드를 시작한다. 이어서 필요한 프런트엔드 산출물을 배포한다. 구체적인 release 식별자와 순서는 승인 시점의 배포 실행안에 기록하며 현재 문서에서 미래 release 성공을 가정하지 않는다.

운영 적용 직후에는 모든 테이블의 행 수·기존 컬럼 digest·ID, FK/tenant 관계, audit 계약, 새 컬럼 값과 제약 및 기존 index 집합을 사전 증거와 대조한다. ACL/RLS·소유권·default privileges도 전후 동일한지 확인하고 exact-head startup의 읽기 전용 검증을 통과시킨다. 그 뒤 실제 HTTP 응답·CORS·라이브 프런트엔드 번들, 신청 포지션 공개 범위와 일정 유형 응답을 확인한다. 운영 smoke 검증을 위해 콘텐츠나 신청 데이터를 새로 쓰지 않는다.

### 실패와 중단 경계

사전 drift, 백업·복원 불일치, 검증 실패, 잠금/문장 timeout 또는 배포 실패가 발생하면 다음 mutation을 중단하고 증거와 현재 revision·런타임 상태를 보고한다. 실패 검증을 우회하거나 timeout을 늘리지 않고 자동 롤백·강제 push를 하지 않는다. PostgreSQL의 실패한 DDL 트랜잭션이 원자적으로 취소되는 것과 운영 버전을 임의로 되돌리는 자동 롤백은 구별한다. 성공적으로 커밋된 migration 이후의 복원·downgrade는 현재 데이터와 백업을 다시 검토해 별도 방향을 정한다.

revision 23 downgrade는 두 requested 필드 중 하나라도 non-NULL이거나 `UNCLASSIFIED` 외 일정 유형이 하나라도 있으면 데이터 폐기를 거부한다. 이 보호를 무력화하기 위해 해당 값을 삭제·초기화하지 않는다. 이 실행안은 R2 원본·콘텐츠 쓰기/삭제·데이터 이전, 비밀 설정, 스케일·과금 인프라 및 실제 메일 발송 활성화를 포함하지 않는다. 운영 DB 별도 승인이 오기 전까지 Phase 2의 운영 DB 적용은 대기한다.
