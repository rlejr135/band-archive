# 아키텍처 리팩터링 검증 보완

## 대상과 판정 기준

- 조사일: 2026-11-05.
- 대상 제품: `d5cae8795f9916ea68ba963ad07d10c3af58cb07`. 조사 HEAD: `63ea386e3b6499358296f5e65151e2b0a0038181`. 실행 cwd `E:/Anything`에서 `git diff d5cae8795f9916ea68ba963ad07d10c3af58cb07 63ea386e3b6499358296f5e65151e2b0a0038181 --name-only -- band-archive/backend` 결과 차이 없음. 아래 코드 경로는 별도 표시 없으면 `E:/Anything/band-archive` 기준이다.
- 지시서: `docs/plans/2026-11-05-architecture-refactoring-follow-up-instructions.md` C절. 원계획 `docs/plans/2026-10-25-architecture-refactoring-plan.md:148`의 가입·역할·탈퇴·프로필, addendum `docs/plans/2026-10-25-architecture-refactoring-plan-addendum.md` 6절의 transaction 책임 기준에 대응한다.
- 용어: 계정 AppUser, 가입·역할 BandMembership, 밴드별 멤버 프로필 MemberProfile(현 모델 Member)은 별개다.
- 분류: 전환 완료 / 기존 구조가 기준을 충족해 유지 / 명시적 제외·보류 / 미확인·결함. 마지막 분류의 ‘미확인’은 결함·악용 가능성이 확정되었다는 의미가 아니다.
- 이 C 조사에서는 코드·테스트를 읽었으며 테스트 실행, 제품 변경, 운영 접근은 하지 않았다. 아래 노드의 존재는 실행 성공 증거가 아니다.
- A runtime 대응 검증: 다른 담당자의 실제 결과 반영 전 **미확인**.
- B 마지막 FE 실행 통과. 필수 PG와 이번 시각 비교는 미확인(아래 B절).

## A. 운영 runtime 대응 검증 — 준비 결과

다음은 총괄 담당자가 전달한 실행 사실이며, C 조사자가 별도로 실행한 결과는 아니다.

- Fly 읽기 전용 `image show`로 machine `080e444f9643d8`의 image `registry.fly.io/band-archive:deployment-01M6K9R0A7RRYDCGVQBFGZX6E4`, digest `sha256:156133750a9ec26589a677023d66142f9d097e7b94e1659ea226a93e118cd376`가 기존 v91 기록과 일치함을 확인했다. 실제 Python patch 버전·OS/아키텍처·설치 패키지 manifest는 아직 확보하지 못했다. image 연결 확인과 runtime 동등성 검증을 구분한다.
- `DockerDesktopLinuxEngine` pipe가 없었다. Docker Desktop 숨김 실행 요청 후에도 해당 process가 확인되지 않았고 Docker status가 응답하지 않았다. 작업이 소유한 CLI 두 개만 Ctrl+C로 종료했다. 사용자에게 Docker Desktop 직접 실행을 요청할 단계이며 기존 개발 자원은 종료하지 않았다.
- `backend/tools/verification/`의 재현 도구 5개(`prepare_snapshot.py`, `runtime_manifest.py`, `run_isolated.py`, `bootstrap/sitecustomize.py`, `README.md`)는 총괄 리뷰를 완료했고 지원 도구 커밋 `6b9f2e1`로 보존했다. AST 검사 4개가 통과했다. 검증 실행 전 준비물이지 통과 증거가 아니다.
- `prepare_snapshot.py` 실행은 성공했다. 소유 출력 경로는 `backend/tools/.state/followup-reviewed-source-20261105`이며 fixture 경로와 실행 파일 선택의 검증 환경 차이만 확인했다. `run_isolated.py --help` 성공은 CLI 진입 확인이다. pytest·운영 runtime 대응 계약 통과를 뜻하지 않는다.
- 로컬 Python 3.12 가상환경에서 `runtime_manifest` capture와 같은 파일끼리 compare가 성공했다. 이는 CLI 작동 검사일 뿐 운영 manifest 확보·비교 일치 또는 runtime 대응 검증 증거가 아니다.

### A-3 필수 기준표

아래 기준은 지시서 A-3에서 고정했으며 준비 상황에 맞춰 완화하지 않는다. 현재 이 실행의 실제 pytest 결과는 모두 미확인이다.

| 묶음 | 필요한 실행 증거 | 현재 상태 |
|---|---|---|
| 일반 DB 유스케이스 | 중간 실패·flush·audit·commit 오류 원자성, callback-before-commit, commit 후 JSON 실패 의미 | 미실행/미확인 |
| 실제 PG 경쟁 | 곡 제안 채택·투표·부모 삭제·권한 변경의 독립 연결, DB 잠금 대기와 최종 저장 결과 | 미실행/미확인 |
| 업로드 시작·완료 | 예약·동시 시작·실제 크기·완료 직전 재인가·멱등 완료·단계별 commit | 미실행/미확인 |
| 복구 | timeout/HEAD/재시도·materialization 후 중단·결과 재조회·시작 복구 schema/flag gate | 미실행/미확인 |
| 접근·signing | 차단·삭제 대기·pruned 원본 signer 미호출·소스 선택·TTL·밴드/부모 관계 | 미실행/미확인 |

**A 판정: 미해소.** runtime 환경 미확보와 필수 계약 미실행이 남았다. 운영 공개 HTTP 200·도구 준비 성공으로 대체하지 않는다.

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

과거 공개 Wave4 보고서는 24회 비교 중23 동일·1 bounce를 기록했으나 edit candidate는 미검증이었다. 이는 과거 증거이며 이번 새 비교 결과가 아니다. 이번에는 CUA browsers가 빈 배열이고 IAB unavailable이라 시각 비교를 실행하지 못했다. 소유 fixture 준비만 했고 서버·스크린샷을 생성하지 않았다. 다른 플랫폼 우회나 새 시각 회귀 체계는 도입하지 않았다. 따라서 PC/모바일·빈 목록·오류·폼 열림 등 이번 전후 시각 동일성은 **미확인**이다.

## B-3. 실제 PG 실행 증거

이번 실행에서 PG 테스트는 **실행하지 않았다**. 아래는 후속 실행 대상이며 과거 전체1738 passed를 이 node의 이번 실행 증거로 사용하지 않는다.

| 필수 시나리오 | 정확한 node/목록 상태 | 대상·runtime | 이번 결과 |
|---|---|---|---|
| 가입 프로필 동시 승인 | `tests/test_member_profiles_postgres.py::test_profile_concurrent_approval_has_one_linked_record` | d5cae879 / 운영 대응 runtime 미확보 | 미실행 |
| 역할·소유권·탈퇴 순서 | `tests/test_band_leave_postgres.py::test_postgres_transfer_role_and_leave_serialize_band_first` | 동일 | 미실행 |
| 추방·소유권 경쟁 | `tests/test_member_removal_postgres.py::test_removal_and_target_owner_promotion_serialize` | 동일 | 미실행 |
| 외부 확인 중 추방 | `tests/test_member_removal_postgres.py::test_removal_during_external_upload_verification_prevents_materialization` | 동일 | 미실행 |
| 완료 잠금 선행·보존 | `tests/test_member_removal_postgres.py::test_completion_lock_wins_then_removal_retains_completed_media` | 동일 | 미실행 |
| 초대 중복 수락·폐기 경쟁 | `tests/test_invite_link_concurrency.py::test_concurrent_duplicate_accept_is_atomic_and_idempotent`, `::test_revoke_and_accept_have_one_serialized_order` | 동일 | 미실행 |
| A-3 곡 제안·투표·부모 삭제·업로드·복구·signing | 정확한 수집/실행 node 표는 runtime 환경 확보 후 확정 필요; 위 C 관련 node만으로 대체 안 함 | 동일 | 미실행/목록 미확정 |

## B-4. 재현 절차 준비

공개 보존할 최소 도구는 `backend/tools/verification/`에 준비되어 있으며 총괄 리뷰·기본 도구 확인을 완료했다(지원 커밋 `6b9f2e1`). 기존 private runner 경로·stdout hash만으로 재현을 요구하지 않는 방향이다. 현재 확인은 소유 snapshot 준비 성공과 CLI help까지다. 실제 pytest 인자·opt-in 변수·로컬 PG 준비/정리·node별 결과는 실행 후 확정해야 하며 아직 통과 결과가 없다.

**B-4 판정: 준비 부분 해소.** 실행 재현 도구 준비를 FE 결과·PG 실행 증거 확보로 계산하지 않는다. FE 결과는 B-1, 시각·PG 한계는 B-2/B-3에 기록한다.

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

C 지적은 **부분 해소**다. 코드 위치·업무 경계·미확인 범위의 대응을 명시했지만, 원계획의 필수 대상을 모두 충족했다는 결론은 보류한다.

## 최종 완료 게이트

| 지적 | 완료 조건 | 현재 판정 |
|---|---|---|
| P1 runtime 차이 | 고정 제품과 연결된 runtime 대응 환경에서 A-3 필수 계약 실제 실행·통과, 차이·fake 범위 기록 | 미해소 |
| P2 실행 증거 | 마지막 FE 및 필수 PG의 대상·실행·결과·재현 경로 확인 | FE 실행 통과, PG·시각 미확인, B-4 준비 부분 해소 |
| P2 범위 불명확 | 가입·역할·탈퇴·프로필 원계획 대응과 유지/미확인 근거 | 부분 해소, C 계약 검증 미확인 |

검증 보완 과정에서 제품 변경·배포는 수행하지 않았다. 현재 전체 필수 완료 판정은 보류한다.
