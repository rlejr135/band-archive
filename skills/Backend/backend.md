<!-- Last synced commit: 487f5b4d7ebb776cf61307fe420d8e5b844cb7d1 -->

# Band Archive Backend

## 개요

밴드 합주/연습 관리용 REST API 서버. 곡 관리, 미디어 파일 업로드, 연습 기록, 멤버 관리, 곡 추천 투표, 공지사항, 합주 일정(캘린더) 기능을 제공한다.

## 기술 스택

- **프레임워크:** Flask 3.0.0
- **ORM:** Flask-SQLAlchemy 3.1.1
- **DB:** SQLite (개발/배포 모두)
- **파일 스토리지:** Cloudflare R2 (S3 호환, boto3)
- **마이그레이션:** Flask-Migrate 4.0.5
- **CORS:** Flask-Cors 4.0.0
- **서버:** Gunicorn (프로덕션), Werkzeug (개발)
- **배포:** Docker + Fly.io (도쿄 리전 `nrt`)
- **테스트:** pytest 7.4.3

## 디렉토리 구조

```
band-archive/backend/
├── app.py              # Flask 앱 팩토리, 블루프린트 등록, 시작 시 마이그레이션
├── config.py           # Dev/Test/Prod 설정 클래스 + S3 환경변수
├── extensions.py       # SQLAlchemy 인스턴스
├── storage.py          # S3 호환 스토리지 추상화 (upload/delete/generate_url/exists/copy)
├── models.py           # 전체 DB 모델 (8개 + 연결 테이블 1개)
├── errors.py           # ValidationError, NotFoundError 커스텀 예외
├── validators.py       # 입력 검증 유틸리티
├── requirements.txt    # 의존성
├── .env                # 환경 변수
├── Dockerfile          # python:3.13-slim 기반
├── fly.toml            # Fly.io 배포 설정
├── routes/
│   ├── songs.py        # 곡 CRUD + 미디어 관리 + R2 파일 관리 (가장 복잡)
│   ├── practice_logs.py # 연습 기록
│   ├── members.py      # 멤버 관리
│   ├── personal_logs.py # 개인 녹음 기록
│   ├── suggestions.py  # 곡 추천 + 투표
│   ├── announcements.py # 공지사항 (단일 레코드 upsert)
│   ├── rehearsals.py   # 합주 일정 CRUD (달력 기능)
│   └── dashboard.py    # 통계
└── tests/
    ├── conftest.py     # pytest 픽스처
    ├── test_songs.py   # 곡 엔드포인트 테스트
    ├── test_announcements.py # 공지사항 테스트
    └── test_rehearsals.py    # 합주 일정 테스트
```

## DB 모델

### Song
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | |
| title | String(100), Required | 곡 제목 |
| artist | String(100), Required | 아티스트 |
| status | String(20), Default='Practice' | Practice / Completed / OnHold |
| chords | Text | 코드 |
| link | String(200) | 참고 링크 |
| memo | Text | 메모 |
| genre | String(50) | 장르 |
| difficulty | Integer, Default=3 | 난이도 1~5 |
| sheet_music | String(200) | 악보 파일명 |
| created_at / updated_at | DateTime | 자동 생성/갱신 |

관계: `media_files` (1:N → Media), `practice_logs` (1:N → PracticeLog), `rehearsals` (N:M → Rehearsal via `rehearsal_songs`)

### Media
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | |
| song_id | FK → Song | |
| filename | String(200) | UUID 기반 저장 파일명 |
| original_filename | String(200) | 원본 파일명 (표시용) |
| file_type | String(20) | audio / video / image / document |
| file_size | Integer | 바이트 단위 |

### Member
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | |
| name | String(100), Required | 이름 |
| instrument | String(100), Required | 악기 |

관계: `personal_logs` (1:N → PersonalLog)

### PersonalLog
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | |
| member_id | FK → Member | |
| title | String(200), Required | 녹음 제목 |
| filename | String(200) | UUID 저장 파일명 |
| original_filename | String(200) | 원본 파일명 |
| file_type | String(20) | audio / video만 허용 |
| file_size | Integer | 바이트 단위 |

S3 Key: `personal_logs/{filename}`

### PracticeLog
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | |
| song_id | FK → Song | |
| date | DateTime | 연습 날짜 |
| content | Text | 연습 내용 |
| feedback | Text | 피드백 |
| recording | String(200) | 녹음 파일명 |

### SongSuggestion
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | |
| title | String(100), Required | 곡 제목 |
| artist | String(100), Required | 아티스트 |
| link | String(500), Required | 링크 |
| memo | Text | 메모 |
| thumbs_up / thumbs_down | Integer, Default=0 | 투표 수 |

### Rehearsal
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | |
| title | String(200), Required | 일정 제목 |
| date | Date, Required | 합주 날짜 (단일 일정용) |
| start_date | Date | 기간 시작일 (기간 일정용) |
| end_date | Date | 기간 종료일 (기간 일정용) |
| time | String(20) | 합주 시간 (예: "19:00") |
| location | String(200) | 장소 |
| latitude | Float | 위도 (Naver Map) |
| longitude | Float | 경도 (Naver Map) |
| memo | Text | 메모 |
| color | String(7), Default='#ffd32a' | 달력 표시 색상 |
| created_at / updated_at | DateTime | 자동 생성/갱신 |

관계: `songs` (N:M → Song via `rehearsal_songs`)

### rehearsal_songs (연결 테이블)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| rehearsal_id | FK → Rehearsal, PK | |
| song_id | FK → Song, PK | |

### Announcement
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK | 항상 1 (단일 레코드) |
| content | Text, Required | 공지 본문 |
| updated_at | DateTime | 마지막 수정 시각 |

## API 엔드포인트

### 곡 관리 (`/songs`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/songs` | 전체 조회 (필터: `q`, `status`, `genre`) |
| GET | `/songs/<id>` | 단건 조회 |
| POST | `/songs` | 생성 |
| PUT | `/songs/<id>` | 수정 |
| DELETE | `/songs/<id>` | 삭제 (미디어 파일 캐스케이드 삭제) |

### 미디어 (`/songs/<id>/media`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/songs/<id>/media` | 곡의 미디어 목록 |
| POST | `/songs/<id>/media` | 미디어 업로드 |
| POST | `/songs/<id>/upload` | 악보 업로드 |
| PUT | `/media/<id>/rename` | 미디어 이름 변경 (한글 지원) |
| DELETE | `/media/<id>` | 미디어 삭제 |
| GET | `/uploads/<filename>` | R2 presigned URL로 리다이렉트 (하위 호환) |

### 연습 기록 (`/practice-logs`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/songs/<id>/practice-logs` | 곡별 연습 기록 조회 |
| POST | `/songs/<id>/practice-logs` | 연습 기록 생성 |
| GET | `/practice-logs/<id>` | 단건 조회 |
| PUT | `/practice-logs/<id>` | 수정 |
| DELETE | `/practice-logs/<id>` | 삭제 |
| POST | `/practice-logs/<id>/upload` | 녹음 업로드 |

### 멤버 (`/members`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/members` | 전체 조회 |
| POST | `/members` | 생성 |
| GET | `/members/<id>` | 단건 조회 |
| PUT | `/members/<id>` | 수정 |
| DELETE | `/members/<id>` | 삭제 |

### 개인 녹음 (`/members/<id>/logs`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/members/<id>/logs` | 멤버별 녹음 조회 |
| POST | `/members/<id>/logs` | 녹음 업로드 (audio/video만) |
| DELETE | `/personal-logs/<id>` | 녹음 삭제 |
| GET | `/uploads/personal_logs/<filename>` | R2 presigned URL로 리다이렉트 (하위 호환) |

### 곡 추천 (`/suggestions`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/suggestions` | 전체 조회 (투표 차이순 정렬) |
| POST | `/suggestions` | 추천 등록 |
| DELETE | `/suggestions/<id>` | 삭제 (비밀번호: "admin") |
| POST | `/suggestions/<id>/vote` | 투표 (vote_type: "up" / "down") |

### 공지사항 (`/announcement`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/announcement` | 현재 공지 조회 (없으면 빈 응답) |
| PUT | `/announcement` | 공지 수정 (없으면 생성, upsert) |

### 합주 일정 (`/rehearsals`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/rehearsals` | 전체 조회 (쿼리: `year`, `month` 필터) |
| GET | `/rehearsals/<id>` | 단건 조회 (연결된 곡 목록 포함) |
| POST | `/rehearsals` | 일정 생성 (song_ids로 곡 연결 가능) |
| PUT | `/rehearsals/<id>` | 일정 수정 |
| DELETE | `/rehearsals/<id>` | 일정 삭제 |

### 대시보드 (`/dashboard`)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/dashboard/stats` | 통계 (총 곡 수, 상태별 수, 최근 기록) |

### 기타
| Method | Path | 설명 |
|--------|------|------|
| GET | `/` | 헬스체크 ("Band Archive API is running!") |

## 파일 스토리지 (Cloudflare R2)

- **스토리지:** Cloudflare R2 (S3 호환 API, boto3 사용)
- **추상화:** `storage.py` — `StorageClient` 클래스 (upload, delete, generate_url, exists, copy)
- **파일 서빙:** presigned URL 방식 (to_dict()에서 직접 반환)
- **S3 Key 구조:**
  - 곡 미디어/악보: `media/{uuid}.{ext}`
  - 연습 녹음: `recordings/{uuid}.{ext}`
  - 개인 로그: `personal_logs/{uuid}.{ext}`
- **허용 확장자:**
  - 이미지: png, jpg, jpeg, gif, webp
  - 문서: pdf
  - 오디오: mp3, wav, ogg, m4a, aac, flac
  - 비디오: mp4, webm, mov, avi, mkv
- **최대 크기:** 200MB
- **파일명:** UUID 기반 랜덤 생성 (`{uuid}.{ext}`), 원본 이름은 DB에 별도 저장
- **M4A 특수 처리:** Content-Type을 `audio/mp4`로 설정 (브라우저 호환)
- **하위 호환:** `/uploads/` 엔드포인트는 R2 presigned URL로 302 리다이렉트

## 설정 및 환경 변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| FLASK_ENV | 실행 환경 | development |
| DATABASE_URL | DB 경로 | sqlite:///band_archive.db |
| SECRET_KEY | Flask 시크릿 | dev-secret-key |
| CORS_ALLOWED_ORIGINS | 허용 오리진 (콤마 구분) | localhost:5173,3000 |
| FLASK_CONFIG | 설정 클래스 | (자동) |
| S3_ENDPOINT_URL | R2 엔드포인트 URL | - |
| S3_ACCESS_KEY | R2 Access Key ID | - |
| S3_SECRET_KEY | R2 Secret Access Key | - |
| S3_BUCKET_NAME | R2 버킷 이름 | - |
| PORT | 서버 포트 | 5000 |

### 설정 클래스
- **DevelopmentConfig:** DEBUG=True, SQLite 로컬 파일
- **TestingConfig:** TESTING=True, 인메모리 SQLite
- **ProductionConfig:** DEBUG=False, `/data/band_archive.db`, R2 스토리지

## 시작 시 마이그레이션 (`_run_migrations`)

SQLite 환경에서 `db.create_all()`로 추가되지 않는 컬럼을 수동 체크/추가:
- `media` 테이블: `original_filename` 컬럼
- `personal_log` 테이블: `original_filename` 컬럼
- `personal_log` 테이블: `file_size` 컬럼
- `rehearsal` 테이블: `location`, `latitude`, `longitude` 컬럼

## 인증

현재 인증 체계 없음. 곡 추천 삭제 시에만 하드코딩된 비밀번호(`"admin"`) 확인.

## 캐스케이드 삭제

- Song 삭제 → Media (DB + R2), PracticeLog recording (R2) 자동 삭제
- Member 삭제 → PersonalLog (DB + R2) 자동 삭제

## 배포

- **플랫폼:** Fly.io (도쿄 `nrt`)
- **이미지:** python:3.13-slim
- **서버:** gunicorn --bind 0.0.0.0:8080
- **DB 볼륨:** 2GB 영구 볼륨 `/data` 마운트 (SQLite만 사용)
- **파일 스토리지:** Cloudflare R2 (S3 호환)
- **프론트엔드 오리진:** `https://rlejr135.github.io`

## 실행 방법

```bash
# 개발
cd band-archive/backend
pip install -r requirements.txt
flask run --port 5000

# 테스트
pytest

# 프로덕션 (Docker)
docker build -t band-archive .
docker run -p 8080:8080 band-archive
```
