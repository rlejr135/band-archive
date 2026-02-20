<!-- Last synced commit: 487f5b4d7ebb776cf61307fe420d8e5b844cb7d1 -->

---
name: Frontend Architecture
description: Analysis of the frontend architecture, structure, and functionality
---

# Frontend Analysis - Band Archive

## Overview
The frontend is a **React 19** application built with **Vite 7**, designed to manage a band's archive. It features song management, practice logs, member tracking, song suggestions, an announcement toast, and a rehearsal calendar.

## Technology Stack
- **Framework**: React 19.2.0
- **Routing**: React Router DOM 7.13.0
- **Build Tool**: Vite 7.2.4
- **State Management**: React Context API (`SongContext`)
- **Styling**: Vanilla CSS (`App.css`, `index.css`, and modular CSS)
- **HTTP Client**: Native `fetch` API & `XMLHttpRequest` (for progress tracking)
- **Calendar**: react-calendar 5.0.0

## Project Structure
`band-archive/frontend/src/`

- **`components/`**: Modular UI components
    - `dashboard/`: Dashboard view components (`Dashboard.jsx`, `Dashboard.css`)
    - `calendar/`: 합주 일정 달력 컴포넌트
        - `RehearsalCalendar.jsx` + `.css`: 달력 메인 (react-calendar 래핑, 도트/하이라이트 표시)
        - `RehearsalDetail.jsx` + `.css`: 선택 날짜의 일정 목록 및 삭제
        - `RehearsalModal.jsx` + `.css`: 일정 생성/수정 모달 (제목, 날짜, 기간, 시간, 장소+지도, 색상, 곡 연결, 메모)
        - `LocationPicker.jsx` + `.css`: Naver Map 기반 장소 검색/선택 (geocoder 연동)
    - `songs/`: Song list, details, forms, and suggestion components
    - `members/`: Member list and detail views
    - `common/`: Reusable components
        - `SearchBar.jsx` + `.css`: 검색 바
        - `AnnouncementToast.jsx` + `.css`: 토스트형 공지 알림 (헤더 아래 항상 표시, 인라인 편집)
        - `FileUpload.jsx` + `.css`: 파일 업로드 컴포넌트
        - `MediaPlayer.jsx` + `.css`: 미디어 재생 컴포넌트
        - `PasswordModal.jsx` + `.css`: 비밀번호 입력 모달
    - `layout/`: Layout components (`Header.css`)
    - `practices/`: Practice log components
- **`context/`**: Global state management
    - `SongContext.jsx`: Manages song lists, current selection, and loading states.
- **`services/`**: API interaction layers
    - `api.js`: Core API functions for songs, logs, suggestions, announcements.
    - `memberApi.js`: API functions specific to member management.
    - `rehearsalApi.js`: 합주 일정 CRUD API 함수.
- **`hooks/`**: Custom hooks
    - `useAsyncData.js`: 비동기 데이터 로딩 훅
- **`assets/`**: Static assets (`logo.png`)
- **`App.jsx`**: Main application component handling routing, layout, and `AnnouncementToast`.
- **`App.css`**: Global layout/button/responsive styles
- **`index.css`**: CSS variables, animations, scrollbar, utility classes

## Key Features & Routes

### 1. Dashboard (`/`)
- Displays an overview of band activities.
- Shows statistics (via `fetchDashboardStats`).
- **합주 일정 달력** (`RehearsalCalendar`) 통합 — 월간 달력에 일정 도트/기간 하이라이트 표시.
- Entry point for quick navigation.

### 2. Song Management (`/songs`)
- **List View**: Browse all songs with search/filter capabilities.
- **Detail View (`/songs/:id`)**:
    - View song details (chords, memo 등) — 코드/메모는 항상 표시.
    - **인라인 편집**: 코드(chords), 메모(memo) 필드를 상세 화면에서 바로 편집 가능 (편집 버튼 → textarea → 저장/취소).
    - Manage associated media (audio/scores).
    - CRUD operations for songs.
- **Song Form**: title, artist, status, genre, difficulty, link, chords, memo (lyrics 필드 제거됨).
- **Practice Logs**:
    - Record practice sessions per song.
    - Upload recordings of practice sessions.

### 3. Member Management (`/members`)
- **Directory**: List all band members.
- **Profiles (`/members/:id`)**:
    - Individual member details.
    - Personal logs (upload & manage personal practice files).

### 4. Suggestions (`/suggestions`)
- Submit new song ideas.
- Vote on suggested songs.
- Secure deletion (password protected).

### 5. Announcement Toast (모든 페이지)
- 헤더 아래에 항상 표시되는 토스트형 공지 배너.
- 📢 아이콘 + 공지 텍스트 + ✏️ 인라인 편집 기능.
- DB에 단일 레코드(id=1)로 유지, 누구나 수정 가능.

### 6. Rehearsal Calendar (대시보드 내)
- `react-calendar` 기반 월간 달력.
- 일정 있는 날짜에 컬러 도트, 기간 일정은 배경 하이라이트.
- 날짜 클릭 시 해당 날짜 일정 목록(RehearsalDetail) 표시.
- 일정 추가/수정 모달(RehearsalModal): 제목, 날짜, 기간, 시간, 장소, 색상 선택, 곡 연결, 메모.
- 월 이동 시 해당 월 데이터 자동 재조회.

## API Integration (`src/services`)
- **`api.js`**:
    - `fetchSongs`, `getSong`, `createSong`, `updateSong`, `deleteSong`
    - `uploadMedia` (supports progress tracking via XHR)
    - `fetchPracticeLogs`, `createPracticeLog`, `uploadRecording`
    - `fetchSuggestions`, `createSuggestion`, `deleteSuggestion`, `voteSuggestion`
    - `fetchAnnouncement`, `updateAnnouncement`
    - `deleteMedia`, `renameMedia`
    - `fetchDashboardStats`
    - `getPracticeLog`, `updatePracticeLog`, `deletePracticeLog`
- **`memberApi.js`**:
    - CRUD for members.
    - `uploadPersonalLog` (supports progress tracking).
- **`rehearsalApi.js`**:
    - `fetchRehearsals(year, month)`, `getRehearsal(id)`
    - `createRehearsal(data)`, `updateRehearsal(id, data)`, `deleteRehearsal(id)`

## CSS Design System (`index.css`)
```css
--primary-color: #ffd32a;    /* Vibrant Yellow */
--primary-hover: #ffc048;
--secondary-color: #0fbcf9;  /* Cyan/Blue */
--accent-color: #ff5e57;     /* Red/Pink */
--background-color: #1e272e; /* Dark Blue-Grey */
--surface-color: #2f3640;    /* Lighter Dark */
--text-primary: #f1f2f6;
--text-secondary: #d2dae2;
--border-color: #4b6584;
--shadow-sm/md/lg
--radius-md: 12px;
--font-family: 'Outfit', 'Inter', sans-serif;
```

## Development Commands
- `npm run dev`: Start development server (Vite)
- `npm run build`: Build for production
- `npm run lint`: Run ESLint
- `npm run preview`: Preview production build
