# 전체 아키텍처

기준일: 2026-08-23. 이 문서는 구현 커밋 `79785e0`, `d068982`, `405adab`, `623d024`과 당시 운영 관측을 기준으로 한다.

## 현재 구성

```text
브라우저 ── 정적 React/Vite ──► Cloudflare (FE)
    │                                  │
    ├─ REST API ───────────────────────► Fly.io Flask/Gunicorn ─► SQLite (/data)
    │                                      ├─ 프로세스당 M4A worker 1개
    │                                      └─ NAVER Local Search
    ├─ NAVER Maps JS SDK ──────────────► NAVER Maps
    └─ presigned PUT/GET ──────────────► Cloudflare R2
                                           ├─ 원본 video
                                           └─ 추출 M4A
```

R2에 저장하는 영상 산출물은 **원본 video와 M4A 하나뿐**이다. 720p/480p 파생 영상은 더 이상 생성·보관하지 않는다. SQLite에는 곡·합주·미디어 메타데이터와 처리 상태를, R2에는 미디어 바이트를 둔다.

## 미디어 처리 경계

영상은 `queued → processing → completed | failed` 상태를 따른다. 영상 이외 미디어는 `not_required`다. 행에는 시작/완료 시각, heartbeat, 시도 횟수, 안전한 오류 메시지를 남긴다. worker는 프로세스마다 하나이며 DB의 조건부 claim으로 여러 프로세스가 같은 작업을 집지 않게 한다. 시작 시 stale heartbeat의 `processing` 행은 `queued`로 되돌린다.

원인은 이전 구현이 HTTP 요청 안에서 background thread를 시작한 데 있다. Fly의 autostop 및 최소 머신 0 구성과 합쳐져 작업이 중단될 수 있었고, 상태와 R2 파일이 불일치했으며 과거 720p/480p migration도 남았다. 현재는 별도 폴링 worker와 DB queue를 쓰지만, FFmpeg는 여전히 API 프로세스와 같은 머신에서 실행한다.

## 대용량 업로드 경계

영상 최대 크기는 1 GiB다. 100 MiB 이상 영상은 16 MiB part의 R2 multipart를 사용한다. 계약은 `initiate`, `parts`, `complete`, `abort` 네 API이며, 프런트엔드는 최대 3개 part를 병렬 전송하고 각 PUT을 최대 3회 재시도한다. part별 진행률을 합산해 전체 진행률을 표시하며 취소 시 XHR abort와 multipart abort를 시도한다.

이전 iPhone의 200 MiB 초과 성공은 `RehearsalDetail` 경로가 Flask의 200 MiB body guard를 거치지 않고 R2 single PUT을 직접 수행했고 파일이 R2 single PUT 한계 안에 있었기 때문이다. 이는 서버 업로드 한도가 R2 직접 업로드 한도가 아님을 뜻한다.

## UI 처리 계약

처리 상태 API의 `status`는 일반 media 응답의 `transcoding_status`로 정규화한다. 새 업로드와 이미 펼쳐진 미디어 모두 `queued`/`processing`을 감시하며, terminal 상태·unmount·연속 네트워크 실패 5회에서 감시를 중단한다. 라디오 모드는 `completed`이고 `audio_url`이 있을 때만 활성화하며, video/audio 전환 때 현재 재생 상태를 보존하려고 시도한다.

## 당시 사전 감사

이 문서 작성 전 관측된 상태는 video 55개, `completed` 0개, `pending` 28개, `processing` 23개, `failed` 4개, M4A 0개였다. 200 MiB 초과 영상 44개는 모두 R2 원본 존재와 DB 파일 크기 일치를 확인했다. 이는 당시 관측값이며, 운영 명령을 실행한 결과가 아니다.

## 배포 상태

FE=Cloudflare, BE=Fly.io, object storage=Cloudflare R2가 현재 운영 기준이다. k3s는 예시 레지스트리·도메인과 `latest` 태그를 포함한 초안이다. 백엔드를 Cloudflare 또는 더 성능 좋은 호스팅으로 옮기는 일은 검토 단계이며, 그 전에 SQLite 단일 볼륨·FFmpeg 작업·백업/복구를 함께 평가한다. 상세 실행 절차는 [배포 운영 가이드](deployment.md)에 있다.
