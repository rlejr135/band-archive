# 백엔드

## 역할과 현재 배포 상태

`band-archive/backend`는 밴드 아카이브의 REST API 서버다. 곡·합주·멤버·개인 연습 기록·미디어·갤러리·댓글·공지·추천곡을 관리하고, 파일은 Cloudflare R2의 S3 호환 API에 저장한다.

현재 백엔드 배포 대상은 **Fly.io**다. `fly.toml`은 `band-archive` 앱을 도쿄(`nrt`) 리전에 두고, production 설정과 `/data` 볼륨을 사용하도록 정의한다. HTTP 서비스는 내부 포트 `8080`, HTTPS 강제, suspend 기반 자동 중지/시작, 1 GB 메모리·1 vCPU로 설정되어 있다. 데이터베이스 기본 경로는 이 볼륨의 SQLite 파일이다.

백엔드를 Cloudflare 또는 더 높은 성능의 다른 플랫폼으로 옮기는 일은 **현재 구현이나 배포 사실이 아니라 향후 검토 사항**이다. 이전 시에는 SQLite 단일 파일·Fly Volume 의존성, FFmpeg 처리 자원, 환경변수/비밀 관리, R2 연결성을 함께 재검토해야 한다.

## 기술 스택

- Python 3.13 slim 컨테이너, Flask 3, Gunicorn
- Flask-SQLAlchemy, SQLAlchemy ORM, Flask-Migrate
- 기본 DB: SQLite (`DATABASE_URL`로 교체 가능)
- Cloudflare R2: `boto3`를 통한 S3 호환 클라이언트 및 presigned URL
- 동영상 처리: 컨테이너에 설치된 FFmpeg, Python 스레드
- 외부 연동: Naver Search Local API (`requests`)
- 테스트: pytest 및 Flask test client

`Dockerfile`은 FFmpeg를 설치하고 Gunicorn으로 `app:create_app()`을 포트 `8080`에 바인딩한다. timeout은 300초다.

## 애플리케이션 구조

`app.py`의 `create_app()`이 설정을 로드하고 CORS, ORM, Flask-Migrate, 오류 처리기, 스토리지 클라이언트와 모든 Blueprint를 등록하는 앱 팩토리다. 설정 클래스를 직접 넘기지 않으면 `FLASK_CONFIG`에서 클래스를 선택한다.

| 모듈 | 책임 |
| --- | --- |
| `config.py` | 개발·테스트·운영 환경별 Flask/DB/S3 설정 |
| `extensions.py` | 공유 SQLAlchemy 인스턴스 |
| `models.py` | ORM 모델과 API 직렬화 |
| `routes/` | 도메인별 Blueprint 및 요청 검증/처리 |
| `storage.py` | R2 등 S3 호환 저장소의 업로드·다운로드·삭제·복사·presign |
| `transcoder.py` | R2 영상 다운로드, FFmpeg 변환, 결과 재업로드 |
| `validators.py` | 문자열·상태·난이도·확장자·파일명 검증 |
| `errors.py` | `ValidationError`/`NotFoundError`의 JSON 응답 변환 |

개발 모드의 CORS origin은 모든 origin이고 credentials를 허용한다. 운영 모드에서는 `CORS_ALLOWED_ORIGINS`의 쉼표 구분 목록만 허용한다. 앱 시작 시 `db.create_all()` 후 SQLite인 경우에만 누락 컬럼을 `ALTER TABLE`로 보완하고, 이전 `practice_log` 테이블을 삭제한다.

## 데이터 모델과 관계

| 모델 | 주요 관계 |
| --- | --- |
| `Song` | `Media` 1:N, `Rehearsal`과 `rehearsal_songs` 조인 테이블로 N:M |
| `Media` | 필수 `song_id`, 선택 `rehearsal_id`; `Comment` 1:N |
| `Rehearsal` | 일정 날짜/기간, 장소와 위·경도, 연결 곡과 미디어 |
| `Member` | `PersonalLog` 1:N |
| `PersonalLog` | 멤버의 오디오/영상 기록, `Comment` 1:N |
| `Comment` | 미디어 또는 개인 기록에 연결되고, `parent_id`로 대댓글 자기 참조 |
| `GalleryImage` | 갤러리 오브젝트와 대표 이미지 상태 |
| `SongSuggestion` | 추천곡과 찬성/반대 집계 |
| `Announcement` | ID 1 하나만 쓰는 단일 공지 |

`Song`, `Member`, `Media`, `PersonalLog`, `Comment` 일부는 ORM cascade로 하위 레코드를 삭제한다. 오브젝트 저장소의 파일은 각 삭제 라우트에서 별도로 삭제하려 시도한다.

## API 영역

- 곡: `/songs` CRUD, 검색(`q`)과 상태·장르 필터, 악보/미디어 업로드, 미디어-합주 연결, 대표 설정·이름 변경·삭제
- 합주: `/rehearsals` CRUD, 월별/기간 겹침 조회, 곡 N:M 연결, 합주 미디어 조회·업로드
- 멤버·개인 기록: `/members` CRUD, `/members/:id/logs` 조회·업로드, 개인 기록 삭제
- 저장소 직접 업로드: `/uploads/presign`, `/uploads/complete/media`, `/uploads/complete/gallery`
- 갤러리: `/gallery` 조회·업로드·삭제·대표 이미지 설정 및 조회
- 댓글: 미디어/개인 기록 댓글, 대댓글, 비밀번호 기반 수정·삭제
- 부가 기능: `/suggestions` 및 투표, `/announcement`, `/dashboard/stats`, `/api/search-places`

## 업로드, R2, 트랜스코딩

모든 R2 키는 기능별 접두어를 사용한다: `media/`, `personal_logs/`, `gallery/`. 서버가 받는 기존 multipart 업로드와, 대용량 파일을 위한 presigned PUT 업로드가 모두 존재한다.

직접 업로드 흐름은 다음과 같다.

1. 클라이언트가 파일명·콘텐츠 타입·대상(`media` 또는 `gallery`)으로 `/uploads/presign`을 호출한다. 서버는 확장자를 확인하고 UUID 기반 파일명을 만들어 10분짜리 PUT URL을 반환한다.
2. 클라이언트가 R2로 직접 업로드한다.
3. `/uploads/complete/media` 또는 `/uploads/complete/gallery`가 R2 객체 존재를 확인한 뒤 DB 레코드를 만든다.
4. 조회 응답은 기본적으로 1시간짜리 GET presigned URL을 반환한다.

영상 `Media`는 생성 뒤 별도 Python 스레드에서 상태를 `processing`으로 바꾸고, 원본을 임시 디렉터리로 내려받는다. FFmpeg가 720p MP4, 480p MP4, 오디오 전용 M4A를 만든 뒤 R2 `media/`에 업로드한다. 완료되면 `completed`, 오류면 `failed`가 된다. 완료된 영상 응답은 원본·720p·480p·audio URL 묶음도 제공한다.

## Naver Map 및 지역 검색

합주(`Rehearsal`)는 `location`, `latitude`, `longitude`를 저장한다. 백엔드의 Naver 연동은 지도 렌더링 자체가 아니라 Naver Local Search API의 서버 측 프록시다.

`GET /api/search-places?query=...`는 최대 5개 장소를 조회해 Naver 응답의 HTML 강조 태그를 제거하고 제목, 지번/도로명 주소, `mapx`, `mapy`를 반환한다. 클라이언트 ID/Secret이 없으면 500, Naver 호출 실패 시 502를 반환하며 외부 요청 timeout은 5초다. 프론트엔드 지도 SDK의 초기화 방식은 이 백엔드 폴더만으로는 판단할 수 없다.

## 환경변수

값은 저장소나 문서에 기록하지 않는다. 코드와 샘플 설정에서 확인되는 이름은 다음과 같다.

| 변수 | 용도 |
| --- | --- |
| `FLASK_CONFIG` | 사용할 설정 클래스 지정 |
| `DATABASE_URL` | SQLAlchemy DB URL |
| `PORT` | 직접 실행 시 바인딩 포트 |
| `CORS_ALLOWED_ORIGINS` | 운영 CORS 허용 origin 목록 |
| `S3_ENDPOINT_URL` | R2/S3 호환 endpoint |
| `S3_ACCESS_KEY` | 오브젝트 저장소 접근 키 |
| `S3_SECRET_KEY` | 오브젝트 저장소 비밀 키 |
| `S3_BUCKET_NAME` | 오브젝트 저장소 버킷 이름 |
| `NAVER_SEARCH_CLIENT_ID` | Naver Local Search 클라이언트 ID |
| `NAVER_SEARCH_CLIENT_SECRET` | Naver Local Search 클라이언트 Secret |
| `FLASK_APP`, `FLASK_ENV`, `SECRET_KEY` | `.env.example`에 제공되는 Flask 개발 관련 변수; 현 코드에서 `SECRET_KEY`는 Flask config에 명시적으로 주입하지 않음 |
| `UPLOAD_FOLDER` | R2 이전용 스크립트가 기존 로컬 파일 위치를 찾을 때 사용 |

## 로컬 실행과 테스트

백엔드 디렉터리에서 가상환경을 만든 뒤 의존성을 설치하고, `.env.example`을 바탕으로 실제 로컬 DB·R2·Naver 값을 별도 환경 파일 또는 셸 환경변수로 제공한다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
pytest
```

테스트는 곡, 합주, 공지에 대한 pytest 사례가 있다. 테스트 설정은 인메모리 SQLite를 사용하지만, 파일 업로드 테스트 경로는 현재 `StorageClient`를 대체(mock)하지 않고 R2 클라이언트를 초기화한다. 따라서 저장소 환경변수/연결 상태에 따라 테스트가 실패하거나 실제 저장소에 영향을 줄 수 있으므로, 안전한 mock 또는 별도 테스트 버킷을 먼저 갖춰야 한다.

## 현재 한계와 우선 위험

- **인증·인가 부재:** 곡, 합주, 멤버, 갤러리, 공지, 미디어 등 변경 API에 공통 인증이 없다. 추천곡 삭제의 고정 문자열과 댓글 비밀번호만으로는 운영 권한을 보호할 수 없다.
- **업로드 검증:** presign 단계는 확장자와 선언된 content type만 본다. 실제 콘텐츠 검사, 서버 검증 파일 크기 제한, 업로드 주체 인증, 악성 파일 검사와 업로드 완료 레코드의 소유권 검증이 없다. Flask의 200 MB 제한도 R2 직접 PUT에는 적용되지 않는다.
- **트랜스코딩 실행 방식:** Gunicorn 프로세스 내부의 비내구성 스레드에서 CPU·메모리 집약 작업을 수행한다. 서버 재시작/자동 suspend 시 작업이 끊길 수 있고, 동시 업로드 제어·재시도 큐·작업 관찰성이 없다.
- **데이터베이스·마이그레이션:** Fly Volume의 단일 SQLite와 시작 시 `create_all()`/수동 `ALTER TABLE`/테이블 삭제를 병행한다. 다중 인스턴스, 안전한 롤백, 정식 schema revision과 맞지 않으며 배포 전에 백업과 명시적 마이그레이션 전략이 필요하다.
- **오브젝트 정합성:** DB 트랜잭션과 R2 삭제·복사·업로드가 원자적으로 묶이지 않는다. 일부 실패 시 고아 객체나 DB의 깨진 참조가 남을 수 있다. 영상 파일을 합주 경로로 올릴 때에는 트랜스코딩을 시작하지 않는 구현 차이도 있다.
- **오류·운영성:** `get_songs`는 내부 예외 문자열을 응답에 노출한다. 전역 500 처리, 요청 추적, health check, 구조화 로그, 메트릭이 코드에 없다.
