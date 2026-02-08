# Backend Task: 한 곡당 여러 미디어 파일 지원

## 📋 작업 개요
현재 `Song` 모델의 `sheet_music` 필드는 단일 파일만 저장 가능합니다.
한 곡당 여러 개의 미디어 파일(오디오/비디오)을 업로드하고 관리할 수 있도록 백엔드를 수정해주세요.

---

## 🎯 목표
- 한 곡에 여러 미디어 파일 업로드 가능
- 각 미디어 파일의 메타데이터 관리 (파일명, 타입, 업로드 날짜)
- 기존 파일 덮어쓰기 방지
- RESTful API 제공

---

## 📁 현재 구조

### models.py (현재)
```python
class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    artist = db.Column(db.String(100), nullable=False)
    # ... 기타 필드들
    sheet_music = db.Column(db.String(200), nullable=True)  # ⚠️ 단일 파일만 저장
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
```

### routes/songs.py (현재)
```python
@songs_bp.route('/songs/<int:id>/upload', methods=['POST'])
def upload_sheet_music(id):
    song = _get_song_or_404(id)
    
    if 'file' not in request.files:
        raise ValidationError("No file provided")
    
    file = request.files['file']
    # ... 파일 검증
    
    filename = secure_filename(file.filename)
    filename = f"{id}_{filename}"
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
    
    song.sheet_music = filename  # ⚠️ 기존 파일 덮어씀
    db.session.commit()
    return jsonify(song.to_dict()), 200
```

---

## ✅ 요구사항

### 1. 새로운 `Media` 모델 생성

**파일**: `backend/models.py`

```python
class Media(db.Model):
    """곡에 첨부된 미디어 파일 (오디오/비디오)"""
    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    file_type = db.Column(db.String(20), nullable=True)  # 'audio' or 'video'
    file_size = db.Column(db.Integer, nullable=True)  # bytes
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationship
    song = db.relationship('Song', backref=db.backref('media_files', lazy=True, cascade='all, delete-orphan'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'song_id': self.song_id,
            'filename': self.filename,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'url': f'/uploads/{self.filename}',
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
```

### 2. `Song` 모델 수정

**파일**: `backend/models.py`

```python
class Song(db.Model):
    # ... 기존 필드 유지
    # sheet_music 필드는 하위 호환성을 위해 유지하되, deprecated로 표시
    sheet_music = db.Column(db.String(200), nullable=True)  # Deprecated: use media_files instead
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'artist': self.artist,
            'status': self.status,
            'lyrics': self.lyrics,
            'chords': self.chords,
            'link': self.link,
            'memo': self.memo,
            'genre': self.genre,
            'difficulty': self.difficulty,
            'sheet_music': self.sheet_music,  # 하위 호환성
            'media': [media.to_dict() for media in self.media_files],  # ✅ 새로운 필드
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
```

### 3. 미디어 업로드 API 수정

**파일**: `backend/routes/songs.py`

#### 3-1. 기존 엔드포인트 수정 (하위 호환성)
```python
@songs_bp.route('/songs/<int:id>/upload', methods=['POST'])
def upload_sheet_music(id):
    """
    Deprecated: 하위 호환성을 위해 유지
    새로운 /songs/<id>/media 엔드포인트 사용 권장
    """
    song = _get_song_or_404(id)
    
    if 'file' not in request.files:
        raise ValidationError("No file provided")
    
    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")
    
    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
    
    filename = secure_filename(file.filename)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = f"{id}_{timestamp}_{filename}"
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)
    
    # Media 테이블에 추가
    file_type = 'video' if filename.lower().endswith(('.mp4', '.webm', '.mov', '.avi', '.mkv')) else 'audio'
    media = Media(
        song_id=id,
        filename=filename,
        file_type=file_type,
        file_size=os.path.getsize(file_path)
    )
    db.session.add(media)
    
    # sheet_music 필드도 업데이트 (최신 파일로)
    song.sheet_music = filename
    db.session.commit()
    
    return jsonify(song.to_dict()), 200
```

#### 3-2. 새로운 미디어 관리 엔드포인트 추가
```python
@songs_bp.route('/songs/<int:id>/media', methods=['GET'])
def get_media_list(id):
    """곡의 모든 미디어 파일 목록 조회"""
    song = _get_song_or_404(id)
    return jsonify([media.to_dict() for media in song.media_files])


@songs_bp.route('/songs/<int:id>/media', methods=['POST'])
def add_media(id):
    """곡에 새 미디어 파일 추가"""
    song = _get_song_or_404(id)
    
    if 'file' not in request.files:
        raise ValidationError("No file provided")
    
    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")
    
    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
    
    filename = secure_filename(file.filename)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = f"{id}_{timestamp}_{filename}"
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)
    
    # 파일 타입 감지
    file_type = 'video' if filename.lower().endswith(('.mp4', '.webm', '.mov', '.avi', '.mkv')) else 'audio'
    
    # Media 레코드 생성
    media = Media(
        song_id=id,
        filename=filename,
        file_type=file_type,
        file_size=os.path.getsize(file_path)
    )
    db.session.add(media)
    db.session.commit()
    
    return jsonify(media.to_dict()), 201


@songs_bp.route('/media/<int:media_id>', methods=['DELETE'])
def delete_media(media_id):
    """미디어 파일 삭제"""
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")
    
    # 파일 시스템에서 삭제
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], media.filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # DB에서 삭제
    db.session.delete(media)
    db.session.commit()
    
    return jsonify({"message": "Media deleted"}), 200
```

### 4. 데이터베이스 마이그레이션

**중요**: 새로운 `Media` 테이블을 생성해야 합니다.

```bash
# Flask-Migrate 사용 시
flask db migrate -m "Add Media model for multiple files per song"
flask db upgrade

# 또는 app context에서 자동 생성
# app.py의 create_all()이 자동으로 처리
```

### 5. Import 추가

**파일**: `backend/routes/songs.py`

```python
from models import Song, Media  # Media 추가
from datetime import datetime, timezone  # timestamp 생성용
```

---

## 🔍 테스트 시나리오

### 1. 미디어 업로드 테스트
```bash
# 첫 번째 파일 업로드
curl -X POST http://localhost:5000/songs/1/media \
  -F "file=@practice1.mp3"

# 두 번째 파일 업로드 (같은 곡)
curl -X POST http://localhost:5000/songs/1/media \
  -F "file=@practice2.mp4"
```

### 2. 미디어 목록 조회
```bash
curl http://localhost:5000/songs/1/media
```

**예상 응답**:
```json
[
  {
    "id": 1,
    "song_id": 1,
    "filename": "1_20260208_123456_practice1.mp3",
    "file_type": "audio",
    "file_size": 5242880,
    "url": "/uploads/1_20260208_123456_practice1.mp3",
    "created_at": "2026-02-08T12:34:56Z"
  },
  {
    "id": 2,
    "song_id": 1,
    "filename": "1_20260208_123500_practice2.mp4",
    "file_type": "video",
    "file_size": 15728640,
    "url": "/uploads/1_20260208_123500_practice2.mp4",
    "created_at": "2026-02-08T12:35:00Z"
  }
]
```

### 3. 곡 정보 조회 (media 포함)
```bash
curl http://localhost:5000/songs/1
```

**예상 응답**:
```json
{
  "id": 1,
  "title": "Summer of 69",
  "artist": "Bryan Adams",
  "media": [
    {
      "id": 1,
      "filename": "1_20260208_123456_practice1.mp3",
      "file_type": "audio",
      "url": "/uploads/1_20260208_123456_practice1.mp3"
    },
    {
      "id": 2,
      "filename": "1_20260208_123500_practice2.mp4",
      "file_type": "video",
      "url": "/uploads/1_20260208_123500_practice2.mp4"
    }
  ]
}
```

### 4. 미디어 삭제
```bash
curl -X DELETE http://localhost:5000/media/1
```

---

## 📝 주의사항

1. **하위 호환성**: 기존 `sheet_music` 필드는 유지하되, 최신 업로드 파일로 업데이트
2. **파일명 중복 방지**: 타임스탬프를 파일명에 포함
3. **Cascade 삭제**: Song 삭제 시 연결된 Media도 자동 삭제
4. **파일 시스템 정리**: Media 삭제 시 실제 파일도 삭제
5. **에러 처리**: 파일 업로드 실패, 디스크 공간 부족 등 예외 처리

---

## 🎯 프론트엔드 연동 정보

백엔드 작업 완료 후, 프론트엔드는 다음과 같이 API를 사용합니다:

### API 엔드포인트
- `GET /songs/:id/media` - 미디어 목록 조회
- `POST /songs/:id/media` - 미디어 추가
- `DELETE /media/:id` - 미디어 삭제
- `POST /songs/:id/upload` - 기존 엔드포인트 (하위 호환)

### 응답 구조
Song의 `to_dict()`에 `media` 배열이 포함되어야 합니다:
```json
{
  "id": 1,
  "title": "곡 제목",
  "media": [
    {
      "id": 1,
      "filename": "파일명",
      "file_type": "audio",
      "url": "/uploads/파일명"
    }
  ]
}
```

---

## ✅ 완료 체크리스트

- [ ] `Media` 모델 생성 (models.py)
- [ ] `Song.to_dict()`에 media 필드 추가
- [ ] `GET /songs/:id/media` 엔드포인트 구현
- [ ] `POST /songs/:id/media` 엔드포인트 구현
- [ ] `DELETE /media/:id` 엔드포인트 구현
- [ ] 기존 `POST /songs/:id/upload` 수정 (Media 테이블 사용)
- [ ] 데이터베이스 마이그레이션 실행
- [ ] API 테스트 (Postman/curl)
- [ ] 에러 처리 확인

---

## 📞 문의사항

작업 중 궁금한 점이나 추가 요구사항이 있으면 알려주세요!
