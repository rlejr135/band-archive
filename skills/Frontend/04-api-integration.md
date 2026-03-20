# API Integration

## 개요

- **HTTP 클라이언트**: Native Fetch API (axios 미사용)
- **Base URL**: `import.meta.env.VITE_API_URL`
  - 개발: `http://localhost:5000`
  - 운영: `https://band-archive.fly.dev`
- **파일 업로드**: Presigned URL → R2 직접 업로드 (XHR, 진행률 추적)
- **Naver Map Client ID**: `import.meta.env.VITE_NAVER_MAP_CLIENT_ID` (LocationPicker에서 사용)

## 서비스 파일

| 파일 | 담당 |
|------|------|
| `src/services/api.js` | 곡, 미디어, 대시보드, 추천, 공지, 댓글 |
| `src/services/memberApi.js` | 멤버, 개인 로그 |
| `src/services/rehearsalApi.js` | 합주 일정 CRUD |
| `src/services/galleryApi.js` | 갤러리 이미지 CRUD, 대표 이미지 |
| `src/services/uploadApi.js` | Presigned URL 발급, R2 직접 업로드, 메타데이터 등록 |

## API 엔드포인트 일람

### 곡 (Songs)

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchSongs()` | GET | `/songs` | 전체 곡 목록 |
| `getSong(id)` | GET | `/songs/:id` | 곡 상세 |
| `createSong(data)` | POST | `/songs` | 곡 생성 |
| `updateSong(id, data)` | PUT | `/songs/:id` | 곡 수정 |
| `deleteSong(id)` | DELETE | `/songs/:id` | 곡 삭제 (비밀번호 없음, 클라이언트 PasswordModal에서 검증) |

### 미디어 (Media)

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `uploadMedia(songId, file, onProgress, rehearsalId)` | presigned | R2 직접 업로드 | 미디어 업로드 (3단계: presign → R2 PUT → complete) |
| `deleteMedia(mediaId)` | DELETE | `/media/:id` | 미디어 삭제 |
| `renameMedia(mediaId, newFilename)` | PUT | `/media/:id/rename` | 미디어 이름 변경 |
| `linkMediaToRehearsal(mediaId, rehearsalId)` | PATCH | `/media/:id/rehearsal` | 미디어-합주 연동 변경/해제 |

### 대시보드 (Dashboard)

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchDashboardStats()` | GET | `/dashboard/stats` | 대시보드 통계 |

### 추천곡 (Suggestions)

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchSuggestions()` | GET | `/suggestions` | 추천곡 목록 |
| `createSuggestion(data)` | POST | `/suggestions` | 추천곡 추가 |
| `deleteSuggestion(id, password)` | DELETE | `/suggestions/:id` | 추천곡 삭제 |
| `voteSuggestion(id, voteType)` | POST | `/suggestions/:id/vote` | 투표 (up/down) |

### 멤버 (Members)

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchMembers()` | GET | `/members` | 멤버 목록 |
| `getMember(id)` | GET | `/members/:id` | 멤버 상세 |
| `createMember(data)` | POST | `/members` | 멤버 생성 |
| `updateMember(id, data)` | PUT | `/members/:id` | 멤버 수정 |
| `deleteMember(id)` | DELETE | `/members/:id` | 멤버 삭제 |

### 개인 로그 (Personal Logs)

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchMemberLogs(memberId)` | GET | `/members/:id/logs` | 개인 로그 목록 |
| `uploadPersonalLog(memberId, file, title, onProgress)` | POST | `/members/:id/logs` | 개인 로그 업로드 |
| `deletePersonalLog(logId)` | DELETE | `/personal-logs/:id` | 개인 로그 삭제 |

### 공지사항 (Announcement)

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchAnnouncement()` | GET | `/announcement` | 현재 공지 조회 |
| `updateAnnouncement(content)` | PUT | `/announcement` | 공지 수정 (upsert) |

### 장소 검색 (Search Places) — `LocationPicker.jsx`에서 직접 호출

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| (inline fetch) | GET | `/api/search-places?query=` | 장소명 검색 (Naver Search Local API 프록시) |

### 합주 일정 (Rehearsals) — `rehearsalApi.js`

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchRehearsals(year, month)` | GET | `/rehearsals?year=&month=` | 월별 일정 조회 |
| `getRehearsal(id)` | GET | `/rehearsals/:id` | 일정 단건 조회 |
| `createRehearsal(data)` | POST | `/rehearsals` | 일정 생성 |
| `updateRehearsal(id, data)` | PUT | `/rehearsals/:id` | 일정 수정 |
| `deleteRehearsal(id)` | DELETE | `/rehearsals/:id` | 일정 삭제 |
| `fetchRehearsalMedia(rehearsalId)` | GET | `/rehearsals/:id/media` | 합주 연결 미디어 조회 |
| `uploadRehearsalMedia(rehearsalId, songId, file, onProgress)` | presigned | R2 직접 업로드 | 합주에서 미디어 업로드 (3단계) |

### 댓글 (Comments) — `api.js`

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchComments(targetType, targetId)` | GET | `/:targetType/:id/comments` | 댓글 목록 (대댓글 중첩) |
| `createComment(targetType, targetId, data)` | POST | `/:targetType/:id/comments` | 댓글 작성 |
| `createReply(commentId, data)` | POST | `/comments/:id/replies` | 대댓글 작성 |
| `updateComment(commentId, data)` | PUT | `/comments/:id` | 댓글 수정 (비밀번호 검증) |
| `deleteComment(commentId, password)` | DELETE | `/comments/:id` | 댓글 삭제 (비밀번호 검증) |

> `targetType`은 `"media"` 또는 `"personal-logs"`

### 갤러리 (Gallery) — `galleryApi.js`

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `fetchGalleryImages()` | GET | `/gallery` | 전체 이미지 목록 |
| `uploadGalleryImage(file, onProgress)` | presigned | R2 직접 업로드 | 이미지 업로드 (3단계) |
| `deleteGalleryImage(id)` | DELETE | `/gallery/:id` | 이미지 삭제 |
| `setFeaturedImage(id)` | PATCH | `/gallery/:id/featured` | 대표 이미지 설정 |
| `fetchFeaturedImage()` | GET | `/gallery/featured` | 대표 이미지 1장 조회 |

### 업로드 공통 (Presigned URL) — `uploadApi.js`

| 함수명 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `getPresignedUrl(filename, contentType, uploadType)` | POST | `/uploads/presign` | presigned PUT URL 발급 |
| `uploadToStorage(uploadUrl, file, contentType, onProgress)` | PUT | R2 presigned URL | R2 직접 업로드 (XHR) |
| `completeMediaUpload(data)` | POST | `/uploads/complete/media` | 미디어 메타데이터 등록 |
| `completeGalleryUpload(data)` | POST | `/uploads/complete/gallery` | 갤러리 메타데이터 등록 |

## API 호출 패턴

### 기본 GET 요청
```javascript
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000';

export const fetchSongs = async () => {
  const response = await fetch(`${API_URL}/songs`);
  if (!response.ok) throw new Error('Failed to fetch songs');
  return await response.json();
};
```
> `API_URL`은 `export`되어 `memberApi.js`에서 import하여 재사용.
>
> **파일 URL 주의:** 미디어/녹음 파일의 URL은 백엔드가 R2 presigned URL을 절대경로로 반환하므로, 프론트에서 `API_URL`과 조합하지 않고 `media.url`, `log.recording_url`, `playingLog.url`을 그대로 사용해야 함.

### POST/PUT 요청 (JSON)
```javascript
export async function createSong(songData) {
  const response = await fetch(`${API_URL}/songs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(songData),
  });
  if (!response.ok) throw new Error('Failed to create song');
  return response.json();
}
```

### DELETE 요청 (단순)
```javascript
export const deleteSong = async (id) => {
  const response = await fetch(`${API_URL}/songs/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete song');
  return await response.json();
};
```
> 곡 삭제 비밀번호는 프론트엔드 PasswordModal에서 클라이언트 검증 (기본값 `'admin'`).
> 추천곡 삭제(`deleteSuggestion`)만 백엔드에 password를 전송.

### 파일 업로드 (Presigned URL + 진행률 추적)
```javascript
// 3단계: presign → R2 직접 PUT → complete
export const uploadMedia = async (songId, file, onProgress, rehearsalId) => {
  const { getPresignedUrl, uploadToStorage, completeMediaUpload } = await import('./uploadApi');

  const contentType = file.type || 'application/octet-stream';
  const { upload_url, filename } = await getPresignedUrl(file.name, contentType, 'media');
  await uploadToStorage(upload_url, file, contentType, onProgress);  // XHR PUT, raw file
  return completeMediaUpload({
    filename, original_filename: file.name, file_size: file.size,
    song_id: songId, rehearsal_id: rehearsalId || null,
  });
};
```
> R2 PUT 시 `FormData`가 아닌 **raw file** (`xhr.send(file)`)을 보내야 함.
> `file.type`이 빈 문자열일 수 있으므로 `'application/octet-stream'` fallback 사용.

## 에러 처리 패턴

```javascript
// 컴포넌트에서의 에러 처리
try {
  const data = await fetchSomething();
  setData(data);
} catch (error) {
  console.error('Error:', error);
  alert('오류가 발생했습니다.');  // 사용자에게 alert으로 알림
}
```

- `response.ok` 체크 후 에러 throw
- 컴포넌트에서 try-catch로 처리
- 사용자에게 `alert()`으로 알림 (토스트 라이브러리 미사용)
- Context에서는 `error` 상태로 관리

## 새 API 추가 가이드

1. `src/services/api.js`, `memberApi.js`, `rehearsalApi.js`, 또는 `galleryApi.js`에 함수 추가
2. `API_URL` 기반으로 엔드포인트 구성
3. 파일 업로드면 `uploadApi.js`의 3단계 패턴 사용 (presign → R2 PUT → complete)
4. JSON 요청이면 `Content-Type: application/json` 헤더 설정
5. 에러 시 `throw new Error()` 패턴 유지
