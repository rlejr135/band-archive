# 프런트엔드

`band-archive/frontend`은 Cloudflare에서 제공하는 React/Vite SPA다. `VITE_API_URL`은 API 기본 주소, `VITE_NAVER_MAP_CLIENT_ID`는 브라우저 지도 SDK 설정이다. 두 값은 빌드 결과에 포함될 수 있으므로 비밀값을 넣지 않는다.

## 미디어 업로드

`src/services/mediaUploadManager.js`는 video가 100 MiB 이상이면 multipart, 미만이면 existing single presigned PUT을 선택한다. multipart는 16 MiB part, 동시성 3, part별 PUT 최대 3회 재시도, aggregate progress를 사용한다. 각 part 완료 후 브라우저가 응답 `ETag`를 읽어 complete API에 전달하므로 R2 CORS가 `ETag`를 expose해야 한다.

취소·화면 unmount는 진행 중 XHR을 중단하고 session이 있으면 abort API를 best-effort로 호출한다. 1 GiB 초과 video는 업로드 전 차단한다.

## 처리 상태와 재생

`useMediaUpload`는 processing endpoint의 `status`를 `transcoding_status`로 정규화한다. 새 업로드뿐 아니라 펼쳐진 기존 media도 상태를 poll한다. terminal, unmount, 연속 네트워크 실패 5회에서 polling을 종료한다.

`MediaPlayer`의 라디오(데이터 절약) 모드는 video가 `completed`이고 `audio_url`이 있을 때만 활성화된다. video와 audio를 전환할 때 재생 시간·재생 여부를 보존하려고 시도한다. 720p/480p 선택 UI나 해당 파일을 전제로 한 구현은 현재 계약에 포함하지 않는다.

## 배포 smoke test

배포 후 iPhone, Android, desktop에서 다음을 확인한다: 100 MiB 미만 single PUT, 100 MiB 이상 multipart, 1 GiB 근접 video, 취소 후 재시도, multipart `ETag` CORS, 완료된 video의 라디오 모드. 실행 순서와 R2 CORS 예시는 [배포 운영 가이드](deployment.md)에 있다.
