# Frontend 리팩토링 계획

프론트엔드 전체 코드를 분석한 결과, 잘 구현된 부분과 개선이 필요한 부분을 식별했습니다.
억지로 리팩토링하지 않고, **실질적인 코드 중복과 일관성 문제**만 다룹니다.

---

## ✅ 잘 되어있어서 그대로 두는 것들

| 영역 | 이유 |
|------|------|
| `calendar/` 컴포넌트 3개 | 역할 분리 깔끔 (Calendar ↔ Detail ↔ Modal) |
| `FileUpload.jsx` | Drag & Drop + 프로그레스 잘 구현 |
| `MediaPlayer.jsx` | 미디어 타입별 렌더링 깔끔 |
| `AnnouncementToast.jsx` | 인라인 편집 UX 잘 구현 |
| `PasswordModal.jsx` | 범용적 설계 (checkPassword prop 패턴) |
| `SearchBar.jsx` | 단순하고 적절 |
| `SongForm.jsx` | formData 패턴 깔끔 |
| `MemberDashboard.jsx` | `useAsyncData` 활용 잘 되어있음 |
| `MemberDetail.jsx` | `useAsyncData` 활용 잘 되어있음 |
| `rehearsalApi.js` | 깔끔, 일관적 |
| `useAsyncData.js` 훅 | 잘 설계됨, 다른 곳에서 더 활용 가능 |
| 전체 CSS 파일 | 모듈화 잘 되어있음 |

---

## 리팩토링 대상

### 1. XHR 업로드 유틸리티 추출

**문제**: `api.js`의 `uploadMedia`, `uploadRecording`과 `memberApi.js`의 `uploadPersonalLog`에 **거의 동일한 XHR 진행률 추적 코드가 3번 반복**되고 있습니다.

#### [NEW] [uploadWithProgress.js](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/services/uploadWithProgress.js)

공통 XHR 업로드 함수를 추출합니다:

```javascript
// 3곳에서 반복되는 XHR 패턴을 통합
export const uploadWithProgress = (url, formData, onProgress) => {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress((e.loaded / e.total) * 100);
      }
    });
    xhr.addEventListener('load', () => { /* ... */ });
    xhr.addEventListener('error', () => { /* ... */ });
    xhr.open('POST', url);
    xhr.send(formData);
  });
};
```

#### [MODIFY] [api.js](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/services/api.js)

- `uploadMedia`, `uploadRecording` → `uploadWithProgress` 호출로 교체

#### [MODIFY] [memberApi.js](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/services/memberApi.js)

- `uploadPersonalLog` → `uploadWithProgress` 호출로 교체

---

### 2. 미디어 타입 유틸리티 추출

**문제**: 파일 확장자 기반 미디어 타입 감지 로직이 `MediaPlayer.jsx`, `SongDetail.jsx`, `PracticeLogSection.jsx` **3곳에 분산**되어 있으며, 지원하는 확장자 목록도 미세하게 다릅니다.

#### [NEW] [mediaUtils.js](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/utils/mediaUtils.js)

```javascript
const AUDIO_EXTS = ['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'];
const VIDEO_EXTS = ['mp4', 'webm', 'mov', 'avi', 'mkv'];
const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'webp'];

export const getMediaType = (filename) => { /* ... */ };
export const getMediaIcon = (type) => { /* ... */ };
```

#### [MODIFY] [MediaPlayer.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/components/common/MediaPlayer.jsx)

- 내부 `getMediaType` → import 교체

#### [MODIFY] [SongDetail.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/components/songs/SongDetail.jsx)

- 내부 `getMediaType`, `iconForType` → import 교체

#### [MODIFY] [PracticeLogSection.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/components/practices/PracticeLogSection.jsx)

- 인라인 확장자 체크 → import 교체

---

### 3. `SongContext.jsx` 리팩토링 — 중복 refresh 패턴 통합

**문제**: `addMediaToSong`, `removeMediaFromSong`, `renameMediaInSong` 3개 함수에서 **거의 동일한 "API 호출 → getSong → songs + currentSong 갱신" 패턴이 3번 반복**됩니다 (각 ~19줄).

#### [MODIFY] [SongContext.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/context/SongContext.jsx)

```diff
+  // 공통 helper: 곡 데이터 새로고침
+  const refreshSong = async (songId) => {
+    const updatedSong = await getSong(songId);
+    setSongs(prev => prev.map(s => s.id === songId ? updatedSong : s));
+    if (currentSong?.id === songId) setCurrentSong(updatedSong);
+    return updatedSong;
+  };

   const addMediaToSong = async (songId, file, onProgress) => {
-    // 19줄의 중복 코드
+    try {
+      await uploadMedia(songId, file, onProgress);
+      await refreshSong(songId);
+    } catch (error) {
+      console.error('Failed to upload media:', error);
+      throw error;
+    }
   };
```

---

### 4. `App.jsx` 정리

**문제**: 개발 중 남은 **디버그 코멘트 4줄**이 코드 가독성을 저하시킵니다.

#### [MODIFY] [App.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/App.jsx)

14~17행의 디버그 코멘트 제거:

```diff
 import './App.css';
-import './components/layout/Header.css'; // Header.css also moved? Wait, Header component is imported? No, Header is in App.jsx manually? Ah Header.css used.
-// Header component seems to be missing from imports, maybe it's defined inside App or MainContent?
-// Ah, previous App.jsx overwrote MainContent but didn't import Header component, it implemented Header inside MainContent.
-// But Header.css was imported. We moved Header.css to layout/Header.css.
+import './components/layout/Header.css';
```

---

### 5~7. `useAsyncData` 훅 적용 (3개 컴포넌트)

**문제**: `MemberDashboard`와 `MemberDetail`은 이미 `useAsyncData` 훅을 잘 활용하는데, 다른 3개 컴포넌트는 동일한 패턴(useState + useEffect + try/catch/finally)을 **수동으로 구현**하고 있습니다.

> [!IMPORTANT]
> 이 리팩토링은 기존 동작을 전혀 변경하지 않으며, 단지 이미 프로젝트에 존재하는 `useAsyncData` 훅을 일관되게 활용합니다.

#### [MODIFY] [SongSuggestion.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/components/songs/SongSuggestion.jsx)

```diff
-const [suggestions, setSuggestions] = useState([]);
-const [loading, setLoading] = useState(true);
-
-useEffect(() => { loadSuggestions(); }, []);
-
-const loadSuggestions = async () => {
-  try { ... } catch { ... } finally { setLoading(false); }
-};
+const { data: suggestions, setData: setSuggestions, loading, reload: loadSuggestions }
+  = useAsyncData(fetchSuggestions);
```

#### [MODIFY] [Dashboard.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/components/dashboard/Dashboard.jsx)

```diff
-const [stats, setStats] = useState(null);
-const [loading, setLoading] = useState(true);
-useEffect(() => { loadStats(); }, []);
-const loadStats = async () => { ... };
+const { data: stats, loading, reload: loadStats }
+  = useAsyncData(fetchDashboardStats);
```

#### [MODIFY] [PracticeLogSection.jsx](file:///c:/Users/rlejr/anything/band-archive/band-archive/frontend/src/components/practices/PracticeLogSection.jsx)

```diff
-const [logs, setLogs] = useState([]);
-const [loading, setLoading] = useState(true);
-const loadLogs = useCallback(async () => { ... }, [songId]);
-useEffect(() => { if (songId) loadLogs(); }, [songId, loadLogs]);
+const { data: logs, setData: setLogs, loading, reload: loadLogs }
+  = useAsyncData(() => fetchPracticeLogs(songId), [songId]);
```

---

## 리팩토링 제외 (검토 후 보류)

| 대상 | 보류 사유 |
|------|----------|
| `SongDetail.jsx` 분리 | 239줄로 큰 편이지만, 미디어 관리 로직이 곡 상세와 밀접하게 연결. 분리 시 prop drilling 증가 |
| `RehearsalCalendar` → `useAsyncData` | 월 변경 시 파라미터가 동적으로 바뀌는 패턴이 `useAsyncData`와 잘 맞지 않음 |
| `SongList.jsx` 분리 | 63줄로 적절한 크기 |
| `SongContext` 분리 | 현재 규모(158줄)면 단일 파일로 충분 |

---

## Verification Plan

> [!NOTE]
> 프로젝트에 자동화된 테스트가 없으므로 빌드 검증 + 수동 확인으로 진행합니다.

### 빌드 검증
```powershell
cd c:\Users\rlejr\anything\band-archive\band-archive\frontend
npm run build
```
- ✅ 빌드 성공 (에러 없음)
- ✅ 경고 확인

### 수동 확인 요청 (유저)
리팩토링 완료 후, 다음 기능이 정상 동작하는지 각 페이지를 한번씩 확인해주세요:
1. **대시보드**: 통계 로딩, 곡 상태 펼치기, 합주 일정 달력
2. **곡 목록**: 검색, 곡 선택, 곡 추가/수정/삭제
3. **곡 상세**: 미디어 업로드/재생/삭제/이름변경, 연습 일지 CRUD + 녹음 업로드
4. **멤버**: 멤버 목록, 상세, 개인 기록 업로드/재생/삭제
5. **다음 곡 추천**: 추천 추가, 투표, 삭제(비밀번호)
