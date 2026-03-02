# State Management

## 개요

상태관리는 **React Context API** + **컴포넌트 로컬 상태**로 구성됩니다.
외부 상태관리 라이브러리(Redux, Zustand 등)는 사용하지 않습니다.

## SongContext (전역 상태)

**파일**: `src/context/SongContext.jsx`

### Provider 구조
```jsx
// src/main.jsx 또는 App.jsx에서 감싸줌
<SongProvider>
  <MainContent />
</SongProvider>
```

### 상태 (State)

| 상태 | 타입 | 초기값 | 설명 |
|------|------|--------|------|
| `songs` | `Song[]` | `[]` | 전체 곡 목록 |
| `currentSong` | `Song \| null` | `null` | 현재 선택된 곡 |
| `isEditing` | `boolean` | `false` | 편집/생성 모드 여부 |
| `loading` | `boolean` | `true` | 로딩 상태 |
| `error` | `string \| null` | `null` | 에러 메시지 |

### 메서드 (Actions)

| 메서드 | 파라미터 | 설명 |
|--------|----------|------|
| `loadSongs()` | - | 전체 곡 목록 재로딩 |
| `addSong(songData)` | `object` | 새 곡 생성 → API POST |
| `editSong(id, songData)` | `number, object` | 곡 수정 → API PUT |
| `removeSong(id)` | `number` | 곡 삭제 → API DELETE |
| `selectSong(song)` | `Song \| null` | 곡 선택 (currentSong 변경, isEditing=false) |
| `startEdit(song)` | `Song` | 편집 모드 진입 (currentSong 설정, isEditing=true) |
| `startCreate()` | - | 생성 모드 진입 (currentSong=null, isEditing=true) |
| `cancelEdit()` | - | 편집/생성 모드 취소 (isEditing=false) |
| `addMediaToSong(songId, file, onProgress, rehearsalId)` | `number, File, function, number?` | 곡에 미디어 업로드 (진행률 콜백, 선택적 합주 연동) |
| `removeMediaFromSong(songId, mediaId)` | `number, number` | 곡에서 미디어 삭제 |
| `renameMediaInSong(songId, mediaId, newName)` | `number, number, string` | 미디어 이름 변경 |

### 사용법
```jsx
import { useSongs } from '../../context/SongContext';

function MyComponent() {
  const {
    songs,
    currentSong,
    isEditing,
    loading,
    error,
    loadSongs,
    addSong,
    editSong,
    removeSong,
    selectSong,
    startEdit,
    startCreate,
    cancelEdit,
    addMediaToSong,
    removeMediaFromSong,
    renameMediaInSong
  } = useSongs();

  // ...
}
```

### 데이터 흐름
```
API (backend)
  ↕ fetch
services/api.js
  ↕ 호출
SongContext (전역 상태)
  ↕ useSongs() 훅
Components (UI)
```

## useAsyncData 훅 (`hooks/useAsyncData.js`)

반복되는 `useState(loading/error) + useEffect + try/catch/finally` 패턴을 추상화.

```js
import useAsyncData from '../../hooks/useAsyncData';

// 기본 사용
const { data, setData, loading, error, reload } = useAsyncData(fetchFn, deps, { immediate });
```

| 파라미터 | 타입 | 설명 |
|----------|------|------|
| `fetchFn` | `() => Promise` | 데이터를 가져오는 비동기 함수 |
| `deps` | `array` | useCallback 의존성 배열 (기본: `[]`) |
| `immediate` | `boolean` | 마운트 시 즉시 실행 여부 (기본: `true`) |

**사용 예시**:
```js
// MemberDashboard - 단순 목록 로딩
const { data: members, loading, error, reload } = useAsyncData(fetchMembers);

// MemberDetail - 의존성 있는 로딩
const { data: member } = useAsyncData(() => getMember(id), [id]);
const { data: logs, setData: setLogs, reload: reloadLogs } = useAsyncData(() => fetchMemberLogs(id), [id]);
```

---

## 컴포넌트별 로컬 상태

### Dashboard
```javascript
stats: object    // 대시보드 통계 데이터
loading: boolean // 로딩 상태
```

### SongDetail
```javascript
selectedMedia: object | null     // 현재 선택된 미디어
renamingMediaId: number | null   // 이름 변경 중인 미디어 ID
newFilename: string              // 새 파일명
editingChords: boolean           // 코드 인라인 편집 중 여부
chordsText: string               // 편집 중인 코드 텍스트
chordsSaving: boolean            // 코드 저장 중 여부
editingMemo: boolean             // 메모 인라인 편집 중 여부
memoText: string                 // 편집 중인 메모 텍스트
memoSaving: boolean              // 메모 저장 중 여부
uploadRehearsalId: string        // 업로드 시 합주 연동 선택값
rehearsalPickerMediaId: number|null // 합주 연결 인라인 피커 표시 중인 미디어 ID
```

### SongSuggestion
```javascript
suggestions: array  // 추천곡 목록
loading: boolean    // 로딩 상태
showForm: boolean   // 추가 폼 표시
form: { title, artist, link, memo } // 폼 데이터
deleteTarget: object | null // 삭제 대상
```

### MemberDashboard
```javascript
members: array     // 멤버 목록
loading: boolean   // 로딩 상태
showForm: boolean  // 추가 폼 표시
newMember: { name, instrument } // 새 멤버 데이터
```

### MemberDetail
```javascript
member: object | null     // 멤버 정보
logs: array              // 개인 로그 목록
loading: boolean         // 로딩 상태
```

### RehearsalDetail
```javascript
mediaMap: { [rehearsalId]: Media[] } // 합주별 연결된 미디어 목록
playingMedia: object | null          // 현재 재생 중인 미디어
uploadingFor: number | null          // 업로드 폼 표시 중인 합주 ID
uploadSongId: string                 // 업로드 시 선택된 곡 ID
uploadProgress: number               // 업로드 진행률 (0-100)
uploading: boolean                   // 업로드 중 여부
```

### FileUpload
```javascript
isDragging: boolean           // 드래그 중 여부
uploadProgress: { [fileId]: number } // 파일별 업로드 진행률
```

## 상태 추가 가이드

### 전역 상태가 필요한 경우
- 여러 컴포넌트에서 공유되는 데이터 → Context에 추가
- 패턴: `SongContext.jsx`에 state + 메서드 추가

### 로컬 상태로 충분한 경우
- 해당 컴포넌트에서만 사용되는 UI 상태 (폼 데이터, 모달 표시 등)
- 패턴: `useState` 훅 사용

### 새 Context 추가 시
```jsx
// src/context/NewContext.jsx
import { createContext, useContext, useState, useEffect } from 'react';

const NewContext = createContext();

export function NewProvider({ children }) {
  const [state, setState] = useState(initialValue);

  // API 호출 메서드들...

  return (
    <NewContext.Provider value={{ state, ...methods }}>
      {children}
    </NewContext.Provider>
  );
}

export function useNew() {
  const context = useContext(NewContext);
  if (!context) throw new Error('useNew must be used within NewProvider');
  return context;
}
```
