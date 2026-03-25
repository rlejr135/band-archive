# API Endpoints

Base URL: `https://band-archive.fly.dev` (prod) / `http://localhost:5000` (dev)

---

## Songs (`routes/songs.py`)

### `GET /songs`
곡 목록 조회 (검색/필터 지원)

| Query Param | 설명 |
|-------------|------|
| `q` | 제목/아티스트 검색 (case-insensitive, partial match) |
| `status` | 상태 필터 (`Practice`, `Completed`, `OnHold`) |
| `genre` | 장르 필터 |

**Response:** `200` Song[] (media 포함)

### `GET /songs/<id>`
곡 상세 조회
**Response:** `200` Song | `404`

### `POST /songs`
곡 생성
```json
{
  "title": "곡 제목",        // required, max 100
  "artist": "아티스트",      // required, max 100
  "status": "Practice",      // optional, default "Practice"
  "difficulty": 3,           // optional, 1-5, default 3
  "chords": "",              // optional
  "link": "",                // optional, max 200
  "memo": "",                // optional
  "genre": ""                // optional, max 50
}
```
**Response:** `201` Song | `400`

### `PUT /songs/<id>`
곡 수정 (partial update)
**Body:** 위와 동일한 필드 (모두 optional)
**Response:** `200` Song | `400` | `404`

### `DELETE /songs/<id>`
곡 삭제 (cascade: media 함께 삭제)
**Response:** `200` `{"message": "Song deleted"}` | `404`

---

## Media (`routes/songs.py`)

### `GET /songs/<id>/media`
곡의 미디어 파일 목록
**Response:** `200` Media[]

### `POST /songs/<id>/media`
미디어 파일 업로드 (multipart/form-data)
**Body:** `file` (max 200MB), `rehearsal_id` (optional, 합주 연동)
**허용 확장자:** png, jpg, jpeg, gif, webp, pdf, mp3, wav, ogg, m4a, aac, flac, mp4, webm, mov, avi, mkv
**Response:** `201` Media | `400` | `404`

### `POST /songs/<id>/upload`
악보 업로드 (sheet_music 필드에 저장)
**Body:** `file` (multipart)
**Response:** `200` Song | `400` | `404`

### `PUT /media/<media_id>/rename`
미디어 파일명 변경
```json
{ "filename": "새파일명.mp3" }
```
**Response:** `200` Media | `400` | `404`

### `PATCH /media/<media_id>/featured`
대표 미디어로 설정 (같은 곡 내 기존 대표 자동 해제, 곡당 1개)
**Response:** `200` Media | `404`

### `PATCH /media/<media_id>/rehearsal`
미디어-합주 연동 변경/해제
```json
{ "rehearsal_id": 3 }   // null이면 연동 해제
```
**Response:** `200` Media | `404`

### `DELETE /media/<media_id>`
미디어 파일 삭제 (DB + 디스크)
**Response:** `200` | `404`

### `GET /uploads/<filename>`
파일 다운로드 (static serving)
> `.m4a` 파일은 `Content-Type: audio/mp4`로 강제 설정

---

## Suggestions (`routes/suggestions.py`)

### `GET /suggestions`
추천곡 목록 (순투표수 내림차순)
**Response:** `200` SongSuggestion[]

### `POST /suggestions`
추천곡 등록
```json
{
  "title": "곡 제목",    // required
  "artist": "아티스트",  // required
  "link": "URL",         // required
  "memo": "메모"         // optional
}
```
**Response:** `201` SongSuggestion | `400`

### `DELETE /suggestions/<id>`
추천곡 삭제 (비밀번호 필요)
```json
{ "password": "admin" }
```
**Response:** `200` | `400` | `404`

### `POST /suggestions/<id>/vote`
투표
```json
{ "vote_type": "up" }   // "up" 또는 "down"
```
**Response:** `200` SongSuggestion | `400` | `404`

---

## Members (`routes/members.py`)

### `GET /members`
멤버 목록 **Response:** `200` Member[]

### `POST /members`
멤버 등록
```json
{ "name": "이름", "instrument": "악기" }
```
**Response:** `201` Member | `400`

### `GET /members/<id>`
멤버 상세 **Response:** `200` Member | `404`

### `PUT /members/<id>`
멤버 수정 **Response:** `200` Member | `400` | `404`

### `DELETE /members/<id>`
멤버 삭제 (cascade: personal_logs 함께 삭제)
**Response:** `200` | `404`

---

## Personal Logs (`routes/personal_logs.py`)

### `GET /members/<member_id>/logs`
멤버의 연습 기록 목록 (최신순)
**Response:** `200` PersonalLog[]

### `POST /members/<member_id>/logs`
연습 기록 업로드 (multipart/form-data)
**Body:** `title` (form field) + `file` (audio/video only)
**허용 확장자:** mp3, wav, ogg, m4a, aac, flac, mp4, webm, mov, avi, mkv
**Content-Type fallback:** 확장자 검증 실패 시 Content-Type 헤더로 재판단 (모바일 대응)
**Response:** `201` PersonalLog | `400` | `404`

### `DELETE /personal-logs/<log_id>`
연습 기록 삭제 (DB + 디스크)
**Response:** `200` | `404`

### `GET /uploads/personal_logs/<filename>`
개인 연습 파일 다운로드

---

## Announcements (`routes/announcements.py`)

### `GET /announcement`
현재 공지 조회 (없으면 빈 응답)
**Response:** `200` Announcement | `200` `{}`

### `PUT /announcement`
공지 수정 (없으면 생성, upsert — id=1 단일 레코드)
```json
{ "content": "공지 내용" }
```
**Response:** `200` Announcement | `400`

---

## Rehearsals (`routes/rehearsals.py`)

### `GET /rehearsals`
합주 일정 조회 (월별 필터)

| Query Param | 설명 |
|-------------|------|
| `year` | 연도 필터 |
| `month` | 월 필터 |

**Response:** `200` Rehearsal[]

### `GET /rehearsals/<id>`
합주 일정 단건 조회 (연결된 곡 목록 포함)
**Response:** `200` Rehearsal | `404`

### `POST /rehearsals`
합주 일정 생성
```json
{
  "title": "일정 제목",
  "date": "2026-02-20",
  "start_date": "2026-02-20",
  "end_date": "2026-02-22",
  "time": "19:00",
  "location": "장소명",
  "latitude": 37.5665,
  "longitude": 126.978,
  "memo": "메모",
  "color": "#ffd32a",
  "song_ids": [1, 2]
}
```
**Response:** `201` Rehearsal | `400`

### `PUT /rehearsals/<id>`
합주 일정 수정
**Body:** 위와 동일한 필드 (모두 optional)
**Response:** `200` Rehearsal | `400` | `404`

### `DELETE /rehearsals/<id>`
합주 일정 삭제
**Response:** `200` | `404`

### `GET /rehearsals/<id>/media`
합주에 연결된 미디어 목록 조회
**Response:** `200` Media[]

### `POST /rehearsals/<id>/media`
합주에서 미디어 업로드 (multipart/form-data)
**Body:** `file` + `song_id` (필수, 미디어가 속할 곡)
**Response:** `201` Media | `400` | `404`

---

## Search Places (`routes/search.py`)

### `GET /api/search-places`
장소명 검색 (Naver Search Local API 프록시)

| Query Param | 설명 |
|-------------|------|
| `query` | 검색어 (장소명, 주소 등) |

**Response:** `200` `[{title, address, roadAddress, mapx, mapy}]`
> - `mapx`, `mapy`는 Katec(TM128) 좌표
> - `title`에서 HTML 태그(`<b>`, `</b>`) 자동 제거
> - 최대 5개 결과 반환
> - 환경변수: `NAVER_SEARCH_CLIENT_ID`, `NAVER_SEARCH_CLIENT_SECRET` 필요

---

## Comments (`routes/comments.py`)

### `GET /media/<media_id>/comments`
미디어 댓글 목록 (대댓글 중첩)
**Response:** `200` Comment[]

### `POST /media/<media_id>/comments`
미디어 댓글 작성
```json
{ "author": "홍길동", "password": "1234", "content": "이 부분 좋다!" }
```
**Response:** `201` Comment | `400` | `404`

### `GET /personal-logs/<log_id>/comments`
개인로그 댓글 목록 (대댓글 중첩)
**Response:** `200` Comment[]

### `POST /personal-logs/<log_id>/comments`
개인로그 댓글 작성
**Body:** 위와 동일
**Response:** `201` Comment | `400` | `404`

### `POST /comments/<id>/replies`
대댓글 작성
**Body:** 위와 동일
**Response:** `201` Comment | `400` | `404`

### `PUT /comments/<id>`
댓글 수정 (비밀번호 검증)
```json
{ "password": "1234", "content": "수정된 내용" }
```
**Response:** `200` Comment | `400` | `404`

### `DELETE /comments/<id>`
댓글 삭제 (비밀번호 검증, 하위 대댓글 cascade)
```json
{ "password": "1234" }
```
**Response:** `200` | `400` | `404`

---

## Gallery (`routes/gallery.py`)

### `GET /gallery`
전체 이미지 목록 (최신순)
**Response:** `200` GalleryImage[]

### `POST /gallery`
이미지 업로드 (multipart/form-data)
**Body:** `file` (이미지만: png, jpg, jpeg, gif, webp)
**Response:** `201` GalleryImage | `400`

### `DELETE /gallery/<id>`
이미지 삭제 (DB + R2)
**Response:** `200` | `404`

### `PATCH /gallery/<id>/featured`
대표 이미지로 설정 (기존 대표 자동 해제)
**Response:** `200` GalleryImage | `404`

### `GET /gallery/featured`
대표 이미지 1장 조회 (대시보드용, 없으면 null)
**Response:** `200` GalleryImage | `200` null

---

## Uploads (`routes/uploads.py`)

### `POST /uploads/presign`
presigned PUT URL 발급 (클라이언트 → R2 직접 업로드용)
```json
{
  "filename": "concert.mp4",
  "content_type": "video/mp4",
  "upload_type": "media"
}
```
- `upload_type`: `"media"` 또는 `"gallery"`
- `content_type` 생략 시 `guess_content_type`으로 자동 결정
- 확장자 검증: media는 `allowed_file`, gallery는 이미지만 허용

**Response:** `200` `{ upload_url, key, filename, content_type }` | `400`

### `POST /uploads/complete/media`
R2 직접 업로드 후 미디어 메타데이터 DB 등록
```json
{
  "filename": "abc123.mp4",
  "original_filename": "concert.mp4",
  "file_size": 52428800,
  "song_id": 7,
  "rehearsal_id": null
}
```
- `storage.exists()` 로 실제 업로드 확인
- `detect_file_type`으로 file_type 자동 결정

**Response:** `201` Media | `400` | `404`

### `POST /uploads/complete/gallery`
R2 직접 업로드 후 갤러리 이미지 메타데이터 DB 등록
```json
{
  "filename": "abc123.jpg",
  "original_filename": "band_photo.jpg",
  "file_size": 2048000
}
```
**Response:** `201` GalleryImage | `400`

---

## Dashboard (`routes/dashboard.py`)

### `GET /dashboard/stats`
대시보드 통계
**Response:**
```json
{
  "total_songs": 5,
  "status_counts": { "Practice": 4, "Completed": 1 }
}
```
