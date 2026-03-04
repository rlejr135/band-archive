# Frontend Architecture

## 기술 스택

| 카테고리 | 기술 | 버전 |
|----------|------|------|
| UI 프레임워크 | React | 19.2.0 |
| 라우팅 | React Router | 7.13.0 |
| 빌드 도구 | Vite | 7.2.4 |
| 린팅 | ESLint | 9.39.1 |
| 스타일링 | 순수 CSS | - |
| HTTP 클라이언트 | Native Fetch API | - |
| 상태관리 | React Context API | - |
| 달력 | react-calendar | 5.0.0 |

### 사용하지 않는 것들
- TypeScript (타입 정의만 devDependencies에 존재)
- CSS 프리프로세서 (SCSS/LESS)
- UI 컴포넌트 라이브러리 (MUI, Ant Design 등)
- 상태관리 라이브러리 (Redux, Zustand 등)
- HTTP 라이브러리 (axios, react-query 등)

## 프로젝트 구조

```
frontend/
├── index.html                          # HTML 진입점
├── package.json                        # 의존성 & 스크립트
├── vite.config.js                      # Vite 번들러 설정
├── eslint.config.js                    # 린팅 설정
├── .env.development                    # 개발 API URL: http://localhost:5000
├── .env.production                     # 운영 API URL: https://band-archive.fly.dev
├── public/                             # 정적 에셋
└── src/
    ├── main.jsx                        # React 앱 진입점 (ReactDOM.createRoot)
    ├── App.jsx                         # 메인 앱 컴포넌트 (라우팅 + 레이아웃)
    ├── App.css                         # 핵심 레이아웃 스타일
    ├── index.css                       # 글로벌 스타일 & CSS 변수
    ├── assets/                         # 이미지 (logo.png, react.svg)
    ├── components/
    │   ├── common/                     # 재사용 가능한 공통 컴포넌트
    │   │   ├── FileUpload.jsx/css      # 드래그앤드롭 파일 업로드
    │   │   ├── MediaPlayer.jsx/css     # 오디오/비디오/이미지 플레이어
    │   │   ├── SearchBar.jsx/css       # 곡 검색
    │   │   ├── AnnouncementToast.jsx/css # 토스트형 공지 배너 (인라인 편집)
    │   │   └── PasswordModal.jsx/css   # 삭제 확인 비밀번호 모달
    │   ├── dashboard/                  # 대시보드
    │   │   └── Dashboard.jsx/css
    │   ├── calendar/                   # 합주 일정 달력
    │   │   ├── RehearsalCalendar.jsx/css # 달력 메인 (도트/하이라이트)
    │   │   ├── RehearsalDetail.jsx/css   # 선택 날짜 일정 목록
    │   │   └── RehearsalModal.jsx/css    # 일정 생성/수정 모달
    │   ├── layout/
    │   │   └── Header.css              # 헤더 스타일
    │   ├── songs/                      # 곡 관리
    │   │   ├── SongList.jsx/css        # 곡 목록
    │   │   ├── SongDetail.jsx/css      # 곡 상세 (코드/메모 인라인 편집)
    │   │   ├── SongForm.jsx/css        # 곡 생성/수정 폼
    │   │   ├── SongMedia.css           # 미디어 목록 스타일
    │   │   └── SongSuggestion.jsx      # 다음 곡 추천
    │   ├── members/                    # 멤버 관리
    │   │   ├── MemberDashboard.jsx/css
    │   │   └── MemberDetail.jsx/css
    │   └── gallery/                    # 갤러리
    │       └── Gallery.jsx/css
    ├── context/
    │   └── SongContext.jsx             # 글로벌 곡 상태 관리
    ├── hooks/
    │   └── useAsyncData.js             # 비동기 데이터 로딩 훅
    └── services/
        ├── api.js                      # 곡/대시보드/연습/추천/공지 API
        ├── memberApi.js                # 멤버 & 개인 로그 API
        ├── rehearsalApi.js             # 합주 일정 CRUD API
        └── galleryApi.js              # 갤러리 이미지 CRUD API
```

## 빌드 & 환경 설정

### Vite 설정 (`vite.config.js`)
- `@vitejs/plugin-react` 플러그인 사용
- `base: '/'` 고정 (Cloudflare Pages 배포)

### 환경 변수
```
VITE_API_URL=http://localhost:5000     # 개발 (.env.development)
VITE_API_URL=https://band-archive.fly.dev  # 운영 (.env.production)
```

### 스크립트
```bash
npm run dev       # 개발 서버 (Vite dev server)
npm run build     # 프로덕션 빌드
npm run lint      # ESLint 실행
npm run preview   # 빌드 결과물 미리보기
```

## 핵심 아키텍처 패턴

### 1. 단방향 데이터 흐름
```
SongContext (전역 상태)
    ↓ Provider
App (라우팅 + 레이아웃)
    ↓ props / context
Pages (SongPage, Dashboard, etc.)
    ↓ props
Components (SongList, SongDetail, etc.)
```

### 2. 서비스 레이어 분리
```
Component → services/api.js → Backend REST API
Component → services/memberApi.js → Backend REST API
Component → services/rehearsalApi.js → Backend REST API
```
- 컴포넌트에서 직접 fetch 호출하지 않고, 서비스 함수를 통해 호출
- 단, Context 내부에서도 서비스 함수 사용

### 3. CSS 파일 관리
- 각 컴포넌트마다 같은 이름의 CSS 파일 존재
- 글로벌 CSS 변수를 `index.css`에서 정의
- CSS Modules 미사용 (BEM 유사 네이밍 컨벤션)
