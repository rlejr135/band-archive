# 정책 제한 스키마 v21 → v22 운영 절차

이 문서는 빈 `content_restriction` 테이블, 그 제약·인덱스와 `audit_event`의 업로드 확인 조회용 부분 인덱스만 추가하는 절차다. 실제 제한 사례를 등록하거나 계정·콘텐츠·R2·메일을 변경하지 않는다. 일반 배포 승인과 별개로 root가 확인한 이번 DB 승인 범위에서만 적용한다.

## 실행 전

- 전체 BE/FE 검증과 독립 코드 리뷰를 마친 소스를 단위 커밋한다.
- 현재 운영은 `20261020_21`, 새 head는 `20261021_22`여야 한다. 다른 revision은 중단한다.
- 현재 shell의 `REHEARSAL_TARGET_URL`을 보호된 자격정보 전달 수단으로 사용한다. 값 출력, 명령 인자 전달, `.env` 파일 읽기·변경을 하지 않는다.
- 목적지는 Tokyo session pooler `aws-0-ap-northeast-1.pooler.supabase.com:5432`, 사용자 `postgres.qrgmgglqssvpctpyixsw`, DB `postgres`, `sslmode=verify-full`로 도구가 고정한다.
- 공개 CA는 `backend/certs/supabase-prod-ca-2021.crt`, PostgreSQL 17 도구는 `backend/tools/.state/postgresql117/pgsql/bin`을 사용한다.
- root가 신규 helper SHA-256, migration SHA-256, source commit을 검토·기록한다. 기존 `.state`의 과거 helper는 source pin으로 재사용하되 수정하지 않는다.

인자 없이 실행하면 준비 메시지만 표시하고 DB 연결·백업·로컬 서버 시작을 하지 않는다.

## 1. 읽기 전용 백업과 소유 로컬 복구 리허설

다음은 실행 명령 형식이며, 검토한 실제 pin으로 채운 뒤 root만 실행한다.

```powershell
& '.venv-test/Scripts/python.exe' tools/policy_restriction_upgrade.py `
  --rehearse `
  --approved-helper-sha256 <검토한-helper-SHA256> `
  --source-commit <검토한-40자리-commit> `
  --migration-sha256 <검토한-migration-SHA256>
```

도구는 원격 repeatable-read/read-only snapshot에서 public 테이블 전체 행 해시·catalog·owner·ACL/RLS·sequence·R2 키 해시를 기록하고, 같은 snapshot으로 `pg_dump --schema=public` 백업을 만든다. Supabase 관리 Auth 스키마·사용자 원문 데이터는 보고서에 쓰지 않는다. 실제 public 데이터는 private dump에만 보관된다.

이후 새 비공개 `.state/policy21-22-*` 디렉터리와 임의의 localhost 포트에서 자신이 만든 PostgreSQL 17만 실행한다. public 백업의 역할·기본 권한과 스키마를 복원하고, 기존 데이터·관계·보안·키의 동등성을 확인한 뒤 **실제 migration 22**를 적용한다. 소유 프로세스만 종료하고, private 증거·백업은 보존한다.

성공 기준:

- report `status=verified`, `mode=rehearse`, `remote_writes=false`.
- `restore_verified`, `local_migration_verified`, `original_rows_security_keys_preserved` 모두 true.
- 원본 37개 테이블의 모든 행·catalog·기존 sequence 상태·owner·ACL/RLS·키 보존.
- 새 head v22, 총 38개 private 관리 테이블, 새 제한 테이블 0행.
- 신규 부분 인덱스와 전체 스키마 계약 일치, 로컬 PG 종료 확인.

root는 출력된 private 디렉터리의 `report.json`, `manifest.json`, `production-public-v21.dump` SHA-256을 별도로 검토·기록한다. 보고서 본문·백업·DB URL을 채팅에 붙이지 않는다. 리허설이 실패하면 재시도 전에 원인을 검토한다.

## 2. 별도로 승인한 운영 적용

```powershell
& '.venv-test/Scripts/python.exe' tools/policy_restriction_upgrade.py `
  --apply `
  --operator-go ROOT_REVIEWED_POLICY_RESTRICTION_V21_V22_APPLY `
  --approved-helper-sha256 <리허설과-동일한-helper-SHA256> `
  --source-commit <리허설과-동일한-commit> `
  --migration-sha256 <리허설과-동일한-migration-SHA256> `
  --evidence-directory <검토한-private-리허설-디렉터리> `
  --report-sha256 <검토한-report-SHA256> `
  --manifest-sha256 <검토한-manifest-SHA256> `
  --dump-sha256 <검토한-public-dump-SHA256>
```

적용은 검토한 증거와 source pin이 일치하고, snapshot이 24시간 이내이며, 운영 v21 데이터·관계·키·sequence·보안이 백업과 여전히 같은 경우만 진행한다. 원본 37개 테이블의 SHARE 잠금으로 일반 테이블 쓰기를 대기시키므로 짧게 쓰기가 대기할 수 있다. PostgreSQL sequence 값은 트랜잭션 스냅샷의 대상이 아니고 별도의 `nextval` 호출은 이 테이블 잠금으로 막히지 않는다. 따라서 리허설부터 적용·fresh postcheck까지 앱의 쓰기 트래픽이 조용한 시간에 진행하고, sequence 또는 행 상태가 백업과 다르면 중단해 새 백업·리허설부터 다시 검토한다. 잠금 5초·SQL 문장 120초 제한을 사용한다.

하나의 트랜잭션에서 migration 22만 실행하고 기존 모든 데이터·sequence·catalog·ACL/RLS/owner·키 보존, 새 제한 테이블 0행, 38개 테이블 private 계약을 다시 검증한 뒤 commit한다. 이어 새 원격 read-only 연결로 재검증한다.

어떤 실패도 자동 재시도·다운그레이드·데이터 수리로 우회하지 않는다. `committed=true` 또는 `unknown`이면 root가 현재 DB를 읽기 전용으로 확인하기 전 재실행하지 않는다. DB 적용 성공 후 필요한 BE/FE 배포·HTTP/CORS/운영 번들 확인은 별도 배포 스킬에 따라 root가 수행한다.

## 보존 범위

이 백업은 public 앱 스키마만 복구 검증한다. Supabase Auth 관리 데이터·R2 파일의 복구 백업이라고 설명하지 않는다. 기존 운영 백업과 사용자 파일은 삭제하지 않는다. 업로드 확인 기록은 해당 콘텐츠가 유지되는 동안 보존하고, 삭제 확정 후 30일 이내 정리한다. 계정 삭제 때 감사 기록의 개인 연결은 제거하거나 익명화한다. 문의·신고 기록은 처리 종료 후 30일 이내 정리하되, 진행 중인 분쟁이나 법정 보존에 필요한 최소 자료는 예외로 다룬다. 이 도구는 해당 정리·삭제를 수행하지 않으며, 실물 삭제 대상은 별도 운영 절차에서 검토한다.
