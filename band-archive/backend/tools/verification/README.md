# 고정 제품 기준선 격리 검증 지원

대상 제품은 `d5cae8795f9916ea68ba963ad07d10c3af58cb07`이다. 이 도구를
추가한 문서/지원 커밋을 제품 커밋으로 혼동하지 않는다. 아래는 **실행 절차**이며
실행 성공을 기록한 보고서가 아니다. 첫 실행 전에 총괄 리뷰를 받는다.

운영 DB/Auth/R2와 연결하거나 제품 의존성/설정을 수정하지 않는다. 운영 배포도 없다.
이 디렉터리에는 비밀 설정, 기존 `.state` 로그, DB 복사본, 외부 bootstrap이 필요 없다.

## 구성과 허용된 차이

- `prepare_snapshot.py`: Git의 고정 커밋에서 추적 backend만 **새 경로**에 추출한다.
  환경/로컬 상태/DB/개인 키 파일을 제외한다. 기존 작업 트리와 파일을 덮어쓰지 않는다.
  `tests/test_data_api_security_migration.py`와 `tests/test_preserve_identity_refresh_postgres.py`의
  PG binary 경로 및 `.exe` 선택만 바꾸고
  정확한 diff·파일별 SHA256을 남긴다. assertion, timeout, loopback 제한, migration,
  실제 잠금 coordinator, 테스트별 cluster 수명은 변경하지 않는다.
- `bootstrap/sitecustomize.py`: python-dotenv의 파일 검색/읽기를 차단한다.
  초기화 성공 표시를 runner가 별도 subprocess에서 확인한다. 실패하면 테스트를 실행하지 않는다.
- `run_isolated.py`: 환경 제거, PG17 opt-in, 소유 tmp/output, JUnit 및 로그 hash,
  **1,800초 제한**을 갖는 runner다. 준비한 소스 hash를 매번 확인한다.
- `runtime_manifest.py`: 전체 설치 distribution의 이름·버전·위치와 interpreter identity를
  기록하고 기존 패키지 변경·제거·승인하지 않은 추가를 거절한다.

기준 테스트를 완전히 무변경 실행했다고 표현하지 않는다. 위 fixture OS adapter는
실행 환경에 필요한 test-only 차이이며 보고서에 diff hash와 함께 적는다.
tests가 fixture를 각 모듈로 직접 import하므로 동명 plugin fixture만 추가하는 방법은 사용하지 않는다.

## 1. 이미지와 소스 고정

정확한 이미지 후보:

```text
registry.fly.io/band-archive@sha256:156133750a9ec26589a677023d66142f9d097e7b94e1659ea226a93e118cd376
```

사용 전에 이미지 manifest/platform digest와 해당 배포의 제품 커밋 연결을 별도 확인한다.
태그 또는 Dockerfile의 `python:3.13-slim`만으로 동일 runtime라고 판단하지 않는다.
실제 Python patch, OS, CPU architecture, SQLAlchemy, PostgreSQL driver 등은 이미지에서 기록한다.
이미지에 tests가 없는 것은 정상이다(`backend/.dockerignore`).

예: 아래 `<python>`은 선택한 로컬 Python의 절대 경로로, `<fresh-parent>`는 이미 존재하는
검증 전용 부모 디렉터리로 치환한다. destination 자체는 없어야 한다.

```text
<python> backend/tools/verification/prepare_snapshot.py --repository E:/Anything/band-archive --destination <fresh-parent>/source-d5cae87
```

이 명령은 pytest를 실행하지 않는다. 생성된 `fixture-os-adapter.diff`와 원본/수정 hash를
총괄이 검토한 뒤 다음 단계로 간다. 제품 소스와 배포 이미지 `/app`의 연결도 별도 확인한다.

## 2. 검증 이미지 준비: 제품 Python 패키지는 불변

정확한 배포 이미지를 `FROM ...@sha256:...`으로 사용하는 **로컬 전용 파생 이미지**를 만든다.
제품 Dockerfile/requirements/global 환경은 수정하지 않는다. 준비 과정의 network 허용과
실제 테스트 실행의 network 차단을 구분한다.

1. test tool 설치 전 `runtime_manifest.py capture /evidence/runtime-before.json` 실행.
2. 해당 Linux OS/architecture에 맞는 **PostgreSQL 17 server binaries**를 준비한다.
   실제 이미지 OS를 확인한 뒤 그 OS의 공식 패키지 또는 검증된 PG17 공급원을 선택한다.
   Windows `.exe` 파일을 Linux에 mount하지 않는다. 공급원/버전/해시와 추가 OS 패키지를 기록한다.
   공유 sidecar DB는 global role/default ACL 변경을 하는 테스트의 cluster 격리를 대신하지 못한다.
3. 배포 이미지에 없는 pytest 및 필요한 test 전용 wheel만 정확 버전으로 준비한다.
   제품 패키지 재설치/업그레이드/다운그레이드 또는 resolver의 암묵적 변경을 허용하지 않는다.
   `--no-deps --target <test-support>` 사용 시 누락된 순수 test 의존성을 따로 명시하고,
   제품 distribution과 이름이 겹치는 wheel은 거절한다.
4. test-support가 interpreter 검색 경로에 포함된 동일 조건으로 설치 후 manifest를 기록한다.
   `compare`는 기존 distribution의 이름·버전·위치가 같아야 통과하며, 추가도 명시 허용해야 한다.
   allowlist는 실제 wheel 목록을 리뷰한 뒤 작성한다. `--allow-added`는 정규화 이름이다.

```text
python /support/runtime_manifest.py capture /evidence/runtime-before.json
python /support/runtime_manifest.py capture /evidence/runtime-after-tools.json
python /support/runtime_manifest.py compare /evidence/runtime-before.json /evidence/runtime-after-tools.json --allow-added pytest
```

마지막 명령의 `pytest`는 예시 최소 허용 항목이다. 실제 추가 패키지가 있으면 자동 허용하지 말고
각 패키지를 검토한 뒤 명시한다. test-support 경로를 전달할 때 `PYTHONPATH`에 의존하지 않는다
(runner가 제거함). 별도 검증 이미지의 interpreter site 경로에 test-only `.pth`를 두거나,
동일 interpreter의 distribution 검색에 포함되는 검토된 설치 방식을 사용한다.
제품 패키지를 shadow하는 중복 distribution은 capture 단계에서 거절된다.

## 3. 테스트 컨테이너 조건

- `--network none`, 포트 publish 없음: 외부 provider 통신은 불가능하고 PG는 loopback으로만 실행한다.
- `--user <non-root-uid>:<gid>`: `initdb`는 root 실행 불가. 검증 snapshot/output/tmp는 이 사용자가
  쓸 수 있어야 한다. 운영 볼륨, 홈 디렉터리, Docker socket, `.env`를 mount하지 않는다.
- support 코드는 읽기 전용, 준비된 snapshot은 독립 로컬 복사본, evidence는 검증 전용 경로다.
- `/tmp`와 필요한 검증 경로 외에 권한을 넓히지 않는다. `--privileged` 사용 안 함.
- runtime env는 명시 allowlist로 시작한다. 운영 DSN/provider 설정을 `--env-file`로 주입하지 않는다.
- R2/서명/검색 fake 및 sentinel 유지. 합성 principal과 실제 HTTP/JWT 테스트를 구분해 보고한다.

실제 바이너리 경로는 이미지에 준비된 PG17 경로로 치환한다. `postgres --version`이 17인지
runner가 확인하며, 기존 fixture는 테스트마다 자기 cluster/port/process를 만든다.

## 4. 수집 → 필수 묶음 → 전체 suite

컨테이너 내부 명령 예시:

```text
python /support/run_isolated.py --backend /verification/source-d5cae87/backend --output-parent /evidence --pg-bin /usr/lib/postgresql/17/bin -- --collect-only
python /support/run_isolated.py --backend /verification/source-d5cae87/backend --output-parent /evidence --pg-bin /usr/lib/postgresql/17/bin -- tests/test_suggestion_proceed_transaction.py tests/test_v2_vote_postgres_contract.py tests/test_upload_quota_postgres.py
python /support/run_isolated.py --backend /verification/source-d5cae87/backend --output-parent /evidence --pg-bin /usr/lib/postgresql/17/bin
```

가운데 명령은 짧은 예시이며 **A3 전체 필수 묶음이 아니다**. 최종 담당자는 ordinary transaction,
parent deletion, initiation/materialization, recovery/startup, access/source/TTL, 실제 JWT를 포함한
명시 대상 파일/node 목록을 확정해 기록한다. marker만 선택하면 marker 없는 PG 테스트가 빠질 수 있다.
확장된 parameter node 목록은 collection stdout과 실행 JUnit을 대조한다. total pass 수나
수집 성공으로 실행 성공을 대신하지 않는다. runner의 기본 JUnit 경로는 매번 소유 output 안이다.

전체 suite를 실행해야 readiness를 판정한다. 실패/skip/timeout/환경 부족은 숨기거나 조건을
완화하지 않고 그대로 기록한다. `--collect-only` 실행은 test 실행 증거가 아니다.
runner 인자는 검토된 pytest 대상·수집 옵션에 한해 사용한다. 임의 plugin/config 우회에 사용하지 않는다.

각 실행 뒤 `runtime-final.json`을 capture하고 최초 manifest와 다시 compare한다.
이미지 digest + 버전/설치 위치 manifest는 환경 증거이지 모든 installed file의 동일성 증명은 아니다.

## 5. 증거와 정리

보고 항목: 제품 커밋, 지원코드 커밋/해시, 이미지 digest/플랫폼, runtime identity 및 manifest hash,
PG version/공급원, fixture diff hash, 전체 명령, 수집/실행한 parameter node, 결과/skip 사유,
JUnit·stdout·stderr hash, fake/미검증 범위. 출력 원문에 운영 비밀/개인정보가 없어야 한다.

timeout은 124로 남으며 무한 재실행하지 않는다. subprocess 제한 시 자식 PG가 남을 수 있으므로
증거를 보존한 채 **이번 실행 소유 컨테이너·프로세스만** 확인/종료한다. 전체 python/postgres kill,
기존 `.state`/volume 일괄 삭제를 하지 않는다. `--rm`은 컨테이너 제거용이지 evidence 삭제용이 아니다.
기준선 snapshot·이번 실행 cluster/tmp 정리는 대상의 실제 절대 경로와 소유권을 확인한 뒤 수행한다.
