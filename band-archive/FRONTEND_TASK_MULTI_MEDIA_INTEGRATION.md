# Frontend Task: 다중 미디어 파일 UI 통합

## 📋 작업 개요
백엔드에 `Media` 모델이 추가되어 한 곡당 여러 미디어 파일을 지원합니다.
프론트엔드를 기존 `sheet_music` (단일 문자열) 기반에서 `media` (배열) 기반으로 전환해주세요.

---

## 🔄 백엔드 변경사항 요약

### Song 응답 구조 변경
`Song.to_dict()` 응답에 `media` 배열이 추가되었습니다.
`sheet_music` 필드는 하위 호환을 위해 그대로 유지됩니다 (최신 업로드 파일명).

```json
{
  "id": 1,
  "title": "Summer of 69",
  "artist": "Bryan Adams",
  "sheet_music": "1_20260208_123456_practice.mp3",
  "media": [
    {
      "id": 1,
      "song_id": 1,
      "filename": "1_20260208_120000_score.pdf",
      "file_type": "document",
      "file_size": 204800,
      "url": "/uploads/1_20260208_120000_score.pdf",
      "created_at": "2026-02-08T12:00:00"
    },
    {
      "id": 2,
      "song_id": 1,
      "filename": "1_20260208_123456_practice.mp3",
      "file_type": "audio",
      "file_size": 5242880,
      "url": "/uploads/1_20260208_123456_practice.mp3",
      "created_at": "2026-02-08T12:34:56"
    }
  ],
  "...기타 기존 필드..."
}
```

### 새 API 엔드포인트

| Method | Route | 설명 | 응답 |
|--------|-------|------|------|
| `GET` | `/songs/:id/media` | 곡의 미디어 목록 | `[Media]` (200) |
| `POST` | `/songs/:id/media` | 미디어 업로드 (권장) | `Media` (201) |
| `DELETE` | `/media/:id` | 미디어 삭제 (파일+DB) | `{"message": "Media deleted"}` (200) |

기존 `POST /songs/:id/upload`도 동작합니다 (하위 호환). 하지만 새 `/songs/:id/media`를 권장합니다.

### file_type 값

| file_type | 확장자 |
|-----------|--------|
| `video` | mp4, webm, mov, avi, mkv |
| `audio` | mp3, wav, ogg, m4a, aac, flac |
| `image` | png, jpg, jpeg, gif, webp |
| `document` | pdf 등 나머지 |

---

## ✅ 변경 필요 파일

### 1. `api.js` — 업로드 엔드포인트 전환 + 삭제 함수 추가

**현재 문제**: `uploadMedia()`가 이전 엔드포인트 `POST /songs/:id/upload`를 호출합니다.

```javascript
// 현재 (api.js:81)
xhr.open('POST', `${API_URL}/songs/${songId}/upload`);

// 변경 → 새 엔드포인트 사용
xhr.open('POST', `${API_URL}/songs/${songId}/media`);
```

**추가 함수**:
```javascript
// 미디어 삭제
export const deleteMedia = async (mediaId) => {
  const response = await fetch(`${API_URL}/media/${mediaId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete media');
  return await response.json();
};
```

> `fetchMediaList(songId)`는 불필요 — `song.media` 배열이 Song 응답에 이미 포함되어 있음.

---

### 2. `SongDetail.jsx` — `song.sheet_music` → `song.media` 배열 전환

**현재 문제**:
- `song.sheet_music`(단일 문자열)으로 파일 1개만 표시
- 파일 타입을 regex로 판별 → 백엔드 `file_type` 필드 사용해야 함
- 삭제 버튼 없음

**변경 포인트**:

#### 2-1. 미디어 목록을 `song.media` 배열로 렌더링
```jsx
// 현재
{song.sheet_music && (
  <div className="media-item" onClick={handlePlayMedia}>
    <span className="media-name">{song.sheet_music}</span>
    <button className="play-btn">▶ 재생</button>
  </div>
)}

// 변경 → 배열 순회
{song.media?.length > 0 ? (
  <div className="media-list">
    {song.media.map((media) => (
      <div key={media.id} className="media-item">
        <span className="media-icon">{iconForType(media.file_type)}</span>
        <div className="media-info">
          <span className="media-name">{media.filename}</span>
        </div>
        {(media.file_type === 'audio' || media.file_type === 'video') && (
          <button className="play-btn" onClick={() => handlePlay(media)}>▶ 재생</button>
        )}
        {media.file_type === 'image' && (
          <button className="play-btn" onClick={() => handlePreview(media)}>🖼️ 보기</button>
        )}
        {media.file_type === 'document' && (
          <a href={`${API_URL}${media.url}`} target="_blank" className="play-btn">📄 다운</a>
        )}
        <button className="log-delete-btn" onClick={() => handleDeleteMedia(media.id)}>🗑️</button>
      </div>
    ))}
  </div>
) : (
  <div className="empty-media">
    <p>등록된 미디어 파일이 없습니다.</p>
  </div>
)}
```

#### 2-2. 재생 핸들러에서 `media.file_type` 사용
```jsx
// 현재: regex로 타입 판별
const handlePlayMedia = () => {
  if (song.sheet_music) {
    const fileUrl = `${API_URL}/uploads/${song.sheet_music}`;
    setSelectedMedia({
      name: song.sheet_music,
      url: fileUrl,
      type: song.sheet_music.match(/\.(mp4|...)$/i) ? 'video' : 'audio'
    });
  }
};

// 변경 → 백엔드 file_type 직접 사용
const handlePlay = (media) => {
  setSelectedMedia({
    name: media.filename,
    url: `${API_URL}${media.url}`,
    type: media.file_type,
  });
};
```

#### 2-3. 삭제 핸들러 추가
```jsx
const handleDeleteMedia = async (mediaId) => {
  try {
    await deleteMedia(mediaId);
    // song 데이터 갱신 필요 (Context를 통해 또는 로컬 state)
  } catch (error) {
    console.error('Failed to delete media:', error);
  }
};
```

#### 2-4. file_type별 아이콘 헬퍼
```jsx
const iconForType = (fileType) => {
  switch (fileType) {
    case 'video': return '🎬';
    case 'audio': return '🎵';
    case 'image': return '🖼️';
    case 'document': return '📄';
    default: return '📁';
  }
};
```

---

### 3. `SongContext.jsx` — 미디어 삭제 액션 추가

```javascript
// 추가 import
import { ..., deleteMedia, getSong } from '../services/api';

// 추가 함수
const removeMediaFromSong = async (songId, mediaId) => {
  try {
    await deleteMedia(mediaId);
    const updatedSong = await getSong(songId);
    setSongs(songs.map(s => s.id === songId ? updatedSong : s));
    if (currentSong && currentSong.id === songId) {
      setCurrentSong(updatedSong);
    }
  } catch (error) {
    console.error('Failed to delete media:', error);
    throw error;
  }
};

// Provider value에 추가
<SongContext.Provider value={{
    ...,
    removeMediaFromSong
}}>
```

---

### 4. `FileUpload.jsx` — `multiple` 다시 `true`로 변경

이전에 백엔드가 단일 파일만 처리해서 `multiple=false`로 바꿨었습니다.
이제 `POST /songs/:id/media`가 각 파일을 독립된 Media 레코드로 저장하므로, 다시 `true`로 변경합니다.

```jsx
// 현재 (FileUpload.jsx:4)
const FileUpload = ({ onUpload, accept = "audio/*,video/*", multiple = false }) => {

// 변경
const FileUpload = ({ onUpload, accept = "audio/*,video/*,image/*,.pdf", multiple = true }) => {
```

> `accept`도 확장: 이미지와 PDF도 업로드 가능하도록 (백엔드 `ALLOWED_EXTENSIONS`에 포함되어 있음)

---

### 5. `MediaPlayer.jsx` — `image`, `document` 타입 처리 추가

**현재 문제**: `video`와 `audio`만 처리하고, `image`/`document`는 "미리보기를 사용할 수 없습니다"가 표시됩니다.

```jsx
// 추가할 분기 (player-content 내부)
const isImage = file.type === 'image';
const isDocument = file.type === 'document';

{isImage && mediaUrl && (
  <div className="image-preview">
    <img src={mediaUrl} alt={file.name} style={{ maxWidth: '100%', borderRadius: '8px' }} />
  </div>
)}

{isDocument && mediaUrl && (
  <div className="document-preview">
    <a href={mediaUrl} target="_blank" rel="noreferrer" className="document-link">
      📄 {file.name} 다운로드
    </a>
  </div>
)}
```

---

## 📝 참고: 백엔드 Media 응답 구조

```typescript
// 참고용 타입 정의
interface Media {
  id: number;
  song_id: number;
  filename: string;           // "1_20260208_123456_practice.mp3"
  file_type: 'video' | 'audio' | 'image' | 'document';
  file_size: number;          // bytes
  url: string;                // "/uploads/1_20260208_123456_practice.mp3"
  created_at: string;         // ISO 8601
}
```

**주의**: `media.url`은 상대 경로 (`/uploads/...`)입니다.
프론트엔드에서 사용 시 `API_URL + media.url`로 전체 URL을 조합해야 합니다.

---

## ✅ 완료 체크리스트

- [ ] `api.js`: `uploadMedia()` 엔드포인트를 `/songs/:id/media`로 변경
- [ ] `api.js`: `deleteMedia(mediaId)` 함수 추가
- [ ] `SongDetail.jsx`: `song.sheet_music` → `song.media` 배열 렌더링
- [ ] `SongDetail.jsx`: 파일별 재생/미리보기/다운로드/삭제 버튼
- [ ] `SongDetail.jsx`: file_type 기반 아이콘 표시
- [ ] `SongContext.jsx`: `removeMediaFromSong` 액션 추가 + Provider에 노출
- [ ] `FileUpload.jsx`: `multiple=true`, `accept`에 이미지/PDF 추가
- [ ] `MediaPlayer.jsx`: `image`, `document` 타입 미리보기/다운로드 지원
