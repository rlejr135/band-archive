# 백엔드

`band-archive/backend`는 Flask/Gunicorn API다. 현재 Fly.io에서 `/data` SQLite 볼륨과 함께 실행되며 R2의 S3 호환 API, FFmpeg, NAVER Local Search를 사용한다.

## 미디어 lifecycle

- video: `queued → processing → completed | failed`
- non-video: `not_required`
- 상태 행: `processing_started_at`, `processing_completed_at`, `processing_heartbeat_at`, `processing_attempts`, `processing_error`
- 산출물: 원본 `media/<video>`와 `media/<video>_audio.m4a`만 유지한다.

`media_processing.py`의 worker는 process당 하나다. `claim_next_queued_media()`의 DB update/claim이 중복 처리를 막고, FFmpeg 실행 중 heartbeat를 갱신한다. 시작 시 stale `processing`은 `AUDIO_PROCESSING_STALE_SECONDS` 기준으로 queue에 복구한다. timeout·heartbeat·stale·poll 설정은 각각 `AUDIO_PROCESSING_TIMEOUT_SECONDS`, `AUDIO_PROCESSING_HEARTBEAT_SECONDS`, `AUDIO_PROCESSING_STALE_SECONDS`, `AUDIO_WORKER_POLL_SECONDS`다.

## Multipart API

| API | 책임 |
| --- | --- |
| `POST /uploads/multipart/initiate` | video·대상·선언 크기(최대 1 GiB)를 검증하고 session, 16 MiB part size 발급 |
| `POST /uploads/multipart/<session_id>/parts` | 특정 part의 presigned PUT URL 발급 |
| `POST /uploads/multipart/<session_id>/complete` | 발급된 part와 ETag를 검증하고 R2 complete, 실제 크기 확인, Media 생성·queue |
| `POST /uploads/multipart/<session_id>/abort` | 진행 중 R2 multipart와 session을 멱등적으로 abort |

기존 single PUT 경로는 `POST /uploads/presign`과 `POST /uploads/complete/media`이다. R2 객체 확인과 DB 기록은 별도 단계이므로 오류 시 고아 객체 점검이 필요하다.

## 복구 명령

실제 production repair는 **아직 실행하지 않았다**. 먼저 아래 dry-run만 실행해 후보와 storage 오류를 검토한다.

```powershell
cd E:\Anything\band-archive\backend
python repair_media_processing.py --limit 55
```

검토가 끝난 경우에만 같은 후보 범위에 적용한다.

```powershell
python repair_media_processing.py --enqueue --limit 55
```

명령은 repair 후보를 먼저 필터링한 뒤 `limit`을 적용하며, dry-run은 DB를 바꾸지 않는다. `--enqueue`는 필요한 상태만 바꾸므로 반복 실행해도 멱등적이다.

## 운영 주의

`fly.toml`은 autostop off 및 min machine 1로 queue worker가 suspend되지 않게 한다. 이는 유휴 Fly 비용을 증가시킨다. Gunicorn의 worker 수를 늘리면 프로세스별 worker도 늘지만 DB claim은 중복 작업을 막는다. 다만 FFmpeg 동시성·CPU/메모리 사용량은 늘어나므로 머신 크기와 로그를 함께 관측해야 한다.

`GET /media/<id>/processing`은 `{status, error, audio_url, attempts, ...}`를 반환하고 `POST /media/<id>/retry-audio`는 실패한 작업을 다시 queue에 넣는다. NAVER 지도 렌더링은 FE SDK의 역할이며, 서버는 `GET /api/search-places`로 Local Search만 프록시한다.
