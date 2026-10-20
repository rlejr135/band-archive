# 밴드 멤버 추방 및 owner/superadmin 용어 기록

작성일: 2026-10-20. 검증 갱신: 2026-10-21. 상태: BE·FE 전체 회귀, 추가 생성 화면 검증 및 운영 배포 확인 완료.

## 요청과 권한 경계

- `AGENTS.md`에 앞으로 사용자 요청의 owner 권한은 해당 밴드 owner와 소속 무관 superadmin을 포함한다고 기록했다. superadmin은 활성·이메일 확인 완료 계정의 서버 DB 권한으로 판정하며 이메일/수정 가능한 JWT metadata로 승격하지 않는다.
- 이번 기능에 한정한 제거 권한 helper를 추가했다. 기존 `require_band_role`을 전역 우회하거나 기존 모든 owner API의 권한을 일괄 확대하지 않았다.
- owner는 밴드 관리 → 멤버 역할에서 추방한다. 비소속 superadmin은 플랫폼 관리 → 밴드 멤버 관리에서 밴드 ID로 최소 활성 명단을 조회한 뒤 추방한다.
- 일반 band admin/member는 추방할 수 없다. 본인·현재 owner는 추방할 수 없으며, owner 변경은 기존 소유권 이전 절차를 사용한다.
- 영구 차단은 아니다. 추방 후 가입 요청 또는 유효한 초대 링크로 다시 가입할 수 있다.

## 구현 계약

- `POST /api/v2/bands/{bandId}/memberships/{membershipId}/remove`: 정확한 `{confirm:true}` body(최대 1KB), band-scope·현재 DB 권한·대상 자격을 서버에서 다시 확인한다. 다른 밴드 대상은 404다.
- 가입 상태만 기존 `left`로 바꾸고 `left_at`, 처리자, `membership_removed` 감사 기록을 남긴다. 이미 left인 대상 재시도는 추가 감사 기록을 만들지 않는다.
- 계정·멤버 프로필·곡·합주·댓글·투표·미디어·R2 객체를 삭제하지 않는다. 새 DB 컬럼/테이블/migration은 필요하지 않다.
- `GET /api/v2/platform/bands/{bandId}/memberships`는 superadmin 전용 최소 활성 명단이다. 이메일·계정 UUID·가입 소개·미디어 URL·비공개 콘텐츠를 반환하지 않는다. 프로필과 표시 이름이 없는 기존 계정은 '멤버'로 표시한다.
- 기존 관리 명단에는 밴드 프로필 이름과 서버의 행별 `can_remove_from_band`를 추가한다. 일반 멤버 명단 응답은 확대하지 않는다.
- FE는 서버의 행별 권한으로 버튼을 표시한다. 확인 문구, 중복 클릭 방지, 계정/밴드 전환 시 abort·늦은 응답 차단, 결과 불명 시 재전송 잠금과 명단 재확인을 적용한다. 성공 후 명단 갱신 문제를 추방 실패로 오인하지 않는다.

## 검증 및 커밋

- `7a22c88`: owner/superadmin 해석을 프로젝트 지침에 기록.
- `33b1c44`: 추방 서비스·API·최소 명단과 보존/권한/PG 경합 테스트.
- `2315643`: owner 추방 UI·독립 플랫폼 멤버 관리·FE 회귀.
- `38ca93c`: 이름 없는 기존 계정의 최소 명단 호환성과 회귀.
- `4f16f62`: 생성 화면 반환 링크·밴드 영어명 안내와 동일 조건의 DOM assertion 출력 개선.
- BE 집중 64개 통과(99.28초). 실제 소유 로컬 PostgreSQL의 owner/superadmin 각각 소유권 이전↔추방 양방향 경합 4개, HEAD 대기 중 추방 후 완료 거부 1개, materialization이 먼저 잠금을 획득한 경우 완료 미디어 보존 1개를 포함한다. 실제 잠금 대기를 관찰하며 임의 sleep 순서만으로 경쟁을 주장하지 않는다.
  - 로그: `backend/tools/.state/validation-20261017-3663f57efde344d6bc243500185fa2fc/stdout.log`
  - SHA-256: `d552ef233b8c6ecd1106e927125cfac016ec8d4ce2485cc137cfdd5de29d343d`
- FE 전체 323/323, 변경 JS lint 및 총괄 독립 최종 build 통과. JS `index-DJem0imL.js`, CSS `index-B7NRcDeh.css`. 기존 500KB 청크 경고는 남는다.
- 총괄이 실제 mock 컴포넌트의 PC/320px 스크린샷을 확인했다. 기본 브라우저 버튼 방지 스타일, 권한별 버튼 유무, 모바일 가로 넘침 없음을 확인했다. 운영 추방은 실행하지 않았다.
  - 로컬 화면: `frontend/.state/member-removal-preview/desktop.jpg`, `mobile-320.jpg`.
- 첫 BE 전체는 180 통과 후 기존 PostgreSQL fixture의 initdb 메모리 할당 오류로 중단됐다. 이를 통과로 간주하거나 테스트를 제외하지 않았다. 동시에 FE 측 Windows DLL 초기화 실패도 있었으며, FE 최종 lint/build/전체 테스트는 이후 통과했다. 운영은 변경하지 않았다.
- 다른 테스트 종료 후 이름 누락 회귀를 포함한 추방 API와 실패했던 기존 PG 테스트를 단독 검증해 24개 통과(18.20초)를 확인했다. SHA-256 `a8cfb77a192506d8607d13f6add6869e34fdf206c3b805d43625dfb755dfdd7b`.
- 최종 BE 전체 단독 재실행 **1,029 통과·실패/오류 0**(975.77초, 경고 924건). PostgreSQL 검증을 제외하거나 약화하지 않고 끝까지 통과했다.
  - 로그: `backend/tools/.state/validation-20261017-e97fdf4e9a6a47fc8e2df8e000e03843/stdout.log`
  - SHA-256: `a8b06433636126fa745723584c4642c16ba5c89542f8d16679fe6ffd4bb26fb1`
- 이후 사용자 추가 FE 요청: 생성 요청 화면의 내 밴드 링크를 공통 반환 링크 스타일로 변경하고 slug 라벨을 '밴드 영어명', 도움말을 '해당 이름이 밴드 URL 주소가 됩니다' 및 실제 허용 문자 안내로 변경한다. 서버 필드명·허용 규칙은 유지하며 BE 재수정이나 DB 변경은 하지 않는다. 추가 FE 전체 검증 결과는 확정 후 기록한다.
- 추가 FE 검증에서 DOM 테스트 프로세스의 V8 메모리 오류를 발견했다. 비동기 `waitFor`의 실패 메시지가 React DOM/Fiber 객체를 깊이 출력하는 경로를 확인했다. DOM과 null/동일 객체를 직접 비교하던 4곳을 논리적으로 동일한 boolean 비교로 바꿨다. assertion·조건·timeout·테스트 범위를 줄이지 않았고 heap/시스템 설정도 변경하지 않았다. watcher 비활성화 가설은 효과가 없어 원복했다. 해당 파일은 우선 29/29 통과(13.28초)했으며 최종 전체 검증은 별도로 수행한다.

- 추가 요청 포함 최종 FE 전체 **324/324 통과·실패/skip 0**(103.10초), 변경 JS 6개 lint 통과, production build 통과(202 modules, 2.63초). 최종 로컬 JS `index-Cv2xTFqK.js`, CSS `index-B7NRcDeh.css`. 500KB 이상 chunk 경고는 남으며 오류가 아니다. 테스트 복구는 DOM 출력 방식만 변경했고 watch 설정은 원복했다.

## 업로드·URL 경계

- 진행 중 업로드는 추방을 막지 않는다. band 잠금 안에서 현재 가입을 다시 검사하므로 추방이 먼저 커밋되면 완료/새 접근이 거부된다. 완료가 먼저 끝나면 생성 콘텐츠는 그대로 보존한다.
- 이미 발급된 서명 URL은 기존 만료 시간까지 유효할 수 있다. 추방은 이미 본 데이터나 발급 URL을 즉시 회수하는 기능이 아니다.
- direct 완료 중 권한 거부는 기존 initiated 복구/만료 정리를 사용하고 multipart 권한 거부는 failed로 종결한다. 완료 시도 전 multipart 예약은 기존 만료·복구 정리까지 유지될 수 있어 즉시 쿼터 반환을 보장하지 않는다.
- R2 multipart 완료가 먼저 끝난 뒤 권한 재검증에서 거부되면 콘텐츠 레코드 없는 객체가 남을 수 있는 기존 경계는 유지한다. 이번에 R2 정리·abort·파일 삭제를 추가하지 않았다.

## 운영 반영

최종 전체 검증 후 상시 승인 범위로 Fly BE → origin/master 일반 push/Cloudflare FE 순서로 배포하고 HTTP·CORS·실제 번들을 확인한다. 비밀 설정·DB 스키마·R2·사양·메일 활성화는 변경하지 않는다. 실제 사람을 운영 검증 목적으로 추방하지 않는다.

- 2026-10-21 KST Fly **v63 complete**, 이미지 `registry.fly.io/band-archive:deployment-01M5CXC0BEE57PEBTPHAWGXFMS`, machine `080e444f9643d8` started, NRT, shared 2 CPU/2048MB. 기존 사양·볼륨 유지. 별도 Fly CHECKS는 미설정이며 배포 smoke check와 HTTP로 검증했다.
- `/members` HTTP 200, `Origin: https://band-archive.pages.dev` CORS 허용. 최신 Gunicorn 시작·worker 부팅 확인, 최신 시작 이후 확인 대상 오류 0건.
- 토큰 없는 새 플랫폼 명단 GET 및 추방 POST는 각각 401, `no-store, private`, `Vary: Authorization`과 Pages CORS를 확인했다. 실제 추방은 하지 않았다.
- `origin/master` 일반 push: `5a7b875` → `4f16f62`. Cloudflare Pages 실제 HTML·JS·CSS 모두 HTTP 200. 운영 JS `/assets/index-Cv2xTFqK.js`, CSS `/assets/index-B7NRcDeh.css`로 로컬 최종 빌드와 일치한다. `can_remove_from_band`, '밴드 멤버 관리', '밴드 영어명', '해당 이름이 밴드 URL 주소가 됩니다'와 Fly API 주소를 실제 번들에서 확인했다.
- 운영 계정으로 실사용 추방을 수행하지 않았다. UI 동작·권한별 거부·콘텐츠 보존은 로컬 회귀와 mock 화면으로 검증했고, 운영에서는 인증 없는 차단 및 정적 배포·HTTP·CORS·기동만 검증했다.
