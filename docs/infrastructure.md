# 인프라 및 운영

## 현재 운영

| 구성요소 | 서비스 | 책임 |
| --- | --- | --- |
| FE | Cloudflare | Vite 정적 파일 |
| BE | Fly.io | Flask API, Gunicorn, FFmpeg, M4A queue worker |
| DB | Fly Volume SQLite | 서비스 및 multipart session 메타데이터 |
| Object storage | Cloudflare R2 | 원본 video와 M4A |
| 지도/검색 | NAVER | 브라우저 지도 SDK / 서버 Local Search |

Fly는 autostop off, min machine 1이다. 머신이 계속 살아 queue를 진행시키지만 유휴 비용이 발생한다. 프로세스별 worker 하나라는 전제에서 Gunicorn worker 수는 FFmpeg 동시성의 상한에도 영향을 준다.

## 현재 위험과 이전 평가

SQLite 단일 볼륨, API 프로세스와 FFmpeg의 자원 공유, DB/R2 비원자성은 장애 복구의 핵심 위험이다. 백엔드의 Cloudflare 또는 타 고성능 호스팅 이전은 아직 결정되지 않았다. 후보는 장시간 FFmpeg 실행/큐, cold start, R2 지연·비용, 관리형 DB 전환, observability, backup/restore, rollback을 동일 부하로 평가한다. 짧은 API에 적합한 serverless runtime만으로 현재 worker 요구사항을 충족한다고 가정하지 않는다.

## R2 CORS 기준

프로덕션 FE origin만 정확히 넣고 실제 도메인으로 대체한다. multipart와 single PUT, 처리 상태 확인에 필요한 예시는 다음과 같다.

```json
[
  {
    "AllowedOrigins": ["https://<production-frontend-origin>"],
    "AllowedMethods": ["GET", "PUT", "HEAD"],
    "AllowedHeaders": ["Content-Type", "x-amz-*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

`ETag`가 없으면 browser는 multipart complete에 필요한 값을 읽을 수 없다. 프리뷰 origin을 허용해야 한다면 별도로 명시하며 wildcard origin으로 대체하지 않는다. Cloudflare 계정의 현재 UI/API에서 multipart upload lifecycle과 abandoned upload 자동 정리 정책을 확인하고 설정한다. 이 문서는 해당 기능의 콘솔/API 명칭을 단정하지 않는다.
