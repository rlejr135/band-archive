# FE 중복 구현 정리 결과 — 2026-11-10

## 범위와 결과

승인된 FE 계획에 따라 상세 이동, 활성 멤버십, 재생, 상세 query 조회를 공통화하고 미사용 FE를 삭제했다. BE API·DB·서버 권한·업로드 상태기계·의존성·native 전송 분기는 변경하지 않았다. P3 배열 병합·모달 포커스 공통화와 UI 재디자인은 제외했다.

| 단위 | 커밋 | 결과 |
|---|---|---|
| 경로·활성 멤버십 | e733be5 | 브라우저 bandUiPaths와 isActiveBandMembership 공유. 업로드 원래 밴드 상세 query 이동, 불완전한 대상 비활성화 |
| 공통 재생 | 6d51b5e | 기존 MediaPlayer에서 UploadContext·네트워크 제거. 화면별 재생 구현 교체 |
| 상세 query | ea48763 | useQueryDetail로 ID·응답 밴드 검증, 요청 취소·generation 공유 |
| 빈 미디어 상태 | 0493504 | 성공한 빈 페이지 안내 및 이후 갱신 오류 보존 |
| legacy 삭제·검증 이전 | 36bb097 | 30모듈·17CSS·1story·4전용 테스트 삭제. 현행 계약 테스트 이전 |

추가 회귀 수정 커밋 **42afef1**은 공통 player의 재생 소스 없음 안내를 기존 계약인 “재생 가능한 파일이 없습니다.”로 보존한다. 기존 통합 테스트는 변경하지 않았다.

## 현재 경계

- **브라우저 경로:** services/bandUiPaths.js. API helper bandWorkspacePaths와 구분한다. 곡·합주 상세는 각각 songs?song=ID, rehearsals?rehearsal=ID를 사용한다.
- **활성 가입 관계:** services/bandMembership.js. band.membership에만 적용한다. 객체가 없으면 false, status 생략은 DTO 호환으로 true, 문자열은 소문자 active만 true다. null·빈 값·다른 상태는 false다. 프로필 membership_status와 기능별 서버 capability는 별도다.
- **재생 코어:** components/common/MediaPlayer.jsx. 안정적인 playbackIdentity와 영상·음원 URL·표시명·처리 상태를 입력받는다. provider 없이 렌더링하며 자체 요청을 하지 않는다.
- **응답 adapter:** services/playbackMedia.js::workspaceMediaFile. 미디어와 개인 기록의 이름 차이를 변환하며 안전한 HTTP(S) URL만 사용한다. 처리 상태 응답의 URL은 재생 소스로 사용하지 않는다.
- **화면 wrapper:** 곡/합주 PlaybackMedia, PersonalLogPlayback, 대표 목록·후보 편집기가 댓글·삭제·대표 지정·처리 상태 조회·서명 URL 갱신을 소유한다.
- **상세 query:** useQueryDetail은 요청 identity·query ID·scoped API 경로·응답 추출만 공유한다. query가 없으면 GET 없이 기존 로컬 선택을 사용한다. 곡/합주 UI·폼과 모바일 dialog·가능 일정 연계는 각각 유지한다.

## 화면별 재생 기능

| 화면 | 배속 0.75/1/1.25/1.5 | ±5초 | A–B 반복 | 가능한 영상/음원 전환 |
|---|---|---|---|---|
| 곡 상세 | 제공 | 제공 | 최소 0.25초 | 양쪽 URL이 있을 때 |
| 합주 상세 | 제공 | 제공 | 최소 0.25초 | 양쪽 URL이 있을 때 |
| 개인 연습 기록 | 제공 | 제공 | 최소 0.25초 | 양쪽 URL이 있을 때 |
| 대표 미디어 | 제공 | 제공 | 최소 0.25초 | 양쪽 URL이 있을 때 |
| 대표 후보 미리보기 | 제공 | 제공 | 최소 0.25초 | 양쪽 URL이 있을 때 |

음원만 있으면 음원으로 재생한다. 같은 콘텐츠의 모드·URL 변경은 위치·배속·볼륨·반복·재생 여부를 복원한다. 계정·밴드·상세 대상 변경은 player identity 또는 상위 상세 key가 초기화한다. 같은 콘텐츠 metadata 변경에서는 댓글 초안과 플레이어를 유지한다.

개인 기록은 기존 v2 처리 상태 단건 조회를 유지한다. 완료 상태에서 음원 URL이 없으면 부모 목록을 다시 읽어 URL을 확보한다. 새 polling은 추가하지 않았다. v2 음원 재추출 endpoint가 없으므로 밴드 재생에서는 legacy 재시도를 호출하지 않고 정보 갱신만 제공한다.

## legacy 감사와 보존

제품 main.jsx부터의 import graph와 테스트·Storybook·native·문자열 로딩을 확인했다. 삭제 후 감사는 288개 소스·설정 파일과 상대/literal 경로 616건을 확인했으며, 남은 제품 JS/JSX 116개 모두 main.jsx에서 도달 가능했다. 제거 모듈 참조는 의도한 retirement assertion에만 남는다.

삭제한 파일 52개:

- frontend/src/components/calendar/LocationPicker.jsx
- frontend/src/components/calendar/RehearsalCalendar.jsx
- frontend/src/components/calendar/RehearsalDetail.jsx
- frontend/src/components/calendar/RehearsalModal.jsx
- frontend/src/components/common/AnnouncementToast.jsx
- frontend/src/components/common/CommentSection.jsx
- frontend/src/components/common/MediaDiscussion.jsx
- frontend/src/components/common/PasswordModal.jsx
- frontend/src/components/common/SearchBar.jsx
- frontend/src/components/dashboard/Dashboard.jsx
- frontend/src/components/gallery/Gallery.jsx
- frontend/src/components/layout/AuthStatus.jsx
- frontend/src/components/members/MemberDashboard.jsx
- frontend/src/components/members/MemberDetail.jsx
- frontend/src/components/songs/SongDetail.jsx
- frontend/src/components/songs/SongForm.jsx
- frontend/src/components/songs/SongList.jsx
- frontend/src/components/songs/SongSuggestion.jsx
- frontend/src/context/SongContext.jsx
- frontend/src/hooks/useAsyncData.js
- frontend/src/services/api.js
- frontend/src/services/commentRequestGuard.js
- frontend/src/services/galleryApi.js
- frontend/src/services/mediaVoteChannel.js
- frontend/src/services/mediaVoting.js
- frontend/src/services/memberApi.js
- frontend/src/services/rehearsalApi.js
- frontend/src/services/rehearsalUploadQueue.js
- frontend/src/services/voterIdentity.js
- frontend/src/services/youtube.js
- frontend/src/components/calendar/LocationPicker.css
- frontend/src/components/calendar/RehearsalCalendar.css
- frontend/src/components/calendar/RehearsalDetail.css
- frontend/src/components/calendar/RehearsalModal.css
- frontend/src/components/common/AnnouncementToast.css
- frontend/src/components/common/CommentSection.css
- frontend/src/components/common/PasswordModal.css
- frontend/src/components/common/SearchBar.css
- frontend/src/components/dashboard/Dashboard.css
- frontend/src/components/gallery/Gallery.css
- frontend/src/components/members/MemberDashboard.css
- frontend/src/components/members/MemberDetail.css
- frontend/src/components/songs/SongDetail.css
- frontend/src/components/songs/SongForm.css
- frontend/src/components/songs/SongList.css
- frontend/src/components/songs/SongMedia.css
- frontend/src/components/songs/SongSuggestion.css
- frontend/src/components/common/SearchBar.stories.jsx
- frontend/src/services/commentRequestGuard.test.js
- frontend/src/services/rehearsalUploadQueue.test.js
- frontend/src/services/youtube.test.js
- frontend/src/services/mediaVoting.test.js

RehearsalTypeBadge·story, 공통 플레이어와 practicePlayback·commentTimestamp·mediaPresentation, FileUpload·UploadContext·useMediaUpload·mediaUploadManager·uploadTransport·nativeUploadQueue를 보존한다. native/v1 전송 호환 분기와 BE v1 endpoint는 이번 삭제 대상이 아니다. 추가로 발견된 고아 Header.css는 승인된 30개 후보 범위를 넘어 삭제하지 않았다.

## 테스트 대응표

| 기존 검증 | 처리·현재 대응 |
|---|---|
| commentRequestGuard.test.js 10개 | 과거 댓글 UI 전용 helper와 삭제. 현행 MediaEngagement DOM의 역순·실패·중복 제출 검증 유지 |
| rehearsalUploadQueue.test.js 3개 | 과거 달력 queue와 삭제. uploadJobRegistry·nativeUploadQueue·UploadPolicy 중복 방지 검증 유지 |
| youtube.test.js 2개 | 과거 embed validator와 삭제. 현행 safeExternalUrl 검증 유지 |
| mediaVoting.test.js 10개 | 과거 UUID/CAS PATCH/BroadcastChannel/비밀번호 promote와 삭제. 현행 v2 scoped PUT·계정 기반 voteTarget/MediaEngagement 검증 유지 |
| songDataFlow.test.js | 삭제 파일 요구를 현행 App route 연결·30모듈 및 SearchBar story 재진입 방지 검사로 교체 |
| locationPickerApi.test.js | BandPlacePicker의 인증·밴드 범위 검색·수동 입력 계약으로 이전 |
| apiConfig.test.js | 환경 설정 네 경우와 실제 config/client 경계 검사 유지. 삭제한 api.js alias 의존만 제거 |
| RehearsalType.dom.test.js | 현행 form/detail/list 6개: enum, REGULAR 생성, unknown/생략 유형 보존, LIVE 변경, 상세/목록 badge |
| UploadCenter 경로 문자열 검사 | 실제 AppRoutes에서 원래 밴드의 대상 곡 상세가 표시되는 통합 테스트 추가 |
| 신규 공통 기능 | bandMembership·bandUiPaths·playbackMedia·PracticeMediaPlayer·QueryDetail 테스트를 현재 test script 수집 위치에 추가 |

## 검증 기록

- 1단계 helper·UploadCenter 9 passed, 실제 라우트·멤버 UI matrix 13 passed.
- 최종 재생 집중 테스트 40 passed / 0 failed / 0 skipped.
- query 격리 테스트 3 passed. 현행 합주 유형 이전 6 passed.
- 삭제·이전 집중 34 passed / 0 failed / 0 skipped.
- 빈 미디어·갱신 실패 포함 상세 수명 집중 22 passed / 0 failed / 0 skipped.
- 재생 소스 fallback 기존 통합 테스트: 32 passed / 0 failed / 0 skipped.
- 전체 FE suite: **498 passed / 0 failed / 0 cancelled / 0 skipped / 0 todo**, 552.317초. 최초 실행에서 기존 fallback 안내 문구 회귀 1건을 발견해 중단했고, 제품 수정 후 처음부터 완료했다.
- 최종 변경 JS/JSX **39개 lint 통과**, 오류·경고 0개.
- production build: **성공**, Vite 258 modules, 3.29초. JS `index-D3H0QJai.js`(860.89 kB), CSS `index-BynS0R9A.css`. 번들의 Fly API 주소와 playbackIdentity·재생 정보 갱신 마커 확인.
- Storybook build: **성공**, preview Vite 89 modules, 5.07초. production·Storybook 모두 500 kB 초과 청크 경고가 있으며 오류는 없다. 이번 범위에서 번들 분할은 추가하지 않았다.
- git diff --check: 작업 범위 통과. 기존 CRLF 안내는 별도다.

최초 baseline 전체 실행은 느린 Vite 시작 때문에 중단했으며 최종 전체 suite를 별도로 실행한다. 이전 테스트의 fixture 오류(UploadContext subscribe 누락, 날짜 입력 이벤트·POST helper 경로 기대)는 현행 계약을 유지하여 수정했고 실패 assertion을 제거하거나 약화하지 않았다. 빈 미디어 테스트에서 발견한 실제 UI 오류는 별도 동작 커밋으로 수정했다.

## 시각 검증의 한계

로컬 합성 미디어 fixture를 준비했으나 in-app browser가 Windows localhost에 연결하지 못했다. Windows Chrome 연결은 Computer Use가 현재 browser URL을 정책 적용에 충분한 확신으로 확인하지 못해 도구가 중단했다. 추가 UI 자동화를 시도하지 않고 자체 fixture 서버를 종료했다.

따라서 PC·모바일 실화면의 시각 검증과 실제 브라우저 codec·오디오 출력 확인은 **미완료**다. DOM의 재생 상태·전환·거부·만료/갱신 및 기존 모바일 화면 동작 검증과 구분한다. 시각 검증을 통과했다고 주장하지 않는다.

## 반영·배포

- 최신 origin/master fetch 및 fast-forward 확인 후 제품 **42afef1**을 `git push origin HEAD:master`로 반영했다. force push·수동 Wrangler·인프라 변경은 하지 않았다.
- Cloudflare Pages 자동 배포 확인: **2026-11-10 23:47:07 KST**(14:47:07 UTC).
- `https://band-archive.pages.dev/?v=...`: HTTP **200**.
- 라이브 `/assets/index-D3H0QJai.js`: HTTP **200**. 로컬 production asset과 같은 파일명이며 API 주소 `https://band-archive.fly.dev`, `playbackIdentity`, `재생 정보 새로고침` 마커가 모두 확인됐다.
- 기존 read-only `https://band-archive.fly.dev/members`: HTTP **200**, `Access-Control-Allow-Origin: https://band-archive.pages.dev`.
- 최초 확인에서는 이전 asset을 제공했고 비동기 배포 후 다시 확인해 새 asset을 검증했다. sandbox DNS 제한은 승인된 외부 HTTP 조회로 해결했다. 운영 응답 본문·비밀은 출력하지 않았다.
- 로컬 JS SHA-256: `5005FA39429FCC1BC546B465A4F6A366369996403FEFCC5AE4B979246A5420D1`.
- FE만 변경했으므로 **BE는 재배포하지 않았다**. 문서 기록 커밋은 검증된 제품 커밋과 별도로 반영한다.
- 기존 사용자 AGENTS.md·k3s/secrets.yaml과 무관한 산출물을 보존했다. DB·R2 원본·계정 설정을 변경하지 않았다.

자동 검증·배포 결과와 별개로 위 시각 검증 한계는 남는다. PC·모바일 실화면 및 실제 codec/오디오 출력 확인을 포함한 모든 검증이 완료됐다고 해석하지 않는다.

재현 명령은 anything-regression-validation 스킬의 bundled Node·현재 test glob을 사용한다. 최종 로그는 로컬 frontend의 `final-fe-suite-rerun-20261110.log`, `final-fe-lint-20261110.log`, `final-fe-build-20261110.log`, `final-fe-storybook-20261110.log`에 보존한다.

