# Band Archive

밴드의 곡, 합주 일정, 멤버별 연습 기록과 미디어를 관리하는 웹 애플리케이션이다.

현재 운영 구성은 다음과 같다.

- 프론트엔드: Cloudflare에서 React 정적 사이트 제공
- 백엔드: Fly.io에서 Flask/Gunicorn 실행
- 데이터베이스: Fly.io 영속 볼륨의 SQLite
- 오브젝트 스토리지: Cloudflare R2
- 외부 연동: NAVER Maps JavaScript SDK 및 NAVER Local Search API

백엔드를 Cloudflare 또는 더 높은 성능의 다른 호스팅으로 옮기는 방안은 향후 검토 사항이며, 현재 운영 환경은 Fly.io다.

## 저장소 구조

```text
Anything/
├─ band-archive/
│  ├─ frontend/       # React/Vite 사용자 화면
│  ├─ backend/        # Flask API, SQLite, R2, FFmpeg, NAVER Search
│  ├─ k3s/            # 검토 중인 k3s 배포 초안
│  └─ .github/        # 과거/대체 GitHub Pages 워크플로
└─ docs/              # 현재 코드 기준 프로젝트 문서
```

## 문서

- [전체 아키텍처](docs/architecture.md)
- [프론트엔드](docs/frontend.md)
- [백엔드](docs/backend.md)
- [인프라 및 운영](docs/infrastructure.md)
- [배포 운영 가이드](docs/deployment.md)
- [개선 로드맵](docs/roadmap.md)

문서는 현재 코드와 운영자가 확인한 배포 사실을 기준으로 한다. 환경변수 이름은 기록하지만 실제 자격증명과 비밀값은 저장소나 문서에 넣지 않는다.

## 빠른 시작

프론트엔드:

```powershell
cd E:\Anything\band-archive\frontend
npm ci
npm run dev
```

백엔드:

```powershell
cd E:\Anything\band-archive\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

로컬 실행 전 필요한 환경변수와 외부 서비스 설정은 [프론트엔드 문서](docs/frontend.md#환경변수)와 [백엔드 문서](docs/backend.md#환경변수)를 확인한다.

## 현재 주의사항

- 변경 API 전반에 공통 인증·인가가 없어 외부 공개 전에 보강이 필요하다.
- R2 직접 업로드는 인증, 실제 파일 크기·내용 검증과 사용량 제한을 서버에서 강화해야 한다.
- FFmpeg 트랜스코딩이 API 프로세스 내부 스레드에서 실행되어 Fly 머신의 응답 성능과 작업 내구성에 영향을 줄 수 있다.
- SQLite 백업·복구 절차와 정식 스키마 마이그레이션 체계가 필요하다.
- 저장소의 k3s 매니페스트와 GitHub Pages 워크플로는 현재 운영 배포의 기준이 아니다.
