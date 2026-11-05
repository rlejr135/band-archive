# Linux 배포 runtime 대응 검증

이 디렉터리는 로컬 격리 검증 지원이다. 제품·운영 배포에 사용하지 않는다.
최초 실제 실행에서는 `.state`의 검토된 파생 이미지와 wrapper를 사용했고, 이 절차는 그 최소
구성을 새 clone에서도 볼 수 있도록 옮긴 것이다. 승격한 통합 recipe 자체를 재빌드했다고
추정하지 않는다. 실제 실행 결과와 이미지 ID는 최종 검증 보고서의 증거표를 따른다.

## 고정 입력과 안전 경계

- 제품 source: `d5cae8795f9916ea68ba963ad07d10c3af58cb07`.
- 배포 ancestor: Dockerfile의 `registry.fly.io/band-archive@sha256:156133750a9ec26589a677023d66142f9d097e7b94e1659ea226a93e118cd376`.
- 실제 확인 runtime: Python 3.13.18, Debian 13 trixie 13.7, x86_64,
  SQLAlchemy 2.1.6, psycopg/psycopg-binary 3.2.10, pytest 7.4.3.
- PG 공급원: 고정 base image의 Debian 공식 trixie 저장소. 실제 검증 버전은
  PostgreSQL 17.11 (`17.11-0+deb13u1`). 통합 recipe는 server/client/libpq에 이 버전을 명시한다.
  나중에 해당 버전이 저장소에서 사라지면 중단하고 공급원을 검토한다. 자동 최신 대체하지 않는다.
- 제품 Python 패키지를 설치/재설치/업그레이드하지 않는다. pytest도 이미 배포 image에 있다.
  기존 OS package upgrade/remove는 simulation과 실제 inventory 비교에서 모두 거절한다.
- `verify_image_source.py`는 source의 tests/tools를 제외한 제품 Python을 `/app`와 대조한다.
  누락·CRLF→LF 이외 내용 차이는 실패한다. raw hash와 정규화 hash를 모두 보존한다.
  최초 확인에서는 114개 정규화 내용이 동일하고 113개 raw 차이가 있었다. 예를 들어
  기준선 `app.py`는 CRLF592개, image 파일은 CRLF0개였다. 원시 byte 동일성을 주장하지 않는다.
- 인증 설정 파일을 읽거나 바꾸지 않는다. image 접근 권한이 없으면 준비된 인증을 사용자에게
  요청한다. 이 절차가 `flyctl auth docker` 또는 자동 로그인/token 발급을 실행하지 않는다.
- 운영 DB/R2/Auth, 기존 `.state`, 홈 디렉터리, Docker socket, 비밀 `.env`를 mount하지 않는다.
  테스트 컨테이너는 `--network none`, UID10001, port publish 없이 실행한다.

## 1. 새 context 준비 (PowerShell)

`<python>`은 로컬 Python 절대 경로다. `<fresh-context>`는 존재하지 않는 검증 전용 경로로
치환한다. 아래 스크립트는 기존 경로를 덮어쓰지 않는다.
명령 작업 디렉터리는 앱 루트 `E:/Anything/band-archive`다(Git 최상위는 `E:/Anything`).

```powershell
$verificationContext = '<fresh-context>'
if (Test-Path -LiteralPath $verificationContext) { throw 'Use a new verification context' }
New-Item -ItemType Directory -Path $verificationContext | Out-Null
New-Item -ItemType Directory -Path (Join-Path $verificationContext 'support') | Out-Null
Copy-Item -LiteralPath 'backend/tools/verification/run_isolated.py','backend/tools/verification/runtime_manifest.py' -Destination (Join-Path $verificationContext 'support')
Copy-Item -LiteralPath 'backend/tools/verification/bootstrap' -Destination (Join-Path $verificationContext 'support') -Recurse
Copy-Item -LiteralPath 'backend/tools/verification/linux/os_inventory.py','backend/tools/verification/linux/verify_image_source.py','backend/tools/verification/linux/container_entrypoint.py' -Destination (Join-Path $verificationContext 'support')
Copy-Item -LiteralPath 'backend/tools/verification/linux/Dockerfile' -Destination (Join-Path $verificationContext 'Dockerfile')
& '<python>' 'backend/tools/verification/prepare_snapshot.py' --repository . --destination (Join-Path $verificationContext 'source')
if ($LASTEXITCODE -ne 0) { throw 'Snapshot preparation failed' }
```

검토 대상: `source/fixture-os-adapter.diff`, `source/verification-source.json`.
두 private PG fixture의 binary 경로·OS 실행명 선택만 달라야 한다. assertion, 기존 skip guard,
readiness, timeout, migration, 실제 잠금·connection 검증은 바꾸지 않는다. 테스트 소스 원본은 수정하지 않는다.

## 2. 이미지 준비와 runtime 보존 확인

```powershell
docker image inspect registry.fly.io/band-archive@sha256:156133750a9ec26589a677023d66142f9d097e7b94e1659ea226a93e118cd376 --format '{{.Id}} {{.Os}}/{{.Architecture}}'
# 없는 경우에만, 사용자가 준비한 기존 registry 인증으로 immutable pull:
docker pull registry.fly.io/band-archive@sha256:156133750a9ec26589a677023d66142f9d097e7b94e1659ea226a93e118cd376
docker build --progress plain -t anything-verify:d5cae87-linux $verificationContext
if ($LASTEXITCODE -ne 0) { throw 'Stop: image/source/package verification failed' }
docker image inspect anything-verify:d5cae87-linux --format '{{.Id}}'
```

이미지가 이미 있다면 pull을 생략한다. Dockerfile 준비 단계의 공식 apt network 접근과
테스트의 network 차단은 구분한다. Python distribution manifest before/after가 동일해야 한다.
PG apt가 만드는 기본 `17/main` cluster는 실행하거나 사용하지 않는다. fixture가 각자 만든
loopback cluster만 사용한다. `/var/run/postgresql`의 verification UID 소유권은 Debian PG의
기본 Unix socket lock 파일을 위한 **자체 검증 컨테이너 전용** 보완이다.

## 3. 수집과 실행

Linux temporary file/PG data를 Windows bind mount 아래 두지 않는다. 최초 bind mount에서는
pytest capture의 open/unlinked 임시 파일 `truncate`가 실패했다. capture를 끄거나 pytest를
바꾸지 않고, container Linux `/tmp`로 옮겨 동일 단일 collection이 성공했다.

아래 각 실행은 컨테이너 이름을 고유하게 쓴다. 기존 컨테이너와 충돌하면 삭제하지 말고
새 이름을 선택한다. `--rm`을 사용하지 않아 종료 후 증거를 복사할 수 있다.

```powershell
docker run --name anything-verify-one-collect --network none --user 10001:10001 anything-verify:d5cae87-linux --backend /verification/source/backend --output-parent /tmp --pg-bin /usr/lib/postgresql/17/bin -- --collect-only tests/test_auth.py::test_me_requires_well_formed_bearer_header
$coreTargets = Get-Content 'backend/tools/verification/linux/core-targets.json' -Raw | ConvertFrom-Json
docker run --name anything-verify-core-collect --network none --user 10001:10001 anything-verify:d5cae87-linux --backend /verification/source/backend --output-parent /tmp --pg-bin /usr/lib/postgresql/17/bin -- --collect-only @coreTargets
docker run --name anything-verify-core --network none --user 10001:10001 anything-verify:d5cae87-linux --backend /verification/source/backend --output-parent /tmp --pg-bin /usr/lib/postgresql/17/bin -- @coreTargets
docker run --name anything-verify-full-collect --network none --user 10001:10001 anything-verify:d5cae87-linux --backend /verification/source/backend --output-parent /tmp --pg-bin /usr/lib/postgresql/17/bin -- --collect-only
docker run --name anything-verify-full --network none --user 10001:10001 anything-verify:d5cae87-linux --backend /verification/source/backend --output-parent /tmp --pg-bin /usr/lib/postgresql/17/bin
```

위 목록은 실행 순서이며 실패 후에도 다음 단계로 넘어가는 스크립트가 아니다. 각 `$LASTEXITCODE`와
runner/manifest status를 확인한다. 실패하면 최초 증거를 보존하고 원인·보완안을 총괄이 검토한다.
1,800초 timeout을 유지한다. core selector는 29파일이며 최초 실수집은 447 parameter nodes였다.
marker가 없는 PG 테스트도 있으므로 `-m postgres_integration`만으로 대체하지 않는다.

wrapper는 runner status와 같은 컨테이너의 최종 distribution 비교를 따로 기록한다.
runner 실패는 그대로 실패이며 runner 성공이어도 manifest 차이가 있으면 실패한다.
R2·서명·외부 검색의 fake 범위와 synthetic actor/실제 HTTP·JWT 범위를 결과에 함께 적는다.
collection 성공은 테스트 실행 성공이 아니다.

## 4. 증거 복사·hash 확인·한정 cleanup

실행 출력의 `PRIVATE_OUTPUT_DIR=/tmp/verification-<id>`를 사용한다. 아래 `<container>`,
`<run-id>`, `<new-evidence>`는 이번 실행의 값으로 바꾼다. **PG data/tmp 전체를 복사하지 않는다**.

```powershell
$verificationEvidence = '<new-evidence>'
if (Test-Path -LiteralPath $verificationEvidence) { throw 'Use a new evidence path' }
New-Item -ItemType Directory -Path $verificationEvidence | Out-Null
foreach ($artifact in @('stdout.log','stderr.log','junit.xml','run.json')) {
    docker cp ('<container>:/tmp/verification-<run-id>/' + $artifact) (Join-Path $verificationEvidence $artifact)
    if ($LASTEXITCODE -ne 0) { throw 'Copy failed; keep the container' }
}
docker cp '<container>:/tmp/verification-postrun' (Join-Path $verificationEvidence 'postrun')
if ($LASTEXITCODE -ne 0) { throw 'Copy failed; keep the container' }
docker cp '<container>:/verification-evidence' (Join-Path $verificationEvidence 'image-metadata')
if ($LASTEXITCODE -ne 0) { throw 'Copy failed; keep the container' }
$run = Get-Content (Join-Path $verificationEvidence 'run.json') -Raw | ConvertFrom-Json
foreach ($entry in $run.hashes.PSObject.Properties) {
    $actual = (Get-FileHash (Join-Path $verificationEvidence $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $entry.Value) { throw 'Hash mismatch; keep the container' }
}
$status = Get-Content (Join-Path $verificationEvidence 'postrun/status.json') -Raw | ConvertFrom-Json
$finalHash = (Get-FileHash (Join-Path $verificationEvidence 'postrun/runtime-final.json') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($finalHash -ne $status.runtime_final_sha256) { throw 'Final manifest hash mismatch' }
# 해시 확인 후 이번에 만든 종료 컨테이너만 제거한다:
docker rm '<container>'
```

위 정리는 이번에 만든 종료 컨테이너에만 한정한다.
raw output에는 합성 fixture 정보가 포함될 수 있으므로 일반 보고에서는 결과·hash·실패 종류만 표시한다.
수집 stdout과 실행 JUnit은 다음 명령으로 대조한다.

```text
<python> backend/tools/verification/linux/summarize_results.py --collection <collection-stdout> --execution <execution-junit> --output <new-results-json>
```

이 JSON은 실제 parameter node별 status/time, 미실행 node, 증거 hash를 보존한다.
동일 node의 중복 JUnit과 미수집 case는 거절한다. stdout/JUnit 합계만으로 필수 PG 실행을 추정하지 않는다.
cleanup에서는 전체 Python/Postgres 종료, `docker system prune`, 기존 volume/.state 일괄 삭제를 하지 않는다.
검증용 image/snapshot 정리도 이번 소유 대상을 확인한 뒤 별도로 수행한다.
