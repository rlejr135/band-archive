# 배포 및 M4A 복구 runbook

이 순서는 production에 아직 실행하지 않은 작업을 위한 절차다. 비밀값·presigned URL·실제 credential은 로그나 문서에 남기지 않는다.

## A. 백업과 read-only 사전 확인

1. Fly SQLite `/data` DB의 일관된 백업과 R2 객체 목록/버전 정책을 확보하고 복원 경로를 검토한다.
2. DB에서 video와 `transcoding_status`별 count, M4A filename 유무를 읽기 전용으로 확인한다.
3. R2에서 원본과 M4A 존재 여부·크기 표본을 확인한다. 당시 사전 감사값(55 videos: completed 0, pending 28, processing 23, failed 4, M4A 0; 200 MiB 초과 44개 원본 존재/DB 크기 일치)은 과거 관측일 뿐 현재 결과가 아니다.

## B. R2 CORS 설정과 검증

`docs/infrastructure.md`의 JSON을 R2 bucket CORS에 적용하되 `<production-frontend-origin>`을 실제 production origin으로 바꾼다. 필요한 method는 browser media GET, single/multipart PUT, 객체 확인 HEAD이고 `Content-Type` 및 서명 관련 `x-amz-*` header를 허용한다. `ETag`는 반드시 `ExposeHeaders`에 둔다.

배포 전 browser DevTools에서 presigned part PUT 응답의 `ETag`를 읽을 수 있는지, CORS preflight가 production origin에서 성공하는지 확인한다. 단일 PUT은 다음 형태로 URL·파일을 로컬에서만 주입해 확인할 수 있다.

```powershell
curl.exe -i -X OPTIONS "<presigned-put-url>" -H "Origin: https://<production-frontend-origin>" -H "Access-Control-Request-Method: PUT" -H "Access-Control-Request-Headers: content-type"
```

R2 multipart lifecycle 및 abandoned upload 정리 정책도 현재 Cloudflare 계정 설정에서 확인한다.

## C. 백엔드 배포

1. Fly secret에 R2·NAVER·DB 값을 확인하고 `AUDIO_PROCESSING_TIMEOUT_SECONDS`, `AUDIO_PROCESSING_HEARTBEAT_SECONDS`, `AUDIO_PROCESSING_STALE_SECONDS`, `AUDIO_WORKER_POLL_SECONDS`를 환경에 맞게 검토한다.
2. backend를 배포하고 `/` 및 실제 읽기 API로 응답을 확인한다. 별도 health endpoint는 코드에 없으므로 존재하지 않는 health URL을 runbook에 가정하지 않는다.
3. 앱 시작 로그에서 schema 보완과 stale recovery, worker 시작을 확인한다. DB에 media 상태/processing 컬럼과 multipart session/part table이 존재하는지 읽기 전용으로 확인한다.
4. Fly가 autostop off/min1인지 확인한다. Gunicorn worker 수를 바꾸면 process당 worker도 늘고 FFmpeg 경합·비용이 변한다. 로그에서 claim, heartbeat, timeout, failed 작업을 관측한다.

## D. repair 실행과 검증

먼저 dry-run 결과의 `examined`, `would_change`, `storage_errors`, queued/failed 후보를 검토한다.

```powershell
cd E:\Anything\band-archive\backend
python repair_media_processing.py --limit 55
```

오류와 대상이 승인된 뒤에만 적용한다.

```powershell
python repair_media_processing.py --enqueue --limit 55
```

후보 filter 뒤 limit이 적용되고 명령은 멱등적이다. 이후 DB 상태 count와 R2 M4A 표본을 확인하며 failed는 `POST /media/<id>/retry-audio`로 제한적으로 재시도한다. production에서 위 명령은 아직 실행하지 않았다.

## E. 프런트엔드 배포와 smoke matrix

Cloudflare 빌드에 `VITE_API_URL`과 `VITE_NAVER_MAP_CLIENT_ID`를 설정하고 새 build를 배포한다. Vite 값은 build-time이므로 변경 시 재빌드한다.

| 기기 | 필수 확인 |
| --- | --- |
| iPhone | <100 MiB single, >100 MiB multipart, 1 GiB 근접, cancel/retry, ETag CORS, completed 후 radio |
| Android | 위와 동일 |
| Desktop | 위와 동일 및 DevTools aggregate progress/OPTIONS 확인 |

## F. 롤백

FE는 먼저 직전 Cloudflare 배포로 되돌린다. 새 enqueue를 중지하고 실행 중 worker 처리 상태를 확인한다. backend를 롤백하더라도 새 DB table/column은 보존하며, schema 호환성을 검토한 뒤에만 앱 버전을 되돌린다. R2 원본과 M4A는 롤백 또는 정리 과정에서 삭제하지 않는다.
