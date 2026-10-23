# 밴드 가입 프로필·일정 유형 VOC 계약

상태: 사용자 승인 VOC를 구현하기 전에 고정하는 계획서. 이 문서는 구현·배포·운영 DB 변경 완료 보고가 아니다.

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
