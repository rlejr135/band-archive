# 아키텍처 리팩터링 검증 보완

## 대상과 판정 기준

- 조사일: 2026-11-05.
- 대상 제품: `d5cae8795f9916ea68ba963ad07d10c3af58cb07`. 조사 HEAD: `63ea386e3b6499358296f5e65151e2b0a0038181`. 실행 cwd `E:/Anything`에서 `git diff d5cae8795f9916ea68ba963ad07d10c3af58cb07 63ea386e3b6499358296f5e65151e2b0a0038181 --name-only -- band-archive/backend` 결과 차이 없음. 아래 코드 경로는 별도 표시 없으면 `E:/Anything/band-archive` 기준이다.
- 지시서: `docs/plans/2026-11-05-architecture-refactoring-follow-up-instructions.md` 전체(A/B/C). 원계획 `docs/plans/2026-10-25-architecture-refactoring-plan.md:148`의 가입·역할·탈퇴·프로필, addendum `docs/plans/2026-10-25-architecture-refactoring-plan-addendum.md` 6절의 transaction 책임 기준에 대응한다.
- 용어: 계정 AppUser, 가입·역할 BandMembership, 밴드별 멤버 프로필 MemberProfile(현 모델 Member)은 별개다.
- 분류: 전환 완료 / 기존 구조가 기준을 충족해 유지 / 명시적 제외·보류 / 미확인·결함. 마지막 분류의 ‘미확인’은 결함·악용 가능성이 확정되었다는 의미가 아니다.
- 이 C 조사에서는 코드·테스트를 읽었으며 테스트 실행, 제품 변경, 운영 접근은 하지 않았다. 아래 노드의 존재는 실행 성공 증거가 아니다.
- A runtime 대응 검증: 고정 runtime의 핵심447개 재실행 통과. 전체 suite는 timeout으로 완료하지 못해 최종 판정 보류.
- B 마지막 FE 및 core 필수 PG31 실행 통과. 이번 시각 비교는 미확인(아래 B절).

## A. 운영 runtime 대응 검증 — 준비 결과

다음은 총괄 담당자가 전달한 실행 사실이며, C 조사자가 별도로 실행한 결과는 아니다.

- Fly 읽기 전용 `image show`로 machine `080e444f9643d8`의 image `registry.fly.io/band-archive:deployment-01M6K9R0A7RRYDCGVQBFGZX6E4`, digest `sha256:156133750a9ec26589a677023d66142f9d097e7b94e1659ea226a93e118cd376`가 기존 v91 기록과 일치함을 확인했다. 실제 Python patch 버전·OS/아키텍처·설치 패키지 manifest는 아직 확보하지 못했다. image 연결 확인과 runtime 동등성 검증을 구분한다.
- `DockerDesktopLinuxEngine` pipe가 없었다. Docker Desktop 숨김 실행 요청 후에도 해당 process가 확인되지 않았고 Docker status가 응답하지 않았다. 작업이 소유한 CLI 두 개만 Ctrl+C로 종료했다. 사용자에게 Docker Desktop 직접 실행을 요청할 단계이며 기존 개발 자원은 종료하지 않았다.
- 후속 확인(2026-11-05): 사용자가 엔진을 실행한 뒤 `docker info`에서 Linux/x86_64, Server 29.5.2를 확인했다. 위 Docker 미준비 기록은 당시 상태로 보존한다. 고정 digest/tag image는 로컬에 없고 기존 인증으로 pull한 결과 `no basic auth credentials`였다. 총괄이 사용자에게 `flyctl.exe auth docker` 직접 실행을 요청하고 대기 중이다. 설정·토큰 읽기/쓰기는 하지 않았고 runtime 테스트도 아직 실행하지 않았다.
- 이후 같은 날 사용자의 Docker 인증 완료 뒤 고정 image pull이 성공했고 digest가 위 immutable 식별값과 일치했다. Docker·인증 장애와 사용자 대기는 앞선 시점의 이력이며 현재 대기 원인이 아니다. 네트워크를 차단한(`network none`) 실제 image에서 Python **3.13.18**(2026-10-28 빌드, GCC14.2), Debian13 trixie13.7/x86_64, SQLAlchemy2.1.6, psycopg/psycopg-binary3.2.10, pytest7.4.3, Gunicorn26.2.0, Flask3.0.0, python-dotenv1.0.0을 확인했다. 앞선 manifest 미확보 상태는 이 확인으로 보완됐지만 계약 테스트 통과를 의미하지 않는다. 이후 파생 image와 실행 결과는 아래 후속 절에 기록한다. 완료 판정은 실제 결과 확보까지 보류한다.
- `backend/tools/verification/`의 재현 도구 5개(`prepare_snapshot.py`, `runtime_manifest.py`, `run_isolated.py`, `bootstrap/sitecustomize.py`, `README.md`)는 총괄 리뷰를 완료했고 지원 도구 커밋 `6b9f2e1`로 보존했다. AST 검사 4개가 통과했다. 검증 실행 전 준비물이지 통과 증거가 아니다.
- `prepare_snapshot.py` 실행은 성공했다. 소유 출력 경로는 `backend/tools/.state/followup-reviewed-source-20261105`이며 fixture 경로와 실행 파일 선택의 검증 환경 차이만 확인했다. `run_isolated.py --help` 성공은 CLI 진입 확인이다. pytest·운영 runtime 대응 계약 통과를 뜻하지 않는다.
- 로컬 Python 3.12 가상환경에서 `runtime_manifest` capture와 같은 파일끼리 compare가 성공했다. 이는 CLI 작동 검사일 뿐 운영 manifest 확보·비교 일치 또는 runtime 대응 검증 증거가 아니다.

### A-3 필수 기준표

아래 기준은 지시서 A-3에서 고정했으며 준비 상황에 맞춰 완화하지 않는다. 아래 상태는 착수 당시 기준표다. 실제 core 결과와 전체 suite timeout은 후속 절을 따른다.

| 묶음 | 필요한 실행 증거 | 착수 당시 상태 |
|---|---|---|
| 일반 DB 유스케이스 | 중간 실패·flush·audit·commit 오류 원자성, callback-before-commit, commit 후 JSON 실패 의미 | 미실행/미확인 |
| 실제 PG 경쟁 | 곡 제안 채택·투표·부모 삭제·권한 변경의 독립 연결, DB 잠금 대기와 최종 저장 결과 | 미실행/미확인 |
| 업로드 시작·완료 | 예약·동시 시작·실제 크기·완료 직전 재인가·멱등 완료·단계별 commit | 미실행/미확인 |
| 복구 | timeout/HEAD/재시도·materialization 후 중단·결과 재조회·시작 복구 schema/flag gate | 미실행/미확인 |
| 접근·signing | 차단·삭제 대기·pruned 원본 signer 미호출·소스 선택·TTL·밴드/부모 관계 | 미실행/미확인 |

### A 후속 실제 실행

고정 image의 Python 환경을 유지한 소유 파생 image03 `sha256:e47d1df5eb0f1651edc15ed3e3f261705efde920693bcd139b70b564e2471f66`에 로컬 PG `17.11-0+deb13u1`을 준비했다. Python 패키지 추가·변경0, 기존 OS 패키지 버전 변경0이며 OS 패키지25개를 추가했다. runtime manifest 전후 차이는0, SHA-256 `FE3A1825A8488DBB8851131617337A17DE90BB69EE7C9E8213D58A858900E4C7`다.

소스 비교는 Python114개가 개행 정규화 후 동일하며113개는 raw 바이트 차이가 있었다. app.py의 git archive 쪽 CRLF는592개, image 쪽은0개였다. raw 차이를 제품 로직 차이 또는 그 반대 방향으로 추정하지 않는다.

초기 bind 임시 경로 문제와 PG Unix socket 환경 문제는 통과 결과와 구분한다. 핵심 첫 pytest 실행은 총447개 중 **416 passed·31 PG setup errors·0 skipped**, 164.82초였다. PG 오류 원인은 Debian 기본 socket 경로 `/var/run/postgresql`의 쓰기 권한이었다. 소유 파생 image의 해당 디렉터리 UID 권한만 보완했고 assertion·제품 guard·timeout을 낮추지 않았다. 이후 PG 경합 노드1개가 **1 passed / 7.48초**, manifest 동일을 확인했다.

동일 핵심447개 재실행은 **447 passed·failed/errors/skipped 각0**, pytest587.80초·runner590.281초다. stdout SHA-256 `11708c5a8252cab7826f38b24e25bf75dd415936c6c1400fca920477da5b4141`, JUnit SHA-256 `826bef6a59bb4b8cbb46cde115c743bac1ec81cfadfd1851641f9445eb58efb1`다. 정확한 명령·node 대응은 B-3에 기록한다.

**A 판정: 부분 해소.** runtime 환경과 핵심 재실행 근거를 확보했으며 전체 suite는 아래 timeout으로 완료하지 못했다. 위 이전 미실행 기준표는 착수 당시 상태이며 최종 필수 시나리오 대응은 실행 목록으로 확인해야 한다. 전체 완료는 아직 보류한다. C의 추가 실패·경쟁 계약과 FE 시각 미확인을 핵심447 통과만으로 해소하지 않는다.

## B-1. 마지막 FE 대상과 실제 실행

마지막 FE 변경은 `f1a4b2f762523a54d45ae8951a2d812e3c27b174`, FE tree는 `c340bfaa3fe1be208a061e0f49fd4eaf8101870a`다. 제품 d5cae879의 FE tree와 동일한 소스를 검증했다. 지원 도구 커밋 `6b9f2e1`은 제품 변경과 구분한다. 아래는 FE 담당자의 2026-11-05 새 실행이며 Node **24.22.1**, cwd `E:/Anything/band-archive/frontend`다.

```text
node --test --test-concurrency=1 src/services/*.test.js src/context/*.test.js src/components/workspace/*.test.js src/components/bands/*.test.js src/components/app/*.test.js
node node_modules/eslint/bin/eslint.js src/components/workspace/DetailMediaLifecycle.dom.test.js src/components/workspace/media/useDetailMedia.js src/components/workspace/rehearsals/RehearsalDetail.jsx src/components/workspace/songs/SongDetail.jsx src/hooks/useMediaUpload.js src/services/mediaUploadManager.js src/services/uploadMediaResponse.js src/services/uploadMediaResponse.test.js
node node_modules/vite/bin/vite.js build --outDir .state/followup-fe-evidence-20261105/dist
```

test는 **470 passed, fail/cancelled/skipped/todo 각0**, duration `324400.017ms`, exit0이다. 영향 8파일 lint exit0, build exit0이며 build에는 500KB 초과 chunk 경고가 있다. lint는 저장소 전체가 아닌 위 8파일 범위다.

재현 환경은 대소문자를 구분하지 않는 OS 변수 allowlist `SystemRoot|WINDIR|COMSPEC|TEMP|TMP|PATH|PATHEXT|APPDATA|LOCALAPPDATA|USERPROFILE|HOMEDRIVE|HOMEPATH|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE`만 전달하고 `ANYTHING_ISOLATED_TEST=1`을 추가한다. provider 변수는 전달하지 않고 dotenv를 읽지 않는다. 기존 Vite 설정은 해당 flag에서 `envDir:false`로 작동한다. Node `spawnSync(process.execPath, args, {env, encoding:'utf8', maxBuffer:30*1024*1024})`로 위 argv를 순차 실행할 수 있다. maxBuffer는 출력 제한이며 heap 증가가 아니다. private run.mjs 없이 같은 인자·환경 정책을 재현한다.

로컬 summary는 `.state/followup-fe-evidence-20261105/{test,lint,build}.summary.json`이며 읽어 확인했다. SHA-256은 test stdout `95eb72689f93da5d670620f950a6637855d9bb120e1da147ad442e11420f4b84`, build stdout `d9e8bf3f70214f32fc1e655556760d53b5ec48d4c05429d45a6c73e0fc5b5e74`, build stderr `aa9b7207d2d2d42fbf4e013331ee76fc39b9b042505b38551893c1f60f35b63a`다. test stderr와 lint stdout/stderr는 빈 출력 SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`다. hash는 위 명령·대상·결과를 대신하지 않는 보조 식별값이다.

생성 CSS `index-B3J8fdwy.css`, JS `index-Cp0gLGqS.js`는 provider 설정 없는 검증용 artifact로 운영 artifact와 동등하다고 주장하지 않는다.

## B-2. 시각 검증 범위

과거 공개 Wave4 보고서는 24회 비교 중23 동일·1 bounce를 기록했으나 edit candidate는 미검증이었다. 이는 과거 증거이며 이번 새 비교 결과가 아니다. 이번에는 CUA browsers가 빈 배열이고 IAB unavailable이라 시각 비교를 실행하지 못했다. 초기 단계에서는 소유 fixture 준비만 했고 서버·스크린샷을 생성하지 않았다. 이후 native CUA 대안으로 Chrome 창1개 inventory까지 확인했으나 get_window→Ctrl+T→비캡처 state 갱신 호출이943.7초 응답하지 않아 총괄이 interrupt했다. 도구의 user-aborted 상태는 사용자가 직접 중단했다는 뜻이 아니다. 새 탭이 일부 생성됐을 수 있으나 상태는 미확인이고 소유 URL 입력·viewport·스크린샷은 실행하지 않았다. 사용자 창 내용을 캡처·출력하지 않았다. 소유 Vite 서버 exec 세션32388만 Ctrl+C 종료(exit1)했다. 다른 플랫폼 우회나 새 시각 회귀 체계는 도입하지 않았다. 따라서 PC/모바일·빈 목록·오류·폼 열림 등 이번 전후 시각 동일성은 **미확인**이다.

## B-3. 실제 PG 실행 증거

핵심 선택은 `backend/tools/verification/linux/core-targets.json`의 **29파일**이며447 parameter node를 수집·실행해447개 모두 통과했다. 실제 실행 recipe는 검토한 소유 image03와 readonly wrapper mount를 사용했다. 다음 argv의 `<wrapper>`는 검증 전용 wrapper 절대 경로다.

```powershell
$coreTargets = Get-Content backend/tools/verification/linux/core-targets.json -Raw | ConvertFrom-Json
docker run --network none --user 10001:10001 --mount 'type=bind,source=<wrapper>,target=/verification-wrapper.py,readonly' --entrypoint python sha256:e47d1df5eb0f1651edc15ed3e3f261705efde920693bcd139b70b564e2471f66 /verification-wrapper.py --backend /verification/source/backend --output-parent /tmp --pg-bin /usr/lib/postgresql/17/bin -- @coreTargets
```

cwd는 `E:/Anything/band-archive`다. 수집은 같은 인자에서 `--` 뒤 `--collect-only`를 추가했고 전체 suite는 core selector를 생략했다. runner timeout1800초는 변경하지 않았다. 실제 선택·결과는 `backend/tools/.state/runtime-verification-20261105-d5cae87-01/evidence/linux-core-pass/{run.json,results.json,junit.xml,stdout.log,postrun/status.json}`, 수집은 형제 `linux-core-collection/stdout.log`,31개 PG 대응은 `linux-core-pass/required-pg-results.json`에 있다. 이 로컬 경로의 존재만으로 재현을 요구하지 않으며29파일 selector와 새 clone 절차는 지원 커밋 `9713bc4`의 `backend/tools/verification/linux/README.md`에 보존했다. 최초 실제 image/wrapper recipe와 승격한 통합 recipe는 구분한다. **통합 recipe 자체는 재빌드·실행하지 않았다.**

전체1738개 collection 성공 뒤 실제 전체 실행은 **1800.012초 timeout, exit124**였다. 최종 summary/JUnit이 없어 전체 통과·missing0·개별 node 통과를 주장하지 않는다. manifest는 동일했고 stdout SHA-256은 `0a3bb580542475432bd352086da49af4dc80ae161d8a9fc2987479a9a4912010`이다. 증거는 같은 evidence 아래 `linux-full-collection`, `linux-full-timeout`에 보존했다. 중간 출력에서는 F1이 관찰됐으나 종료 시점 progress 기호는1373개(`.`1370/F3/E0/skip0)였다. 이는 확정된 개별 실행·통과 건수가 아니며, 수집1738개와의 차이365개도 실행 결과 근거가 부족하다. 이전1738 passed 기록과 이번 timeout을 섞지 않는다.

### 전체 실행의 세 실패 후보 후속 확인

출력 순서로 추정한 아래3개 후보만 같은 image·fixture·runner·`network none`에서 한 번 별도 실행했다. 실제 결과는 **3 failed**, pytest18.27초·runner19.978초, runtime manifest 동일이다. 전체 suite를 다시 실행한 결과가 아니다. 별도 실행의 JUnit이 각 실패를 확정하며, full stdout만으로 같은 개별 node 결과를 확정하지 않는다.

| 별도 실행한 정확 node | 확인된 실패와 한계 |
|---|---|
| `tests/test_featured_media_upgrade_tool.py::test_pinned_public_inventory_compares_real_revision_transition` | `GateError: reviewed source pin differs`. 검토된 private `.state/backup_staging_v12_security13_rehearsal.py`가 격리 snapshot에서 제외되어 source pin을 충족하지 못했다. 새 clone에서 private helper 없이 같은 계약을 재현하는 문제는 미해결이다. |
| `tests/test_policy_restriction_upgrade_tool.py::test_pinned_inventory_rejects_missing_or_unknown_public_tables` | `GateError: source pin differs`. private `.state/rehearse_v19_v21_invite_name.py` 의존성이 snapshot에서 제외됐다. inventory 제품 로직 실패로 단정하지 않으며 private helper 의존 재현 문제도 해결됐다고 처리하지 않는다. |
| `tests/test_legacy_rehearsal.py::test_report_temp_path_replacement_is_not_deleted` | abort 뒤 새 replacement 경로가 사라져 `FileNotFoundError`. 해당 로컬 CLI의 replacement 보존 계약 실패는 확인됐다. inode 재사용 가능성은 계측하지 않은 가설이며 원인 확정이 아니다. 웹 서비스·R2 영향은 확인하지 않았으므로 확대 해석하지 않는다. |

증거는 `backend/tools/.state/runtime-verification-20261105-d5cae87-01/evidence/linux-three-failures`다. JUnit SHA-256 `0186a9a01b8840e65a041f5212ac0d9dc6d450d63d986fdb88c07bcac1662bfb`, stdout SHA-256 `57196ff850659d0c91ec2b4bcedd852b590816dcf572d4bfb4a9198e1d51d4a5`. 제품·기존 테스트를 수정하지 않았다. 핵심447/필수 PG31 통과는 유효하지만 전체 suite gate와 private helper 재현성은 미완료다.

로컬 replacement 실패의 읽기 근거는 `backend/tools/legacy_rehearsal/rehearsal.py:153 _file_identity`의 `(dev, ino)` 식별, `:165 _unlink_owned_path`의 현재 identity 비교 후 unlink, `:272 abort`의 descriptor 닫기 뒤 `:284/:289` helper 호출이다. 소비자는 같은 파일 `:1655`의 CLI report 예약과 `backend/tools/legacy_rehearsal/preserve_identity_refresh.py:704/:706`이다. 후속에서는 소유 handle/identity 근거를 잃었거나 교체가 의심될 때 삭제하지 않는 fail-closed 최소 수정안을 검토하고 POSIX/Windows handle·inode·경로 교체의 독립 회귀 검증이 필요하다. inode 재사용은 여전히 미계측 가설이고 웹/R2 영향은 미확인이다. 이번에 제품 수정은 하지 않았다.

다음31개는 실제 core collection과 성공 JUnit을 대조한 **passed31, failed0, skipped0**이다. core runtime/image·JUnit hash는 A절과 같다.

| 정확한 node ID | 결과 | 초 |
|---|---|---|
| `tests/test_suggestion_proceed_transaction.py::test_private_postgres_two_connections_proceed_once` | passed | 4.334 |
| `tests/test_suggestion_proceed_transaction.py::test_private_postgres_proceed_rechecks_authority_after_waiting_for_band_lock[role_admin]` | passed | 6.788 |
| `tests/test_suggestion_proceed_transaction.py::test_private_postgres_proceed_rechecks_authority_after_waiting_for_band_lock[membership_left]` | passed | 5.067 |
| `tests/test_suggestion_proceed_transaction.py::test_private_postgres_proceed_rechecks_authority_after_waiting_for_band_lock[actor_disabled]` | passed | 4.243 |
| `tests/test_v2_vote_postgres_contract.py::test_votes_serialize_and_aggregate_the_authoritative_final_rows[flip-media]` | passed | 18.118 |
| `tests/test_v2_vote_postgres_contract.py::test_votes_serialize_and_aggregate_the_authoritative_final_rows[flip-suggestion]` | passed | 18.084 |
| `tests/test_v2_vote_postgres_contract.py::test_votes_serialize_and_aggregate_the_authoritative_final_rows[cancel-media]` | passed | 18.118 |
| `tests/test_v2_vote_postgres_contract.py::test_votes_serialize_and_aggregate_the_authoritative_final_rows[cancel-suggestion]` | passed | 18.421 |
| `tests/test_v2_vote_postgres_contract.py::test_votes_serialize_and_aggregate_the_authoritative_final_rows[other-account-media]` | passed | 18.027 |
| `tests/test_v2_vote_postgres_contract.py::test_votes_serialize_and_aggregate_the_authoritative_final_rows[other-account-suggestion]` | passed | 18.046 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[initiate-first-direct-song]` | passed | 18.379 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[initiate-first-direct-rehearsal]` | passed | 18.383 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[initiate-first-multipart-song]` | passed | 18.654 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[initiate-first-multipart-rehearsal]` | passed | 18.568 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[delete-first-direct-song]` | passed | 18.122 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[delete-first-direct-rehearsal]` | passed | 18.062 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[delete-first-multipart-song]` | passed | 18.217 |
| `tests/test_parent_delete_postgres_contract.py::test_parent_deletion_and_upload_reservation_have_one_serialized_order[delete-first-multipart-rehearsal]` | passed | 18.04 |
| `tests/test_upload_initiation_postgres.py::test_gallery_and_personal_log_share_locked_reservation_capacity[gallery-before-personal-log]` | passed | 18.591 |
| `tests/test_upload_initiation_postgres.py::test_gallery_and_personal_log_share_locked_reservation_capacity[personal-log-before-gallery]` | passed | 18.184 |
| `tests/test_upload_quota_postgres.py::test_quota_reservations_serialize_after_accounting[direct-direct]` | passed | 18.161 |
| `tests/test_upload_quota_postgres.py::test_quota_reservations_serialize_after_accounting[direct-multipart]` | passed | 18.155 |
| `tests/test_upload_quota_postgres.py::test_quota_reservations_serialize_after_accounting[multipart-direct]` | passed | 18.285 |
| `tests/test_upload_quota_postgres.py::test_quota_reservations_serialize_after_accounting[expired-cleanup]` | passed | 18.1 |
| `tests/test_upload_quota_postgres.py::test_cleanup_failure_retains_quota_and_retryable_initiation_error_contract[direct]` | passed | 18.072 |
| `tests/test_upload_quota_postgres.py::test_cleanup_failure_retains_quota_and_retryable_initiation_error_contract[multipart]` | passed | 18.464 |
| `tests/test_upload_materialization_postgres.py::test_authority_revoked_during_head_prevents_materialization[membership_left-direct]` | passed | 18.025 |
| `tests/test_upload_materialization_postgres.py::test_authority_revoked_during_head_prevents_materialization[membership_left-multipart]` | passed | 17.953 |
| `tests/test_upload_materialization_postgres.py::test_authority_revoked_during_head_prevents_materialization[actor_inactive-direct]` | passed | 17.952 |
| `tests/test_upload_materialization_postgres.py::test_authority_revoked_during_head_prevents_materialization[actor_inactive-multipart]` | passed | 17.962 |
| `tests/test_postgres_startup_guard_integration.py::test_private_pg_startup_has_no_ddl_or_automatic_jobs_and_rejects_drift` | passed | 8.528 |

범위: 채택4개는 fake JWKS·signer와 합성 서명 JWT를 통한 실제 Flask HTTP/PG 독립 연결·잠금을 검증한다. 투표6개는 합성 actor여서 JWT 인증 범위가 아니다. 부모 삭제8개·시작2개·쿼터6개는 actor/R2를 double·sentinel로 두고 실제 PG 잠금·예약·commit을 검사한다. 쿼터 중4개는 두 HTTP 경쟁, cleanup failure2개는 **HTTP 하나+독립 SQL transaction contender**다. 완료 재인가4개는 fake HEAD/materialization callback을 사용하는 service/request-context이며 실제 PG 연결에서 권한 변경을 검증한다. 시작 guard1개는 소유 PG schema 검사이며 운영 DB·자동화 작업을 실행하지 않는다. 실제 R2 삭제·외부 Auth 사용은 없다.

착수 당시에는 PG를 실행하지 않았지만 후속 핵심447개 재실행은 실제 PG를 포함해 통과했다(A절). 아래 C 관련 목록은 조사 당시 후속 대상 표이며, 정확한 이번 JUnit node와 대조 전에는 각 행을 실행 통과로 바꾸지 않는다. 과거 전체1738 passed도 이번 node 실행 증거로 사용하지 않는다.

| 필수 시나리오 | 정확한 node/목록 상태 | 대상·runtime | 이번 결과 |
|---|---|---|---|
| 가입 프로필 동시 승인 | `tests/test_member_profiles_postgres.py::test_profile_concurrent_approval_has_one_linked_record` | d5cae879 / runtime 확보, 해당 node 확정 결과 없음 | 미확인 |
| 역할·소유권·탈퇴 순서 | `tests/test_band_leave_postgres.py::test_postgres_transfer_role_and_leave_serialize_band_first` | 동일 | 이번 core 대상 아님; full 최종 결과 없음 |
| 추방·소유권 경쟁 | `tests/test_member_removal_postgres.py::test_removal_and_target_owner_promotion_serialize` | 동일 | 이번 core 대상 아님; full 최종 결과 없음 |
| 외부 확인 중 추방 | `tests/test_member_removal_postgres.py::test_removal_during_external_upload_verification_prevents_materialization` | 동일 | 이번 core 대상 아님; full 최종 결과 없음 |
| 완료 잠금 선행·보존 | `tests/test_member_removal_postgres.py::test_completion_lock_wins_then_removal_retains_completed_media` | 동일 | 이번 core 대상 아님; full 최종 결과 없음 |
| 초대 중복 수락·폐기 경쟁 | `tests/test_invite_link_concurrency.py::test_concurrent_duplicate_accept_is_atomic_and_idempotent`, `::test_revoke_and_accept_have_one_serialized_order` | 동일 | 이번 core 대상 아님; full 최종 결과 없음 |
| A-3 곡 제안·투표·부모 삭제·업로드·복구·signing | 위31개 표와447 core 결과에 해당 근거 기록; 위 C 관련 node와 혼용하지 않음 | 동일 | core 통과, full 미완료 |

## B-4. 재현 절차 준비

공개 보존할 최소 도구는 `backend/tools/verification/`에 준비되어 있으며 총괄 리뷰·기본 도구 확인을 완료했다(지원 커밋 `6b9f2e1`). 기존 private runner 경로·stdout hash만으로 재현을 요구하지 않는 방향이다. 현재 확인은 소유 snapshot 준비 성공과 CLI help까지다. 실제 pytest 인자·opt-in 변수·로컬 PG 준비/정리·node별 실제 결과는 위 B-3과 지원 README에 기록했다.

**B-4 판정: 재현 절차 보강.** 실행 재현 도구 준비를 FE 결과·PG 실행 증거 확보로 계산하지 않는다. FE 결과는 B-1, 시각·PG 한계는 B-2/B-3에 기록한다.

## C. 가입·역할·탈퇴·프로필 판정

### C-1. 공통 transaction 경계

`backend/auth_service.py:287–318 synchronize_user`는 계정 동기화를 먼저 commit한다. `authenticate_request:321`, `require_auth:335` 뒤 업무가 수행된다. 따라서 계정 동기화와 업무 변경은 하나의 transaction이 아니다. `backend/band_service.py:37–48 _begin_privileged_mutation`은 SQLite writer lock을 확보하며 PG에서는 기존 session transaction을 이용한다. 신규 session 교체·중첩 begin은 없다.

`require_band_role:104–126`은 잠금 사용 시 Band→행위자 BandMembership을 `populate_existing`으로 다시 읽지만 AppUser는 다시 잠그지 않는다. 생성 요청 심사의 플랫폼 권한 검사는 `backend/routes/bands.py:63–72 require_platform_admin`에서 이루어지고 `review_request` 내부에는 같은 재검사가 없다. `member_profile_service.own_profile:108–113`의 계정 상태 검사도 잠금 전 객체값이다. 반면 `membership_removal_access:846–873`와 `invite_link_service._resolve_context:233–262`는 Band 다음 현재 AppUser를 잠그고 활성·이메일 인증·필요 권한을 확인한다. 이 차이는 구조상 확인되며 실제 권한 변경 경쟁의 외부 영향은 추가 검증이 필요하다.

`band_service.audit:82–91`은 add만, `member_profile_service.ensure_member_profile:32–64`와 `activate_member_profile:67–94`는 flush/변경만 수행한다. 하위 helper에 중간 commit은 없고 가입 활성화·프로필·audit은 상위 commit에 함께 반영된다.

실패 경계는 별도 확인이 필요하다. set_role/leave_band/remove_membership/own_profile에는 명시 rollback이 없고 approve_membership 등은 IntegrityError만 rollback한다. `backend/errors.py:19–36` 오류 핸들러도 rollback하지 않는다. `backend/extensions.py:2–4`, `backend/app.py:509`의 Flask-SQLAlchemy 요청 session 정리와 유스케이스 함수가 명시적으로 rollback을 소유하는 것은 다른 책임이다. 요청 종료 시 미확정 변경의 정리, 직접 호출자가 예외를 잡고 session을 재사용하는 경우를 구분해야 한다. `tests/test_join_name.py:172–187`의 실패 주입 테스트는 183행에서 테스트가 직접 rollback하므로 서비스 자체 실패 정리까지 입증하지 않는다.

### C-2. 원계획 대응표

| 대상 | 실제 진입점·호출자 | transaction·실패·재요청 경계 | 판정 |
|---|---|---|---|
| 가입·심사 | `routes/bands.py:408 request_join → band_service.py:303 join_request`; `routes/bands.py:451 change_membership → :355 approve_membership`; 생성 신청 `routes/bands.py:352 → :225 create_request`, 심사 `:390/:397/:404 → :252 review_request` | Band→가입 잠금. 신청·상태·프로필·audit 상위 commit. 중복 신청/심사는 기존 결과와 created/changed=false. IntegrityError 처리 외 실패 경계·플랫폼 권한 경쟁은 미검증 | **미확인·결함(미확인)**. 유스케이스 경계는 있으나 유지 확정에 필요한 실패·재인가 근거 부족 |
| 역할·소유권 | `routes/bands.py:461 → :717 set_role`; `:598 → :741 transfer_ownership` | Band→행위자 가입→대상 가입. 이전 owner 강등 flush 후 새 owner 지정·audit 한 commit. 대상 계정 활성·인증 검사. 전달 성공 후 재요청은 이전 owner 권한 상실로404 가능 | **미확인·결함(미확인)**. 기존 원자적 구조 확인, rollback/응답 실패·현재 actor 경쟁 검증 부족 |
| 탈퇴·가입 비활성화 | `routes/bands.py:605 → :822 leave_band`; `:526 → :894 remove_membership`; 밴드 폐쇄 `:614 → :923 close_band` | leave는 owner/live upload 409; left 재요청 성공. removal은 계정 재인가, self/owner 금지, left 멱등, 콘텐츠·프로필 보존. 상태/audit 한 commit. 폐쇄는 soft-delete와 purge_after | **미확인·결함(미확인)**. 정상·경쟁 구조 확인, use-case rollback/commit 실패 및 응답 계약 실행 증거 부족 |
| 자기 프로필 수정·활성화 | `routes/member_profiles.py:27/:33 → member_profile_service.py:106 own_profile`; 아래 helper 호출자 목록 | 자기 linked_user만 수정, revision 충돌409. 활성화는 가입과 같은 transaction. active 재요청은 기존 이름 유지. PATCH commit 후 같은 revision 재요청은409 | **미확인·결함(미확인)**. helper 분리·원자성 구조 확인, 실패/재인가 계약 검증 미완료 |

현재 근거만으로 ‘전환 완료’ 또는 ‘기존 구조가 기준을 충족해 유지’를 확정하지 않는다. 불필요한 새 서비스 추출이 필요하다는 결론도 아니다. 필수 범위를 제외·보류로 옮겨 완료 처리하지 않는다.

### C-3. 하위 helper와 호환 호출자

- ensure_member_profile: `backend/band_service.py:289,317,368,493,517,705`; `backend/invite_link_service.py:297`; `backend/member_profile_service.py:79 activate_member_profile`.
- activate_member_profile: `backend/band_service.py:382,386,510`; `backend/invite_link_service.py:323,326`.
- own_profile: `backend/routes/member_profiles.py:27,33`.
- invite accept: `backend/routes/invite_links.py:123 → invite_link_service.accept:284–333`; resume `:111 → resume:265`. accept는 Band→초대→복귀 context→AppUser→가입을 잠그고 상태·프로필·context 완료·audit을 commit한다. 완료 재요청은 active 여부 확인 후 already_member를 반환한다.
- `band_service.join_invite:469–519`는 호환 내부 서비스 및 테스트에서 유지된다. 현재 HTTP `routes/bands.py:585–588`은410으로 서비스 호출을 중단했다. import와 활성 호출은 다르다.
- `band_service.redeem_legacy_claim:657–714`는 보존된 내부·테스트 경로다. 현재 routes에 호출은 없다. Band/claim/가입·프로필·audit 단일 commit, IntegrityError rollback이다. 과거 소유권 수령 HTTP가 현재 활성이라고 기술하지 않는다.
- 위 목록은 비테스트 제품 코드의 직접 호출자를 검색한 결과다. 테스트 호출은 다음 절에 별도로 기록한다.

### C-4. 응답과 실패 후 재요청

서비스는 일반적으로 commit 후 ORM 객체를 반환하고 route에서 membership_dict/jsonify한다(`routes/bands.py:448,459,462,599,606–608`). own_profile도 commit148 뒤 직렬화149–150, invite accept도 commit331 뒤 응답332–333이다. commit 성공 후 직렬화·전송 실패는 DB 변경 실패가 아니며 rollback으로 되돌렸다고 설명하면 안 된다.

join/approve/remove/leave/invite는 기존 결과 반환 분기가 있지만 profile PATCH의 같은 revision 재요청은409, ownership transfer 재요청은404가 가능하다. 변경 없이 현재 계약으로 기록하며 새로운 멱등키·outbox를 추가하지 않는다.

### C-5. 기존 검증 노드와 필요한 후속 확인

다음은 **읽기로 확인한 기존 테스트**이며 이번 C 조사에서 실행하지 않았다. 실제 runtime·선택 조건·실행 결과는 A/B 증거와 연결해야 한다.

| 영역 | backend 기준 pytest node |
|---|---|
| 가입 HTTP·중복·권한 | `tests/test_band_phase3.py::test_creation_approval_owner_and_public_contract`, `::test_membership_permissions_cross_band_and_invite_lifecycle`, `::test_pending_request_unique_constraint_is_idempotent` |
| 이름·포지션·실패 원자성 | `tests/test_join_name.py::test_pending_name_first_value_private_then_approval_consumes_name`, `::test_pending_positions_are_private_first_value_and_apply_on_approval`, `::test_profile_creation_failure_cannot_commit_approval_or_discard_name` |
| 역할·소유권 | `tests/test_band_phase3.py::test_only_owner_changes_admin_roles_and_invites_reset_former_admin`, `::test_transfer_then_leave_preserves_owner_invariant`; `tests/test_band_leave_retention.py::test_transfer_rejects_disabled_or_unverified_target` |
| 탈퇴·추방 | `tests/test_band_leave_retention.py::test_leave_is_self_scoped_idempotent_and_preserves_authored_content`; `tests/test_member_removal.py::test_removal_service_rechecks_account_authority`, `::test_retains_profile_content_and_idempotent_audit_rejoin` |
| 자기 프로필 | `tests/test_member_profiles.py::test_own_profile_edit_and_scope`, `::test_invalid_profile_requests_atomic`, `::test_activation_retries_rejoin_and_no_name_autolink` |
| 실제 PG | `tests/test_member_profiles_postgres.py::test_profile_concurrent_approval_has_one_linked_record`; `tests/test_band_leave_postgres.py::test_postgres_transfer_role_and_leave_serialize_band_first`; `tests/test_member_removal_postgres.py::test_removal_and_target_owner_promotion_serialize`, `::test_removal_during_external_upload_verification_prevents_materialization`, `::test_completion_lock_wins_then_removal_retains_completed_media`; `tests/test_invite_link_concurrency.py::test_concurrent_duplicate_accept_is_atomic_and_idempotent`, `::test_revoke_and_accept_have_one_serialized_order` |

부족한 확인은 (1) HTTP teardown과 직접 호출을 구분한 업무 예외·flush/commit 실패 후 session/DB 결과, (2) commit 성공 뒤 직렬화 실패와 재요청, (3) 심사·역할·자기 프로필에서 현재 AppUser 상태·권한 변경 경쟁이다. 확인된 exploit이 아니라 유지 판정에 필요한 계약 근거의 부족이다. 기존 노드가 이를 다루는지 먼저 조사하고 부족하면 최소 계약 테스트·필요 수정 범위를 별도로 정한다. 현재 사용자에게 확인할 제품 결정은 없으며, 이번 조사 지시만으로 제품 변경을 시작하지 않는다.

### C-6. 독립 후속 조사로 좁힌 기존 테스트의 증명 범위

이번 추가 조사도 읽기 전용이며 신규 테스트·제품 변경·실행은 없다. 기존 노드를 실행하면 아래 범위의 근거는 강화되지만 남은 세 계약을 모두 대신하지는 못한다.

| 확인하려는 계약 | 기존 정확 node와 실제 확인 내용 | 증명하지 않는 부분 |
|---|---|---|
| HTTP 오류와 DB 보존 | `tests/test_member_profiles.py::test_invalid_profile_requests_atomic`(:170–173)는 HTTP400 뒤 새 app context에서 revision0을 검사 | 입력 검증 단계의 거절이므로 변경·flush 이후 실패와 teardown rollback을 직접 확인하지 않음 |
| 서비스 도중 실패 | `tests/test_join_name.py::test_profile_creation_failure_cannot_commit_approval_or_discard_name`(:172–187)는 activation 실패와 이름·상태 보존을 검사 | :183에서 테스트가 직접 rollback함. 서비스의 rollback 소유·동일 session 재사용을 입증하지 않음 |
| 성공 후 반복 요청 | `tests/test_member_profiles.py::test_own_profile_edit_and_scope`(:121–129)는 성공 뒤 같은 revision 요청의409 확인 | commit 뒤 serializer/JSON 실패를 주입하지 않으므로 응답 유실 후 재시도까지 직접 확인하지 않음 |
| 가입 권한 변경 후 재인가 | `tests/test_band_phase3.py::test_transfer_then_leave_preserves_owner_invariant`(:247–251)는 이전 owner의 후속 삭제·역할 변경403 확인 | 순차 요청이며 AppUser 비활성화·인증 해제·platform 권한 변경 경쟁이 아님 |
| stale 가입 권한 | `tests/test_band_phase3.py::test_sqlite_stale_owner_session_cannot_close_after_independent_transfer`(:296–325)는 stale owner membership으로 폐쇄를 시도해403 및 band 보존 확인 | SQLite이며 AppUser 상태·platform 권한 회수 경쟁이 아님 |
| 실제 PG 가입 관계 경쟁 | `tests/test_band_leave_postgres.py::test_postgres_transfer_role_and_leave_serialize_band_first`, `tests/test_member_removal_postgres.py::test_removal_and_target_owner_promotion_serialize`는 가입 역할·탈퇴·추방의 경쟁 확인 | AppUser 경쟁이 아니며 worker가 직접 rollback/remove함(전자는 :78/:81/:84/:87). 서비스 자체 실패 정리 근거로 사용 불가 |
| 계정 자격 거절 | `tests/test_member_removal.py::test_removal_service_rechecks_account_authority`, `tests/test_member_profiles.py::test_own_profile_edit_and_scope`, `tests/test_band_phase3.py::test_band_creation_request_review_rejects_inactive_or_unverified_platform_admin` | 요청 전 상태 변경을 확정하는 순차 검사. 독립 연결의 상태 변경과 잠금 대기 후 재인가를 다루지 않음 |

따라서 추가 검증의 최소 경계는 (1) 승인 변경/flush 후 예외에 대한 HTTP 종료 후 별도 연결의 DB 결과와 직접 서비스 호출자의 rollback 전후 session 결과, (2) 승인·탈퇴/추방·프로필 수정·소유권 이전의 commit 후 직렬화 실패 및 동일 요청 결과, (3) 심사·역할 변경·자기 프로필 수정과 독립 연결의 AppUser 상태/권한 변경 순서다. endpoint마다 실패·재요청 계약이 달라 한 경로 통과로 모두 대체하지 않는다. 이는 필요한 검증 범위의 제안이며 이번에 테스트를 추가하거나 제품 동작을 변경했다는 의미가 아니다.

C 지적은 **부분 해소**다. 코드 위치·업무 경계·미확인 범위의 대응을 명시했지만, 원계획의 필수 대상을 모두 충족했다는 결론은 보류한다.

## 최종 완료 게이트

| 지적 | 완료 조건 | 현재 판정 |
|---|---|---|
| P1 runtime 차이 | 고정 제품과 연결된 runtime 대응 환경에서 A-3 필수 계약 실제 실행·통과, 차이·fake 범위 기록 | 핵심 통과, 전체 suite timeout으로 부분 해소 |
| P2 실행 증거 | 마지막 FE 및 필수 PG의 대상·실행·결과·재현 경로 확인 | FE·core 필수PG31 통과, 시각 미확인 |
| P2 범위 불명확 | 가입·역할·탈퇴·프로필 원계획 대응과 유지/미확인 근거 | 부분 해소, C 계약 검증 미확인 |

검증 보완 과정에서 제품 변경·배포는 수행하지 않았다. 현재 전체 필수 완료 판정은 보류한다. 이번 수행 가능한 검증은 마무리했으며, 확인된 실패의 수정·재현성 보완과 미확인 계약 해소는 별도 후속 계획이 필요하다.
