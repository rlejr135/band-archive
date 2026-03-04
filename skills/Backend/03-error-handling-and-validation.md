# Error Handling & Validation

---

## Custom Exceptions (`backend/errors.py`)

```python
class ValidationError(Exception):     # → 400 {"error": message}
class NotFoundError(Exception):        # → 404 {"error": message}
```

`register_error_handlers(app)`로 Flask에 등록. Route에서 raise하면 자동으로 JSON 응답 반환.

```python
# 사용 예시
raise ValidationError("title is required")    # → 400
raise NotFoundError()                          # → 404 "Song not found"
raise NotFoundError("Media not found")         # → 404 커스텀 메시지
```

---

## Validators (`backend/validators.py`)

### Constants

```python
VALID_STATUSES = {'Practice', 'Completed', 'OnHold'}

ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp',    # image
    'pdf',                                     # document
    'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac', # audio
    'mp4', 'webm', 'mov', 'avi', 'mkv'        # video
}
```

### Functions

| 함수 | 동작 | 실패 시 |
|------|------|---------|
| `validate_status(status)` | Practice/Completed/OnHold 중 하나인지 확인 | ValidationError |
| `validate_difficulty(difficulty)` | int이고 1~5 범위인지 확인 | ValidationError |
| `validate_required_string(value, field)` | None/빈문자열 체크 | ValidationError |
| `validate_non_empty_string(value, field)` | 빈문자열 체크 | ValidationError |
| `validate_string_length(value, field, max)` | 최대 길이 초과 체크 | ValidationError |
| `allowed_file(filename)` | 확장자가 ALLOWED_EXTENSIONS에 포함되는지 | `False` 반환 |
| `generate_secure_filename(filename)` | UUID 기반 안전한 파일명 생성 | `uuid4().hex + ext` |
| `detect_file_type(filename)` | 확장자 기반 파일 타입 판별 (video/audio/image/document) | `'document'` 반환 |
| `guess_content_type(filename)` | MIME 타입 추측 + `.m4a` → `audio/mp4` 예외 처리 | `'application/octet-stream'` 반환 |

### Secure Filename 생성 로직

```python
def generate_secure_filename(filename):
    ext = filename.rsplit('.', 1)[1].lower()
    return f"{uuid.uuid4().hex}.{ext}"
    # 예: "a1b2c3d4e5f6789012345678abcdef01.mp3"
```

---

## File Upload 검증 패턴

각 route에서 공통적으로 사용하는 패턴:

```python
# 1. 파일 존재 확인
if 'file' not in request.files:
    raise ValidationError("No file provided")

# 2. 파일명 비어있지 않은지
if file.filename == '':
    raise ValidationError("No file selected")

# 3. 확장자 허용 확인
if not allowed_file(file.filename):
    raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

# 4. 안전한 파일명 생성 + 저장
filename = generate_secure_filename(file.filename)
file.save(file_path)
os.chmod(file_path, 0o644)
```

---

## `_get_*_or_404` 패턴

각 blueprint에서 동일한 패턴 사용:

```python
def _get_song_or_404(id):
    song = db.session.get(Song, id)
    if not song:
        raise NotFoundError()
    return song
```

사용되는 변형:
- `_get_song_or_404(id)` → songs.py, practice_logs.py
- `db.session.get(Media, media_id)` → songs.py (media 삭제/이름변경)
- `db.session.get(Member, id)` → members.py
- `db.session.get(PersonalLog, log_id)` → personal_logs.py
