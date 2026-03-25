# Models & Relationships

파일: `backend/models.py`

## ER Diagram

```
Song (1) ──── (*) Media
Song (*) ──── (*) Rehearsal    (via rehearsal_songs)
Member (1) ── (*) PersonalLog
Media (1) ─── (*) Comment
PersonalLog (1) ── (*) Comment
Comment (1) ── (*) Comment    (self-referential, 대댓글)
SongSuggestion (독립)
Announcement (독립, 단일 레코드)
GalleryImage (독립, is_featured로 대표 이미지 관리)
```

모든 relationship은 `cascade='all, delete-orphan'` 적용.

---

## Song

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| title | String(100) | NOT NULL | - |
| artist | String(100) | NOT NULL | - |
| status | String(20) | - | 'Practice' |
| chords | Text | nullable | - |
| link | String(200) | nullable | - |
| memo | Text | nullable | - |
| genre | String(50) | nullable | - |
| difficulty | Integer | - | 3 |
| sheet_music | String(200) | nullable | - |
| created_at | DateTime | - | UTC now |
| updated_at | DateTime | - | UTC now, auto-update |

**Relationships:**
- `media_files` (backref from Media)
- `rehearsals` (N:M via `rehearsal_songs`)

**`to_dict()` 출력:**
```json
{
  "id": 1, "title": "Be I", "artist": "Hebi",
  "status": "Practice", "chords": "", "link": "...",
  "memo": "", "genre": "", "difficulty": 3, "sheet_music": null,
  "has_featured_media": false,
  "media": [{ ... }],
  "created_at": "2026-02-08T16:26:31.322338",
  "updated_at": "2026-02-08T16:26:31.322345"
}
```

---

## Media

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| song_id | Integer | FK(song.id), NOT NULL | - |
| rehearsal_id | Integer | FK(rehearsal.id), nullable | - |
| filename | String(200) | NOT NULL | - |
| original_filename | String(200) | nullable | - |
| file_type | String(20) | nullable | - |
| file_size | Integer | nullable | - |
| is_featured | Boolean | - | False |
| created_at | DateTime | - | UTC now |

**Relationships:** `song` (N:1), `rehearsal` (N:1, nullable)
**대표 미디어:** 곡당 1개. 설정 시 같은 곡의 기존 대표 자동 해제.

**file_type 값:** `'video'`, `'audio'`, `'image'`, `'document'`

**`to_dict()` 출력:**
```json
{
  "id": 1, "song_id": 1, "rehearsal_id": 3,
  "rehearsal_title": "합주", "rehearsal_date": "2026-03-01",
  "song_title": "곡 제목", "song_artist": "아티스트",
  "filename": "원본파일명.m4a",
  "file_type": "audio", "file_size": 2090804,
  "url": "/uploads/1_20260208_162641_uuid.m4a",
  "is_featured": false,
  "comment_count": 2,
  "created_at": "2026-02-08T16:26:41.345637"
}
```
> `filename` 필드는 `original_filename`이 있으면 그걸 반환, 없으면 `filename` 반환.

---

## SongSuggestion

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| title | String(100) | NOT NULL | - |
| artist | String(100) | NOT NULL | - |
| link | String(500) | NOT NULL | - |
| memo | Text | nullable | - |
| thumbs_up | Integer | - | 0 |
| thumbs_down | Integer | - | 0 |
| created_at | DateTime | - | UTC now |

---

## Member

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| name | String(100) | NOT NULL | - |
| instrument | String(100) | NOT NULL | - |
| created_at | DateTime | - | UTC now |

**Relationships:** `personal_logs` (backref from PersonalLog)

---

## PersonalLog

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| member_id | Integer | FK(member.id), NOT NULL | - |
| title | String(200) | NOT NULL | - |
| filename | String(200) | NOT NULL | - |
| original_filename | String(200) | nullable | - |
| file_type | String(20) | NOT NULL | - |
| file_size | Integer | nullable | - |
| created_at | DateTime | - | UTC now |

**file_type 값:** `'audio'`, `'video'` (only)
**파일 경로:** `/uploads/personal_logs/{filename}`

---

## Rehearsal

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| title | String(200) | NOT NULL | - |
| date | Date | NOT NULL | - |
| start_date | Date | nullable | - |
| end_date | Date | nullable | - |
| time | String(20) | nullable | - |
| location | String(200) | nullable | - |
| latitude | Float | nullable | - |
| longitude | Float | nullable | - |
| memo | Text | nullable | - |
| color | String(7) | - | '#ffd32a' |
| created_at | DateTime | - | UTC now |
| updated_at | DateTime | - | UTC now, auto-update |

**Relationships:** `songs` (N:M via `rehearsal_songs`), `media_files` (1:N, Media.rehearsal_id)

---

## rehearsal_songs (연결 테이블)

| 필드 | 타입 | 제약 |
|------|------|------|
| rehearsal_id | Integer | FK(rehearsal.id), PK |
| song_id | Integer | FK(song.id), PK |

---

## Announcement

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | 항상 1 (단일 레코드) |
| content | Text | NOT NULL | - |
| updated_at | DateTime | - | UTC now, auto-update |

---

## GalleryImage

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| filename | String(200) | NOT NULL | - |
| original_filename | String(200) | nullable | - |
| file_size | Integer | nullable | - |
| is_featured | Boolean | - | False |
| created_at | DateTime | - | UTC now |

**파일 경로:** `gallery/{filename}` (R2)
**대표 이미지:** `is_featured=True`인 레코드 1개. 변경 시 기존 대표 해제 후 새로 설정.

**`to_dict()` 출력:**
```json
{
  "id": 1,
  "filename": "원본파일명.jpg",
  "file_size": 204800,
  "is_featured": true,
  "url": "presigned-url...",
  "created_at": "2026-03-04T12:00:00.000000"
}
```

---

## Comment

| 필드 | 타입 | 제약 | 기본값 |
|------|------|------|--------|
| id | Integer | PK | auto |
| media_id | Integer | FK(media.id), nullable | - |
| personal_log_id | Integer | FK(personal_log.id), nullable | - |
| parent_id | Integer | FK(comment.id), nullable | - |
| author | String(50) | NOT NULL | - |
| password_hash | String(200) | NOT NULL | - |
| content | Text | NOT NULL | - |
| created_at | DateTime | - | UTC now |

- `media_id` / `personal_log_id`: 둘 중 하나만 연결 (다형성)
- `parent_id`: NULL이면 최상위 댓글, 값이 있으면 대댓글
- 비밀번호: `werkzeug.security`로 해시 저장/검증
- **Relationships:** `replies` (self-referential, cascade delete)

**`to_dict()` 출력 (password 미포함, replies 중첩):**
```json
{
  "id": 1, "media_id": 1, "personal_log_id": null,
  "parent_id": null, "author": "홍길동",
  "content": "이 부분 좋다!",
  "created_at": "...",
  "replies": [{ ... }]
}
```
