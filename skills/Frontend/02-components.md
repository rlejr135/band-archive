# Components 가이드

## 컴포넌트 트리

```
App
└── SongProvider (Context)
    └── MainContent
        ├── Header (app-header)
        │   └── 네비게이션 버튼들 (대시보드, 곡, 멤버, 추천)
        ├── AnnouncementToast (공지 배너, 모든 페이지 상단)
        └── main (app-main)
            └── Routes
                ├── Dashboard
                │   └── RehearsalCalendar (합주 달력)
                │       ├── RehearsalDetail (날짜별 일정 목록)
                │       └── RehearsalModal (일정 생성/수정)
                ├── SongPage
                │   ├── SearchBar
                │   ├── SongList
                │   │   └── PasswordModal
                │   └── SongDetail / SongForm
                │       ├── FileUpload
                │       ├── MediaPlayer
                │       └── CommentSection
                ├── SongSuggestion
                │   └── PasswordModal
                ├── MemberDashboard
                └── MemberDetail
                    ├── FileUpload
                    ├── MediaPlayer
                    └── CommentSection
```

---

## 공통 컴포넌트 (`components/common/`)

### FileUpload
**파일**: `common/FileUpload.jsx`
**역할**: 드래그앤드롭 + 클릭 파일 업로드 (진행률 표시)

| Prop | 타입 | 설명 |
|------|------|------|
| `onUpload` | `(file, onProgress) => Promise` | 파일 업로드 핸들러 (`onProgress` 콜백 포함) |
| `accept` | `string` | 허용 파일 확장자 (기본: `".mp3,.wav,.ogg,.m4a,.aac,.flac,.mp4,.webm,.mov,.avi,.mkv,.png,.jpg,.jpeg,.gif,.webp,.pdf"`) |
| `multiple` | `boolean` | 다중 파일 선택 허용 (기본: `true`) |

**특이사항**:
- 파일 크기 제한 200MB 하드코딩 (`file.size > 200 * 1024 * 1024`)
- `onUpload(file, onProgress)` 형태로 진행률 콜백 전달
- `useCallback`으로 드래그 이벤트 핸들러 최적화
- 파일별 진행률 상태 관리 (`fileId` = `${file.name}-${Date.now()}`)

### MediaPlayer
**파일**: `common/MediaPlayer.jsx`
**역할**: 미디어 타입별 자동 플레이어 렌더링

| Prop | 타입 | 설명 |
|------|------|------|
| `file` | `object` | 미디어 객체 `{ url, name, type }` |

**주의**: `file` prop은 단일 객체로 전달해야 함. `url`, `type`을 개별 props로 전달하면 동작하지 않음.

**렌더링 규칙**:
- `audio` → `<audio>` 태그
- `video` → `<video>` 태그
- `image` → `<img>` 태그
- `document` → 다운로드 링크

### SearchBar
**파일**: `common/SearchBar.jsx`
**역할**: 곡 검색 (제목/아티스트 기준)

| Prop | 타입 | 설명 |
|------|------|------|
| `onSearch` | `(query: string) => void` | 검색어 변경 콜백 |

**특이사항**:
- 내부 `useState`로 검색어 상태 관리
- placeholder 하드코딩: `"곡 제목 또는 아티스트 검색..."`

### AnnouncementToast
**파일**: `common/AnnouncementToast.jsx`
**역할**: 헤더 아래 항상 표시되는 토스트형 공지 배너 (인라인 편집)

| Prop | 타입 | 설명 |
|------|------|------|
| (없음) | - | 자체 상태 관리 (API 직접 호출) |

**특이사항**:
- 마운트 시 `fetchAnnouncement()` 호출
- 인라인 편집: 텍스트 클릭 → 입력 모드 → `updateAnnouncement()` 호출
- DB에 단일 레코드(id=1)로 유지, 누구나 수정 가능

---

### PasswordModal
**파일**: `common/PasswordModal.jsx`
**역할**: 삭제 확인 비밀번호 입력 모달

| Prop | 타입 | 설명 |
|------|------|------|
| `isOpen` | `boolean` | 모달 표시 여부 |
| `onClose` | `() => void` | 닫기/취소 시 콜백 |
| `onConfirm` | `(password) => void` | 비밀번호 검증 성공 시 콜백 |
| `title` | `string` | 모달 제목 (기본: `"비밀번호 확인"`) |
| `checkPassword` | `(password) => Promise<boolean>` | 커스텀 비밀번호 검증 함수 (optional) |

**참고**: `checkPassword` 미제공 시 기본 비밀번호 `'admin'`으로 클라이언트 검증

### CommentSection
**파일**: `common/CommentSection.jsx`
**역할**: 미디어/개인로그 공용 댓글 컴포넌트 (대댓글 지원)

| Prop | 타입 | 설명 |
|------|------|------|
| `targetType` | `string` | `"media"` 또는 `"personal-logs"` |
| `targetId` | `number` | 대상 ID |

**특이사항**:
- `CommentItem` 재귀 렌더링으로 대댓글 표시
- 댓글 작성: 이름 + 비밀번호 + 내용
- 수정/삭제 시 비밀번호 검증 (서버 해시 비교)
- API: `fetchComments()`, `createComment()`, `createReply()`, `updateComment()`, `deleteComment()`

---

## 페이지 컴포넌트

### Dashboard (`components/dashboard/Dashboard.jsx`)
- Props: `onSelectSong`, `onViewSongs`
- 대시보드 통계 표시 (전체 곡 수)
- 곡 상태 분포 (Practice, Completed, OnHold)
- **합주 일정 달력** (`RehearsalCalendar`) 통합
- 빠른 작업: 새로고침, 전체 곡 보기 (`onViewSongs`)
- 연습 팁 섹션
- API: `fetchDashboardStats()`

### RehearsalCalendar (`components/calendar/RehearsalCalendar.jsx`)
- `react-calendar` 래핑, 월간 달력 표시
- 일정 있는 날짜에 컬러 도트, 기간 일정은 배경 하이라이트
- 날짜 클릭 시 `RehearsalDetail` 표시
- 월 이동 시 해당 월 데이터 자동 재조회
- API: `fetchRehearsals(year, month)`

### RehearsalDetail (`components/calendar/RehearsalDetail.jsx`)
- 선택 날짜의 일정 목록 표시 및 삭제
- API: `deleteRehearsal(id)`

### LocationPicker (`components/calendar/LocationPicker.jsx`)
- Naver Map API v3 기반 장소 검색/선택 컴포넌트
- SDK 동적 로딩 (script 태그, geocoder 서브모듈 포함)
- Props: `location`, `latitude`, `longitude`, `onChange`, `onClose`
- 상태: `searchQuery`, `loading`, `error`, `searchResults` (배열), `showResults` (boolean)
- 기능:
  - **장소명 검색**: 백엔드 `/api/search-places` 호출 (Naver Search Local API 프록시)
  - 검색 결과 드롭다운 리스트 표시 (최대 5개, `.location-results` UI)
  - 결과 클릭 시 Katec→WGS84 좌표 변환 (`naver.maps.TransCoord.fromTM128ToLatLng`)
  - location에 "장소명 (도로명주소)" 형태로 저장
  - 지도 클릭으로 선택 (reverse geocode), 마커 표시
- CSS 구조: `.location-search-wrapper` > `.location-search` + `.location-results` (absolute 드롭다운)
- 기본 중심: 서울시청 (37.5665, 126.978)
- 환경변수: `VITE_NAVER_MAP_CLIENT_ID`, `VITE_API_URL` 필요

### RehearsalModal (`components/calendar/RehearsalModal.jsx`)
- 일정 생성/수정 모달
- 필드: 제목, 날짜, 기간(start_date/end_date), 시간, 장소(+지도 연동), 색상 선택, 곡 연결(song_ids), 메모
- 장소 입력 시 🗺️ 버튼으로 LocationPicker 토글 (Naver Map 기반 장소 검색/선택)
- 지도에서 선택 시 location, latitude, longitude 자동 설정
- API: `createRehearsal(data)`, `updateRehearsal(id, data)`

### SongPage (App.jsx 내 인라인)
- 2-패널 레이아웃 (사이드바 + 콘텐츠)
- URL 파라미터와 Context 동기화
- 조건부 렌더링: `isEditing` → SongForm / `currentSong` → SongDetail

### SongList (`components/songs/SongList.jsx`)
- **순수 props 컴포넌트** (Context 미사용)
- Props: `songs`, `onSelectSong`, `onDeleteSong`, `onAddSong`
- 곡 삭제 시 PasswordModal 표시
- 새 곡 추가 버튼 → `onAddSong()` 콜백 호출

### SongDetail (`components/songs/SongDetail.jsx`)
- Props: `song`, `onEdit`, `onUploadMedia`, `onBack`
- Context 사용: `useSongs()`에서 `editSong`, `removeMediaFromSong`, `renameMediaInSong`
- **YouTube 임베드**: link 필드가 YouTube URL이면 iframe 임베드 플레이어 표시 (`getYoutubeId()`)
- **인라인 편집**: 코드(chords), 메모(memo) 필드를 상세 화면에서 바로 편집 가능
  - 편집 버튼 클릭 → textarea 표시 → 저장/취소
  - 내부 상태: `editingChords`, `chordsText`, `chordsSaving`, `editingMemo`, `memoText`, `memoSaving`
  - 저장 시 `editSong(id, { ...song, chords/memo: text })` 호출
- 코드/메모는 항상 표시 (`<pre>` 태그)
- 미디어 관리 (업로드, 재생, 이름 변경, 삭제)
- 미디어 선택 시 인라인 플레이어 + 댓글(CommentSection) 표시
- 내부 상태: `selectedMedia`, `renamingMediaId`, `newFilename`
- 하위 컴포넌트: FileUpload, MediaPlayer, CommentSection

### SongForm (`components/songs/SongForm.jsx`)
- **순수 props 컴포넌트** (Context 미사용)
- Props: `song`, `onSave`, `onCancel`
- 폼 필드: title, artist, status, genre, difficulty, link (YouTube URL 전용), chords, memo
- **YouTube URL 검증**: `isValidYoutubeUrl()` — watch, youtu.be, shorts 형식 지원
  - 유효하지 않은 URL 제출 시 에러 메시지 표시 (`linkError` 상태)
- 생성/수정 모드 자동 판별 (`song` prop 존재 여부)

### SongSuggestion (`components/songs/SongSuggestion.jsx`)
- 독립적 (Context 미사용, 자체 상태 관리)
- 추천곡 목록 + 투표 (thumbs up/down)
- 추천곡 추가/삭제
- API: `fetchSuggestions()`, `createSuggestion()`, `voteSuggestion()`, `deleteSuggestion()`

### MemberDashboard (`components/members/MemberDashboard.jsx`)
- 멤버 그리드 목록
- 멤버 추가 폼
- API: `fetchMembers()`, `createMember()`

### MemberDetail (`components/members/MemberDetail.jsx`)
- URL 파라미터: `useParams()` → `id`
- 멤버 프로필 + 개인 연습 로그
- 로그 업로드 (FileUpload, `accept="audio/*,video/*,.mp3,.wav,.m4a,.mp4,.mov,.avi"`) + 재생 (MediaPlayer)
- API: `fetchMember()`, `fetchPersonalLogs()`, `uploadPersonalLog()`, `deletePersonalLog()`, `deleteMember()`

---

## 새 컴포넌트 작성 가이드

```jsx
// 1. 파일 생성: src/components/{feature}/MyComponent.jsx
// 2. CSS 생성: src/components/{feature}/MyComponent.css

import { useState, useEffect } from 'react';
import { useSongs } from '../../context/SongContext'; // 곡 관련이면
import './MyComponent.css';

function MyComponent({ prop1, prop2 }) {
  const [localState, setLocalState] = useState(null);
  const { songs, currentSong } = useSongs(); // 필요 시

  useEffect(() => {
    // 데이터 로딩
  }, []);

  if (!localState) return <div className="loading">로딩 중...</div>;

  return (
    <div className="my-component">
      {/* 컨텐츠 */}
    </div>
  );
}

export default MyComponent;
```

### 커스텀 훅

#### useAsyncData (`hooks/useAsyncData.js`)
비동기 데이터 로딩 패턴을 추상화하는 훅.

```js
const { data, setData, loading, error, reload } = useAsyncData(fetchFn, deps, { immediate });
```

| 반환값 | 타입 | 설명 |
|--------|------|------|
| `data` | `any` | 로딩된 데이터 |
| `setData` | `function` | 데이터 직접 설정 (optimistic update용) |
| `loading` | `boolean` | 로딩 상태 |
| `error` | `string \| null` | 에러 메시지 |
| `reload` | `function` | 수동 재로딩 |

**사용 위치**: MemberDashboard, MemberDetail

### 컨벤션
- 함수형 컴포넌트만 사용 (클래스 컴포넌트 없음)
- `export default` 사용
- CSS 클래스: 케밥 케이스 (`my-component-title`)
- 로딩 상태: `<div className="loading">` 패턴
- 에러 상태: `<div className="error-state">` 패턴 (공통 CSS)
- 빈 상태: `<div className="empty-state-box">` 패턴 (공통 CSS)
