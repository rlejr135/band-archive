# 프론트엔드

## 역할과 운영 현황

`band-archive/frontend`는 밴드 **들뜬 Archive**의 브라우저 사용자 화면이다. 곡과 미디어, 합주 일정, 멤버 연습 기록, 추천곡, 갤러리, 공지를 관리한다.

현재 운영 정보는 프로젝트 소유자가 확인한 기준으로 **프론트엔드는 Cloudflare에서 제공**된다. 다만 이 디렉터리의 코드에는 Cloudflare Pages/Workers 설정 파일이나 배포 워크플로가 없으므로, 구체적인 배포 방식·캐시 정책·도메인은 이 문서에서 단정하지 않는다.

## 기술 스택

- React 19, React DOM 19, React Router DOM 7
- Vite 7 및 `@vitejs/plugin-react`
- `react-calendar` 5로 합주 일정 캘린더 구현
- CSS 파일을 컴포넌트별로 직접 import하는 방식(별도 UI 프레임워크·CSS-in-JS 없음)
- 브라우저 내장 `fetch`, `XMLHttpRequest`(업로드 진행률용), `FormData`
- ESLint 9 + React Hooks/React Refresh 플러그인
- 컨테이너 배포 선택지: Node 20 Alpine으로 Vite build 후 Nginx stable Alpine에서 정적 파일 제공

## 디렉터리 구조

```text
frontend/
├─ src/
│  ├─ main.jsx                 # StrictMode, BrowserRouter, React root
│  ├─ App.jsx                  # 전역 레이아웃과 URL 라우트
│  ├─ context/SongContext.jsx  # 곡 목록·선택·편집 상태 및 곡 API 동작
│  ├─ hooks/useAsyncData.js    # 화면별 비동기 조회 공통 hook
│  ├─ services/                # 백엔드 HTTP API 및 R2 직접 업로드 호출
│  ├─ components/
│  │  ├─ dashboard/            # 통계, 대표 사진, 합주 캘린더
│  │  ├─ songs/                # 곡 목록/등록/상세, 추천곡
│  │  ├─ calendar/             # 일정 CRUD, 장소 선택, 일정 미디어
│  │  ├─ members/              # 멤버와 개인 연습 기록
│  │  ├─ gallery/              # 사진 갤러리
│  │  ├─ common/               # 업로드, 플레이어, 댓글, 공지 등
│  │  └─ layout/               # 헤더 스타일
│  ├─ assets/                  # 로고 등 번들 자산
│  └─ *.css                    # 전역·화면·컴포넌트 스타일
├─ public/                     # 정적 공개 자산
├─ .env.example                # 필요한 개발 환경변수의 이름 예시
├─ .env.production             # production Vite 환경 파일
├─ vite.config.js
├─ Dockerfile
└─ nginx.conf
```

## 라우팅과 화면 전환

`main.jsx`가 `BrowserRouter`로 전체 앱을 감싼다. 정적 호스팅 환경은 새로고침 경로를 앱으로 돌려줘야 하며, 컨테이너용 `nginx.conf`에는 이를 위한 `try_files ... /index.html` fallback이 있다.

| 경로 | 화면 | 설명 |
| --- | --- | --- |
| `/` | `Dashboard` | 곡 통계·상태별 곡·대표 사진·합주 캘린더 |
| `/songs` | `SongPage` | 검색 가능한 곡 목록과 선택/등록 진입 |
| `/songs/:id` | `SongPage` | URL의 id와 선택 곡 상태를 동기화한 상세 화면 |
| `/suggestions` | `SongSuggestion` | 다음 곡 제안, 투표, 삭제 |
| `/members` | `MemberDashboard` | 멤버 목록과 등록 |
| `/members/:id` | `MemberDetail` | 멤버 상세와 개인 연습 기록 |
| `/gallery` | `Gallery` | 갤러리 조회·업로드·대표 설정 |
| 그 외 | `/` | `Navigate`로 대시보드 이동 |

헤더 버튼은 라우터 탐색으로 동작한다. 곡 페이지는 넓은 화면에서 좌측 목록/우측 상세 또는 폼으로 표시하고, 모바일에서는 선택·편집 상태에 맞춰 표시를 바꾼다.

## 상태 관리

전역 상태는 `SongProvider` 하나다. 이 Context는 곡 목록, 로딩/오류, 현재 선택 곡, 생성·수정 모드를 들고 있으며 곡 CRUD와 미디어 갱신 뒤 목록과 선택 항목을 갱신한다.

그 밖의 화면 상태(대시보드 통계, 일정, 멤버, 갤러리, 모달, 입력값, 업로드 진행률)는 각 컴포넌트의 `useState`/`useEffect`로 관리한다. `useAsyncData`는 멤버 목록·개인 기록에서 조회 결과, 로딩, 오류, 재조회 함수를 묶어 제공한다. 별도 서버 상태 라이브러리, 전역 스토어, 캐시 무효화 계층은 없다.

## 서비스/API 계층

모든 API의 기본 주소는 `src/services/api.js`의 `VITE_API_URL`이며 미설정 시 로컬 백엔드 주소를 사용한다. 서비스 파일은 공통 HTTP 클라이언트 없이 직접 `fetch`를 호출하며, HTTP 실패 시 `Error`를 던진다.

| 모듈 | 책임 |
| --- | --- |
| `api.js` | 곡·미디어·대시보드·추천곡·공지·댓글 API, 미디어 업로드 진입 |
| `rehearsalApi.js` | 합주 일정 CRUD, 일정별 미디어, 일정에서의 미디어 업로드 |
| `memberApi.js` | 멤버 CRUD 및 개인 연습 기록 업로드/삭제 |
| `galleryApi.js` | 갤러리 조회·업로드·삭제·대표 이미지 지정 |
| `uploadApi.js` | presigned URL 발급 → R2 PUT 업로드 → 백엔드 메타데이터 완료 등록 |

미디어·갤러리 파일은 브라우저가 백엔드에서 presigned URL을 받은 뒤 R2에 `XMLHttpRequest` PUT으로 직접 전송한다. 업로드 진행률을 표시할 수 있고, 완료 후에는 백엔드에 파일명·원본명·크기와 연결 대상 메타데이터를 등록한다. 반면 멤버 개인 기록은 현재 `FormData`를 백엔드로 직접 POST한다.

## 주요 기능과 사용자 흐름

- **곡 아카이브**: 제목·아티스트 검색 → 상세 확인 → 곡 정보, 코드, 메모 수정 → 미디어 업로드·이름 변경·삭제·대표 지정 → 필요 시 합주 일정 연결.
- **미디어 재생**: 비디오/오디오/이미지/문서를 확장자와 서버 `file_type`으로 구분한다. 서버가 `qualities`를 내려주면 화질을 선택하며, 비디오의 오디오 품질은 라디오 모드로 재생한다. 변환 대기·진행 상태도 표시한다.
- **합주 관리**: 월별 일정 조회 → 특정 날짜 또는 기간 일정 확인 → 일정 생성·수정·삭제 → 장소 선택기에서 네이버 지도 클릭, 역지오코딩, 장소명 검색으로 좌표와 주소를 기록 → 일정에 곡별 미디어 업로드.
- **네이버 지도 연동**: `LocationPicker`가 Maps JavaScript SDK를 동적으로 로드하고 geocoder를 사용한다. 장소 검색은 백엔드의 `/api/search-places`에 요청하며, 일정 상세에서는 네이버 지도 검색 링크도 제공한다.
- **멤버/연습 기록**: 멤버를 등록하고 상세에서 개인 연습 기록을 업로드·재생·삭제하며, 기록에는 댓글을 남길 수 있다.
- **참여 기능**: 추천곡을 등록하고 투표·삭제할 수 있으며, 미디어와 개인 연습 기록에 댓글과 답글을 작성·수정·삭제할 수 있다. 공지는 화면 상단 토스트에서 조회하고 수정한다.
- **대시보드/갤러리**: 전체 곡 수와 상태별 목록, 대표 갤러리 사진, 합주 캘린더를 한 화면에서 보여준다.

## 환경변수

클라이언트에 노출되는 Vite 환경변수이므로 비밀값을 넣으면 안 된다.

| 이름 | 용도 |
| --- | --- |
| `VITE_API_URL` | Flask 백엔드의 공개 API 기준 URL |
| `VITE_NAVER_MAP_CLIENT_ID` | 네이버 Maps JavaScript SDK의 클라이언트 ID |

`VITE_NAVER_MAP_CLIENT_ID`가 없거나 placeholder이면 장소 선택기는 오류를 표시한다. 장소 검색은 지도 SDK가 아닌 백엔드 경유 API를 사용하므로, 네이버 검색 API의 서버 자격 증명은 프론트엔드 환경변수에 넣지 않아야 한다.

## 로컬 실행·빌드·검증

Node.js 20 계열을 사용한다(Dockerfile 기준).

```powershell
cd E:\Anything\band-archive\frontend
npm ci
Copy-Item .env.example .env.local
npm run dev
```

`VITE_API_URL`은 실행 중인 백엔드 주소로 설정한다. 지도 기능도 확인하려면 `VITE_NAVER_MAP_CLIENT_ID`를 설정하고 해당 키에 개발 origin을 등록해야 한다.

```powershell
npm run lint
npm run build
npm run preview
```

`lint`는 ESLint 정적 검사를 수행하고, `build`는 `dist/` 정적 산출물을 만든다. `preview`는 빌드 결과의 로컬 확인용이다. 현재 `package.json`에는 단위·통합·E2E 테스트 스크립트가 없다.

## 현재 한계와 위험

### 보안

- 프론트엔드 API 호출에는 인증 헤더·세션 처리·권한 제어가 없다. 곡, 일정, 멤버, 갤러리, 공지, 미디어의 변경 UI가 노출되어 있으므로 실제 보호는 반드시 백엔드가 강제해야 한다.
- `PasswordModal`의 기본 검사값이 소스 코드에 존재한다. 클라이언트의 비밀번호 비교는 누구나 번들에서 볼 수 있어 접근 통제가 될 수 없다.
- 댓글과 추천곡 삭제는 비밀번호를 요청 본문으로 보낸다. HTTPS 및 서버 측 해시 검증·rate limit·권한 정책이 전제되지 않으면 탈취·대입 공격 위험이 있다.
- `VITE_*` 값은 번들에 포함된다. 지도 클라이언트 ID는 공개 식별자로 취급하고, API 비밀키·R2 자격증명·네이버 검색 비밀값은 넣지 않는다.
- presigned URL 흐름은 브라우저가 R2에 직접 업로드한다. URL 발급 API가 파일 형식·크기·사용자 권한·만료를 서버에서 엄격히 제한하지 않으면 악성 또는 과도한 업로드를 막을 수 없다.

### 안정성·유지보수

- API 호출에 공통 timeout, `AbortController` 취소, 재시도, 표준 오류 응답 처리, 인증 갱신 처리가 없다. 일부 컴포넌트는 오류를 콘솔에만 기록해 사용자에게 복구 방법을 제공하지 않는다.
- 서버 상태는 화면별로 중복 조회되며 Context는 곡에만 적용된다. 동시 수정, 화면 이동, 업로드 완료 뒤 데이터 일관성을 자동 보장하는 캐시 정책이 없다.
- 업로드 방식이 R2 직접 업로드(미디어·갤러리)와 백엔드 multipart(개인 기록)로 나뉘어 있어 파일 제한·재시도·오류 경험이 일관되지 않다.
- 지도 SDK script와 네이버 지도 객체에 대한 해제 처리가 제한적이다. 모달을 반복 열거나 네트워크 실패를 재시도하는 경우의 리스너·SDK 로딩 경계를 점검할 필요가 있다.
- `BrowserRouter`를 사용하므로 Cloudflare 정적 호스팅에서도 모든 경로를 `index.html`로 반환하는 SPA fallback 설정이 필요하다. 이 디렉터리만으로는 해당 설정 여부를 확인할 수 없다.

### 테스트

- 테스트 프레임워크·테스트 파일·CI 테스트 명령이 없다. 최소한 서비스 HTTP 실패, URL 라우팅, 권한 없는 변경 UI, 업로드 3단계, 미디어 품질/라디오 모드, 지도 실패 경로를 자동화할 필요가 있다.
- `npm run lint`는 유일한 자동 검증이며, 실제 브라우저·백엔드·R2·네이버 지도 연동을 검증하지 않는다.
