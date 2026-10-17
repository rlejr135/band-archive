# 자동 멤버 프로필 운영 점검·이전

이 문서는 실행 승인이 아니다. 운영 DB 변경·기존 가입자 프로필 생성은 별도 사용자 승인, 백업·복원 검증 후 총괄 운영자가 실행한다. 이 작업은 기존 멤버/연습 기록/계정/콘텐츠/R2 객체를 삭제하거나 합치지 않는다.

## 도구 계약

- `member_profile_backfill.py`: 기본은 dry-run. `MEMBER_PROFILE_DATABASE_URL`로 대상을 명시하고 `--band-id <정수>`로 한 밴드를 지정한다. 실행 전 DSN을 출력하지 않는다. `.env`, 앱 기본 DB, 저장된 Fly 자격 증명을 자동으로 읽지 않는다.
- `--mapping-file`은 선택이다. 정확한 `{ "member_id": 123, "user_id": 456 }` 배열만 받으며 user_id는 AppUser 내부 숫자 PK다. 기존 이름/이메일을 보고 자동 연결하지 않는다. 기존 연결을 다른 계정으로 돌리거나 프로필을 병합하지 않는다.
- 연결된 프로필 중복은 현재/탈퇴 상태와 무관하게 중단한다. 활성 멤버십인데 계정이 비활성/이메일 미확인이라면 dry-run 결과의 blocked 목록을 검토한다. apply는 이런 계정이 있으면 중단하며 상태를 고치지 않는다.
- apply는 band 행 잠금을 잡고 검증 후 한 트랜잭션에서 명시적 연결과 누락 프로필 생성만 수행한다. 호출형 `repair_profiles(connection, ...)`은 commit하지 않으므로 승인된 migration/backfill 트랜잭션에 포함할 수 있다.
- 이전 프로필의 instrument, 이름, 개인 기록 참조는 바꾸지 않는다. 새 프로필은 안전한 표시 이름(이메일 제외), 빈 소개/포지션으로 만든다. 반복 실행은 새 중복을 생성하지 않는다.

## v17 → v18 승인 후 안전 절차

1. 테스트된 source commit, migration18 파일, backfill 도구, 운영 runner의 SHA-256을 고정한다. 기존 `tools/.state/rehearse_staging_v16_to_v17.py`, apply 및 그 핀은 수정하지 않는다.
2. 승인된 Supabase 대상/CA 검증/세션 풀러 연결을 확인하되 URL·비밀번호는 출력하거나 CLI 인수에 넣지 않는다. 읽기 전용 트랜잭션에서 v17 단일 head, 현재 스키마, 명시적 band IDs, 연결 중복/누락/blocked 계정을 점검한다. 불명확한 레거시 매칭은 추론하지 않는다.
3. 동일 exported repeatable-read snapshot으로 public custom-format dump와 모든 테이블의 건수/전체 원래 컬럼 해시를 보관한다. 캘린더 3개 테이블, 정책 수락 기록, 가입 소개 등 최근 테이블/필드를 누락하지 않는다. 기존 고정 TABLES 목록에만 의존하지 않는다.
4. 소유한 localhost PostgreSQL 17에 dump를 복원하고 원본과 동등성을 확인한다. migration18 + 승인된 band별 backfill을 한 트랜잭션에서 리허설한다. 검사 실패 시 rollback하고 원인을 해결한다.
5. `member_profile_migration_evidence.capture_old_rows`는 source 시점 컬럼 투영을 해시한다. migration 이후에는 원래 컬럼만 비교한다. `assert_old_rows_preserved`는 모든 비-member 테이블의 전체 행 유지, 기존 member 전체 필드 유지, manifest에 있는 새 member IDs와 정확한 linked_user_id 변경만 허용한다. 실제 원문 개인정보는 증거 JSON에 저장하지 않는다.
6. 스키마 변경은 member 신규 4컬럼, 연결 유일 제약, revision 체크 제약만 허용한다. 신규 필드 기본값과 기존 행의 기본값 적용, 기존 컬럼/제약/인덱스/트리거/ACL/RLS/owner 유지, member sequence가 max(id)보다 뒤처지지 않음, FK·개인 기록 보존을 독립 검증한다. member sequence 증가는 승인된 신규 insert 범위만 허용한다. 기존 번호를 낮추거나 데이터에 맞춰 임의 reset하지 않는다.
7. 실패 주입으로 DDL 후·backfill 후·검증 중 오류가 기존 상태로 rollback되는지 확인한다. SQL transaction commit 결과가 불명확하면 자동 재시도/자동 롤백하지 않고 읽기 전용으로 실제 상태를 확인한다.
8. 운영 적용 직전 pin과 현재 스키마를 재확인하고 짧은 lock/statement timeout을 설정한다. migration version 행과 해당 band 행을 고정 순서로 잠근다. 동일 트랜잭션에서 v18 migration, 승인된 backfill, 데이터/권한 검사, version 전환을 수행한 뒤 commit한다. live 상태와 이전 백업이 달라졌다면 백업 기준을 무시하지 말고 새 백업·리허설을 진행한다.
9. fresh connection에서 v18 schema/startup/security와 프로필 누락 0/중복 0을 다시 확인한다. 검증한 BE를 먼저 배포하고 인증/명단/API smoke 후 FE를 배포한다. 실제 프로필 수정이나 기존 콘텐츠 삭제를 smoke 명목으로 수행하지 않는다.

## 해시 증거의 한계

원래 컬럼 해시 증거만으로 신규 컬럼 기본값, 제약 이름/종류, ACL/RLS, sequence 상태, 앱 권한, 데이터베이스 연결 대상은 검증되지 않는다. 위 항목의 독립 검사를 생략하지 않는다. 앱이 실행 중인 데이터베이스의 읽기/쓰기 경합은 별도 관리하며, 실제 DB 변경 승인은 상시 배포 승인과 구분한다.
