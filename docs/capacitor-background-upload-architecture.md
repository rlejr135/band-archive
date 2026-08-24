# Capacitor 기반 Android/iOS 백그라운드 업로드 아키텍처

상태: **1단계 아키텍처 확정 — 아직 Capacitor, 네이티브 plugin, API 변경은 구현·배포되지 않음.**

## 결정 요약

- 기존 React/Vite 화면·도메인 UI·업로드 큐를 공통으로 유지한다.
- 웹에서는 현재 `mediaUploadManager`의 single/multipart R2 직접 업로드를 그대로 사용한다.
- Capacitor 앱에서는 같은 JS 큐가 `BackgroundUpload` plugin을 호출하고, Android/iOS가 파일 기반 multipart PUT을 수행한다.
- 서버는 R2 credential을 앱에 주지 않는다. session/part presign/complete/abort라는 서버 소유 경계를 유지하되, 앱 재시작 복구에 필요한 session 조회와 part URL 재발급 계약을 추가한다.
- 네이티브 앱 도입 전에 upload API 전반에 사용자 인증·소유권 검증을 추가한다. 현재 API에는 공통 인증·인가가 없으므로, 현 상태로 장기 presigned upload를 mobile background 기능에 확장하지 않는다.

## 현재 코드 기준선

`frontend`은 Capacitor 패키지·Android·iOS 프로젝트가 없는 React 19/Vite SPA다. video는 1 GiB 상한이며 100 MiB 이상에서 현행 R2 multipart로 전환한다. 서버는 16 MiB part와 최대 10,000 part를 발급하고, `POST /uploads/multipart/initiate`, `POST /uploads/multipart/<session_id>/parts`, `complete`, `abort`를 제공한다. `media`(song/rehearsal) 및 `personal_log`(member) 대상은 같은 multipart endpoint를 공유한다.

현재 session은 24시간 후 만료되며, 서버는 **part number가 한 번 발급된 뒤 다시 발급되는 것을 409으로 거부**한다. 완료 ETag는 browser가 메모리에서 들고 complete에 보낸다. 따라서 지금의 웹 계약은 탭 수명 안의 재시도에는 맞지만, 앱 재시작·presigned URL 만료 뒤 재개에는 충분하지 않다.

비디오 처리 완료 후 R2에는 원본 video와 M4A만 남는다. 이 계획은 처리 queue와 라디오 재생 계약을 변경하지 않는다.

## 공통 UI와 전송 adapter

Vite/React 컴포넌트(`FileUpload`, 합주 업로드, 개인 기록 업로드)는 파일 선택, 대상 선택, 진행률, 취소, `queued → processing → completed/failed` 표시를 유지한다. transport 선택은 UI 컴포넌트가 아닌 `uploadMediaFile` 계층의 adapter에서 한다.

| 실행 환경 | 전송 adapter | 책임 |
| --- | --- | --- |
| 웹 | 기존 XHR + R2 presigned single/multipart | 현재 aggregate progress, part PUT 3회 재시도, abort |
| Capacitor Android/iOS | `BackgroundUpload` Capacitor plugin | OS가 관리하는 파일 upload, persistent 상태, notification/OS callback을 JS 이벤트로 전달 |

웹 fallback은 계속 가능해야 한다. native plugin이 없거나 지원하지 않는 파일/플랫폼이면 UI는 명확한 안내 후 현행 foreground 웹 upload로 전환하며, background 완료를 가장해서는 안 된다.

## 플랫폼별 전송 실행기

### Android

사용자가 화면에서 시작한 장시간 video upload에는 Android 14(API 34)+의 **User-Initiated Data Transfer (UIDT) Job**을 기본으로 선택한다. 이 API는 사용자 시작·즉시 실행·사용자에게 보이는 진행률 전송에 맞고 notification을 요구한다. plugin은 `RUN_USER_INITIATED_JOBS`, `JobService`, notification channel을 선언하고, network/저장공간 제약, 예상 송수신 바이트, notification의 진행률·취소 action을 제공한다.

Android 13 이하 또는 UIDT scheduling 불가 상황에는 WorkManager의 foreground-service worker를 fallback으로 사용한다. WorkManager는 짧고 중단 가능한 작업에는 적합하지만 장시간 사용자 시작 전송의 주 실행기로 가정하지 않는다. 일반 foreground service를 1순위로 선택하지 않는 이유는 최근 Android의 `dataSync` foreground-service 시간/시작 제한 때문이다. plugin은 UIDT가 시스템/사용자에 의해 중단되어 callback이 오지 않는 경우에도 디스크 상태만으로 재개할 수 있어야 한다. [Android UIDT 가이드](https://developer.android.com/develop/background-work/background-tasks/uidt?hl=en), [Android data-transfer 선택 가이드](https://developer.android.com/develop/background-work/background-tasks/data-transfer-options?hl=en)

### iOS

iOS는 고유 identifier의 `URLSessionConfiguration.background`와 `URLSessionUploadTask`를 사용한다. 각 R2 part PUT은 `uploadTask(with:fromFile:)`로 만들며, in-memory data/stream task에 의존하지 않는다. 원본은 앱 sandbox에서 background session이 읽을 수 있는 안정적인 로컬 file URL이어야 한다. Photos picker의 보안 범위/임시 URL은 앱 재시작 뒤 유효하지 않을 수 있으므로, enqueue 전에 앱 관리 디렉터리로 복사하거나 이동하고 파일 무결성·여유 디스크를 확인한다.

iOS가 앱을 재기동해 background session event를 전달할 수 있으므로 AppDelegate/Scene lifecycle에서 completion handler를 보관하고 plugin이 session delegate callback을 복구해야 한다. JS 런타임이 없는 동안의 progress/결과는 native DB에 먼저 기록하고 다음 bridge 시작 시 전달한다. [Apple URLSessionUploadTask](https://developer.apple.com/documentation/foundation/urlsessionuploadtask), [Apple background session 구성](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/background%28withidentifier%3A%29)

## 서버 API 보강 계약

기존 initiate/complete/abort의 의미는 유지한다. native 재개를 위해 다음을 **새 버전 계약으로 추가**한다. 현재 코드에는 없다.

| API | 추가/변경 | 목적 |
| --- | --- | --- |
| `GET /uploads/multipart/<session_id>` | 추가 | 인증된 소유자에게 status, target kind/id, object key 식별자, declared bytes, part size, session 만료, 완료 media/log ID, 서버가 아는 part 상태를 반환 |
| `POST /uploads/multipart/<session_id>/parts` | 변경 | 같은 part number 요청을 409으로 끝내지 않고, 아직 완료로 확인되지 않은 part에는 새 presigned URL을 idempotent하게 반환/갱신 |
| `POST /uploads/multipart/<session_id>/parts/ack` | 추가 권장 | native가 PUT 성공 뒤 part number, ETag, bytes, checksum(선택)을 서버에 저장; 재시작 뒤 complete payload를 재구성 |
| `POST /uploads/multipart/<session_id>/complete` | 변경 | 저장된 ETag와 전달 ETag를 대조하고, 이미 complete면 같은 결과를 멱등 반환 |
| `POST /uploads/multipart/<session_id>/abort` | 유지 | 앱 취소 및 만료 정리에 멱등 사용 |

session에는 uploader/owner, 대상 종류와 ID, object key, R2 upload ID, declared bytes, part size, 만료, 상태, part별 ETag/bytes/checksum을 영속한다. `initiated → uploading → completing → completed | aborted | expired | failed`를 명시한다. session 조회·part 재발급·complete·abort는 같은 인증 주체와 같은 대상 권한을 매 요청 검증한다.

presigned URL은 짧은 TTL이며 native DB에 credential처럼 장기 보관하지 않는다. 만료/403은 plugin이 해당 part의 새 URL을 서버에 요청하는 재시도 가능한 상태다. 서버 session 만료는 URL 만료와 분리해 관리하며, session이 만료되었거나 R2 multipart가 사라졌으면 새 session을 만들고 기존 것을 abort/expired로 기록한다.

## JS ↔ native plugin 계약

plugin 이름은 `BackgroundUpload`로 가정한다. JS는 browser `File`을 native에 넘기지 않고, Capacitor가 접근 가능한 로컬 URI와 immutable upload descriptor를 넘긴다.

```ts
type UploadDescriptor = {
  localUri: string;                 // plugin이 durable copy를 확보할 원본 URI
  sessionId: string;                // initiate 뒤 받은 서버 session
  apiBaseUrl: string;               // VITE_API_URL
  target: { kind: 'media' | 'personal_log'; id: number };
  declaredBytes: number;
  contentType: string;
  partSize: number;
  uploadId: string;                 // native-local UUID, sessionId와 별개
};

type UploadState =
  | 'preparing' | 'queued' | 'uploading' | 'paused'
  | 'retry_wait' | 'completing' | 'processing'
  | 'completed' | 'failed' | 'cancelled';

interface BackgroundUploadPlugin {
  enqueue(descriptor: UploadDescriptor): Promise<{ uploadId: string }>;
  resume({ uploadId }: { uploadId: string }): Promise<void>;
  cancel({ uploadId }: { uploadId: string }): Promise<void>;
  listPending(): Promise<Array<PersistedUpload>>;
  addListener('progress' | 'state' | 'completed' | 'failed', callback): Promise<ListenerHandle>;
}
```

`progress`는 uploaded bytes, total bytes, active part 수를 포함한다. `completed`는 R2 part 완료가 아니라 server `complete`가 반환한 media/personal_log와 후속 processing 상태를 포함해야 한다. JS는 이벤트를 화면 큐에 반영하고 기존 audio processing polling을 시작한다.

## 영속·재시도·취소·재시작 복구

native DB(예: Android Room / iOS SQLite)는 upload ID, durable file path, 파일 크기·수정시각·hash, session ID, part size, 완료 ETag 목록, 각 part retry/오류, 마지막 progress, 상태, 생성/갱신 시각을 저장한다. 파일이 삭제·변경되었거나 hash가 다르면 재개하지 않고 사용자에게 새 upload를 요구한다.

1. JS가 target 검증 후 서버 initiate를 호출하고 native에 enqueue한다.
2. native는 durable copy 확보 → OS job/task 등록 → part URL 획득 → file slice PUT → ETag ack를 반복한다.
3. 재시작/OS 중단 뒤 plugin은 native DB와 `GET session`을 대조한다. ack된 part는 건너뛰고, 미완료 part는 새 URL을 받아 재개한다.
4. 모든 part가 확인되면 complete를 한 번 호출한다. complete 충돌/timeout은 session 조회 후 결과를 확정한다.
5. 취소는 먼저 OS task/job을 취소·상태 저장하고 서버 abort를 재시도한다. abort가 네트워크 문제로 지연되면 `cancelled_pending_abort`로 남겨 다음 기회에 처리한다.

자동 재시도는 network/5xx/URL 만료에 한하고 exponential backoff·최대 횟수·사용자 재개 action을 둔다. 4xx 권한 오류, 파일 불일치, session 만료는 자동 무한 재시도를 하지 않는다.

## 보안과 운영 경계

- R2 access key/secret, Fly secret, presigned URL 원문을 JS log·notification·crash report에 기록하지 않는다.
- 인증 access token은 OS secure storage(Keychain/Keystore)에만 저장하고, native HTTP client는 authorization header를 API 요청에만 붙인다. R2 PUT에는 presigned URL만 사용한다.
- 현재 서버에 공통 인증이 없으므로, plugin 개발보다 먼저 upload session의 인증·대상 소유권·rate/size 정책을 도입한다.
- CORS는 웹 R2 PUT에는 계속 필요하지만 native URLSession/OkHttp 직접 PUT의 대체 보안 기제는 아니다. native는 TLS, host validation, URL 만료·최소권한 session으로 보호한다.
- 서버/DB와 R2는 원자적이지 않다. 만료·abort·complete 실패의 session과 R2 abandoned multipart를 주기적으로 점검하며, 완료 원본/M4A는 정리 작업이 확인 없이 삭제하지 않는다.

## 검증 환경과 한계

| Windows에서 가능한 검증 | macOS/Xcode 또는 실제 기기 필요 |
| --- | --- |
| React adapter unit test, API contract/pytest, multipart 재개 시나리오 mock, Android Gradle build·emulator/ADB 일부, R2 CORS/API integration test | iOS Capacitor build/signing, background URLSession lifecycle, 앱 강제 종료·재기동 callback, Photos file copy, 실제 cellular/Wi-Fi 전환, iPhone 열/저장공간 행동 |

Android도 실제 device에서 notification, Task Manager 중단, battery/thermal, OEM 절전, background 재개를 검증해야 한다. Emulator만으로 장시간 이동통신 조건을 승인하지 않는다.

## 단계별 구현·커밋·테스트 계획

1. **BE session durability**: 인증/소유권 경계, session 조회·part ack·part URL 재발급, schema migration, TTL/abort cleanup을 추가한다. pytest로 media/personal_log 분리, 재개, ETag 중복, 만료, 권한 거부, complete 멱등성을 검증한다.
2. **FE transport abstraction**: 기존 web adapter의 동작을 보존한 채 plugin adapter interface와 feature detection을 추가한다. Vitest 또는 현재 도입할 test runner로 web fallback·이벤트 state mapping을 테스트한다.
3. **Capacitor scaffold**: 공통 Vite bundle을 감싸고 Android/iOS 프로젝트를 추가한다. 이 단계는 upload 기능을 아직 활성화하지 않으며 Android/iOS build CI를 만든다.
4. **Android plugin**: UIDT API 34+와 WorkManager foreground fallback, notification progress/cancel, native DB 복구를 구현한다. 실제 Android API 34+와 하위 버전 기기에서 중단/재시작·network 변화 테스트를 한다.
5. **iOS plugin**: background URLSession file upload, durable copy, native DB·delegate wake-up·completion handler를 구현한다. Xcode와 실제 iPhone에서 종료/재기동·network 변화·잠금 화면 테스트를 한다.
6. **통합 rollout**: feature flag로 internal 사용자만 활성화한다. 100 MiB 이상 및 1 GiB 근접 video, media/personal_log, cancel/retry, URL 만료, R2 ETag, server M4A queue를 관찰한 뒤 확대한다.

각 단계는 독립 커밋과 해당 단계의 테스트를 포함한다. API 계약·schema 변경이 배포되기 전에는 native 앱을 public rollout하지 않는다.
