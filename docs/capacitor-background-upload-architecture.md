# Capacitor 백그라운드 업로드: 구현·릴리스 기준

상태: **구현 완료, 프로덕션 공개 전 내부 검증 단계.** 이 문서는 현재 코드와 `c74307f` 이후 Android, `e583b72` 이후 iOS, backend multipart session 구현을 기준으로 한다. 아직 production 배포·실기기 승인과 feature rollout은 수행하지 않았다.

## 범위와 전송 경계

공통 React/Vite UI는 `BackgroundUpload` transport를 native platform에서만 선택하고, plugin이 없거나 browser에서는 기존 web transport를 사용한다.

| 환경 | 현재 동작 | 보장 범위 |
| --- | --- | --- |
| Web | R2 presigned single PUT 및 100 MiB 이상 foreground multipart | 탭/브라우저가 살아 있는 동안의 upload만 보장한다. browser background·재시작 복구를 보장하지 않는다. |
| Android | native picker → app-private durable copy → SQLite/Keystore → UIDT 또는 WorkManager → R2 multipart | 앱 재시작·OS 중단 뒤 저장된 task를 재개하도록 구현되어 있으나 실제 기기/OEM 정책 검증이 필요하다. |
| iOS | PHPicker current representation → Application Support durable copy → SQLite/Keychain → background URLSession part PUT | JS 없이 delegate 결과를 저장·재전달하도록 구현되어 있으나 macOS/Xcode 및 TestFlight 검증이 필요하다. iOS force-quit은 background transfer를 취소한다. |

모든 video는 최대 1 GiB다. backend/R2 산출물은 원본 video와 추출 M4A뿐이다. native UI도 기존 `queued → processing → completed/failed`와 media/personal-log 대상을 유지한다.

## 서버와 R2 계약

backend는 `POST /uploads/multipart/initiate`, `GET /uploads/multipart/<session_id>`, part URL 발급, `POST .../parts/<part>/ack`, `complete`, `abort`를 제공한다. session은 one-time capability token의 **hash**만 저장하고, raw token은 initiate 응답 한 번에서만 전달된다. client는 이후 `X-Upload-Capability` header를 사용한다.

- part size는 16 MiB, 최대 10,000 part, video 상한은 1 GiB다.
- native와 web은 session의 acknowledged parts를 조회해 이미 ACK된 part를 건너뛴다. URL 만료/403은 같은 part URL을 다시 발급받아 재시도한다.
- complete/abort/ACK는 capability token으로 보호되고 complete는 멱등 결과를 반환한다. backend startup migration은 session capability hash, part ETag/bytes/checksum/ack timestamp 컬럼을 보강한다.
- browser R2 PUT에는 CORS가 필요하다. production origin에서 `PUT`, `HEAD`(필요 시 `GET`)와 `Content-Type`을 허용하고 `ETag`를 expose 해야 한다. native URLSession/OkHttp PUT에는 browser CORS가 적용되지 않는다.

현재 인증·소유권 경계는 account session이 아니라 upload capability token이다. token 유출 시 해당 session의 유효 기간 동안 호출될 수 있으므로, 장기적으로 사용자 인증·대상 권한 검증을 capability 검증과 함께 추가해야 한다. token, presigned URL, R2 credential은 로그·notification·DB에 원문 저장하지 않는다.

R2 multipart는 DB transaction과 원자적이지 않다. 만료/취소/실패 session과 R2 abandoned multipart upload lifecycle을 Cloudflare 계정의 현재 console/API 정책으로 주기적으로 점검한다. 완료 원본/M4A는 이 정리로 삭제하지 않는다. legacy single/multipart upload는 durable native state가 없으므로 재개 대상이 아니다.

## Android 구현

Android의 `BackgroundUpload` Capacitor plugin은 SAF picker 결과를 앱 upload directory에 atomic copy하고 SHA-256/크기/경로를 검증한다. token은 Keystore 암호문, task·part ACK·lease·persistent positive `work_id`는 SQLite에 저장한다.

- API 34 이상은 사용자 시작 전송에 맞는 **User-Initiated Data Transfer Job**을 우선 사용한다. notification progress/cancel, `RUN_USER_INITIATED_JOBS`, `FOREGROUND_SERVICE_DATA_SYNC`를 선언한다.
- UIDT가 불가능한 API/상황에서는 WorkManager foreground `dataSync` worker로 fallback한다.
- engine write는 lease owner CAS이며 cancel은 durable state를 먼저 저장한다. retry는 URL 재발급/ACK/complete를 재조정하고, cancelled/terminal task는 재개하지 않는다.
- notification 권한 거부는 upload 자체를 중단시키지 않지만 background 제한 안내를 보여야 한다.

프로젝트는 `minSdk 24`, `compileSdk/targetSdk 36`, Java 21을 사용한다. Android SDK platform `android-36`, build-tools 36.x, platform-tools와 JDK 21이 필요하다. `frontend/android/local.properties`의 `sdk.dir`은 로컬 ignore 파일이며 커밋하지 않는다.

## iOS 구현

iOS plugin은 `PHPicker`의 `.videos`, `.current` representation을 사용한다. `NSItemProvider.loadFileRepresentation` callback이 끝나기 전에 허용 확장자(`mp4`, `webm`, `mov`, `avi`, `mkv`)와 1 GiB/여유 공간을 검사하고 Application Support `background_uploads`로 동기 durable copy한다.

- capability는 Keychain (`AfterFirstUnlockThisDeviceOnly`), task/ACK/attempt/lease/work ID는 SQLite WAL에 보관한다.
- 고정 identifier background `URLSession`의 file-based upload task로 R2 part PUT을 수행한다. source/part file은 canonical app-private child와 regular file인지 확인한다.
- session task description, SQLite attempt mapping, single-flight coordinator, lease-owner CAS가 restart·duplicate part·stale callback을 막는다. pending ACK/complete intent는 foreground pump에서도 재시도한다.
- background finish event와 AppDelegate completion handler는 FIFO credit/handler로 1:1 처리하고 bounded stale handler 정리를 둔다. protected-data 시점의 engine 생성 실패는 provider가 재시도하고 plugin observer가 bridge event 및 retained snapshot을 재결합한다.
- R2 complete 뒤 processing으로 전환하면 durable source를 제거한다. cancelled는 즉시, retryable failure는 7일 보존한다. processing row는 JS polling/ack까지 유지한다.

iOS force-quit은 system background URLSession transfer도 취소할 수 있다. 해당 제한은 UI 안내와 운영 테스트에서 명확히 취급한다. Windows에서는 Swift/Xcode compile을 실행할 수 없으므로 macOS `xcodebuild test`와 signing/TestFlight 검증이 release gate다. 상세 실기기 체크리스트는 [iOS background QA](../band-archive/frontend/ios/IOS_BACKGROUND_UPLOAD_QA.md)를 따른다.

## 빌드·설정·비밀값

frontend 작업 디렉터리는 `E:\Anything\band-archive\frontend`이다.

```powershell
npm ci
npm test
npm run build
npx cap sync
cd android
.\gradlew.bat test --no-daemon
.\gradlew.bat assembleDebug --no-daemon
```

`VITE_API_URL`은 Vite build-time 공개 API base URL이다. production value는 HTTPS backend origin이어야 하며 mobile bundle을 다시 build해야 변경된다. `CAPACITOR_APP_ID`/`CAPACITOR_APP_NAME`은 CI에서 `capacitor.config.ts` 기본값을 override할 수 있다. package/app ID 변경은 Android application ID, iOS bundle ID, signing/provisioning/profile과 함께 계획적으로 수행한다.

R2/Fly/Cloudflare API key, backend secret, Android keystore, iOS signing certificate·provisioning profile, Keychain capability token은 `.env`, CI secret store, OS secure storage에만 둔다. `local.properties`, signing file, private certificate와 credential은 commit/log/audit output에 넣지 않는다.

## 릴리스 순서와 rollback

native app, frontend, backend는 독립적으로 배포할 수 있지만 **backend session schema/API를 먼저 또는 같은 release window에** 호환 배포해야 한다. 구버전 web client가 multipart를 계속 완료할 수 있어야 하며 backend rollback 시 새 session/part columns와 R2 원본/M4A를 삭제하지 않는다.

권장 rollout은 internal feature flag 또는 internal build로 native transport를 제한한 뒤, media/personal_log, 100 MiB 경계, 1 GiB 근접, URL expiry, retry/cancel, processing/M4A ready를 관찰하는 방식이다. feature flag off 또는 frontend rollback은 web foreground fallback으로 되돌리되 이미 생성된 native task/session은 backend 호환 기간 동안 유지한다. rollback은 새 enqueue 중지 → active task 상태 보존/관찰 → frontend → backend 순으로 판단하며 R2 원본/M4A를 삭제하지 않는다.

## 검증 및 남은 제한

CI/로컬 기본 검증은 backend pytest, frontend Node tests/build/Capacitor sync, Android Gradle unit test·debug build다. Windows에서 iOS source/PBX/plist 정적 검사만 가능하며 `xcodebuild`, PHPicker iCloud, background URLSession wake, lock/unlock, force-quit, cellular 전환은 macOS/실기기에서 확인해야 한다.

2026-08-25에 실행한 `npm audit --omit=dev`는 production dependency 경로의 `react-router`/`react-router-dom`에서 **high 2건**을 보고했다. 자동 `npm audit fix`는 실행하지 않았으며, dependency update와 회귀 검증을 별도 변경으로 처리해야 한다.
