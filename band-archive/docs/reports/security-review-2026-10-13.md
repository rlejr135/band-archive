# 보안 재점검 보고서

- 점검일: 2026-10-13
- 점검 대상: `E:/Anything/band-archive`, HEAD `c1c6ed430645dd297a8294a6193518cfd53c1373`
- 점검 범위: 소스, 배포 설정 샘플, 의존성 선언 및 lockfile의 읽기 전용 분석
- 제외: `k3s/secrets.yaml`, `.env*`, 캐시·임시 산출물, 운영 PII/credential, 활성 공격·스캔, 코드/운영 설정 변경
- 배포 맥락: 루트 담당자가 확인한 운영 값은 `LEGACY_V1_WRITES_ENABLED=false`, BE v53 및 FE HEAD 배포 완료다. 이 보고서는 운영 환경을 직접 조회하지 않았다.
- 검증 제한: 테스트나 동적 검증은 실행하지 않았다. 아래 재현/검증 항목은 후속 담당자 권고다.

## 요약

인증·테넌시 경계에서 즉시 악용 가능한 우회는 소스 검토에서 확인되지 않았다. 관리자 API는 매 요청 Bearer 인증과 서버 DB의 관리자 플래그를 확인한다. v2 쓰기는 URL의 band에 속한 활성 멤버십을 다시 확인하며, 공개 읽기는 tenant 및 visibility 조건을 쿼리에 적용한다. 업로드 capability는 랜덤 토큰을 해시해 보관하고 R2 객체 크기·밴드 쿼터를 검증한다.

확인된 우선 개선점은 (1) GitHub Reviewed Advisory의 영향 버전 범위에 걸리는 `Flask-Cors==4.0.0`과 후속 보안 수정이 포함된 `PyJWT==2.10.1` pin, (2) 네트워크에 공개되고 advisory별 추가 조건을 충족할 때 파일 접근 위험이 있는 `Vite 7.3.1`, (3) 무제한 공개 장소검색 프록시와 오래된 레거시 전체 목록 GET의 남용 가능성이다. PyJWT의 알려진 취약 함수는 이 코드에서 사용한다는 증거가 없어 도달 가능성은 별도로 구분했다.

## 발견 사항

### P2 — Flask-CORS 4.0.0이 공개된 취약 버전 범위에 있음

- 판정: GitHub의 Reviewed Advisory Database가 선언한 버전 범위에는 4.0.0이 포함된다. exact 4.0.0에서의 재현/실효 영향은 동적으로 확인하지 않았으므로 이 앱의 실제 취약성을 확정하지 않는다.
- 근거: `backend/requirements.txt:4`가 `Flask-Cors==4.0.0`을 고정한다. GitHub Reviewed Advisory [GHSA-hxwh-jpp2-84pm](https://github.com/advisories/GHSA-hxwh-jpp2-84pm)은 `<4.0.2`를 취약 범위로 표시하지만 설명은 4.0.1 동작을 구체적으로 든다. 패키지 maintainer 저장소의 [CVE-2024-6221 대응 PR #363](https://github.com/corydolphin/flask-cors/pull/363)은 private-network 응답 헤더를 설정하는 호환 수정이 기본 동작을 바꾸지 않는다고 설명한다. maintainer의 [PR #368](https://github.com/corydolphin/flask-cors/pull/368)은 기본값을 바꾸는 breaking release 논의를 보여준다. 별도의 GitHub Reviewed Advisory [GHSA-8vgw-p6qm-5gr7](https://github.com/advisories/GHSA-8vgw-p6qm-5gr7)은 `<6.0.0`을 영향 범위로 기재하지만 서술 예시는 5.0.1을 특정한다. 따라서 두 범위 모두 package-level signal이며, exact 4.0.0의 구체 동작을 이 감사에서 확인한 것처럼 읽어서는 안 된다.
- 앱 도달성: `backend/app.py:385-393`은 production에서 `CORS_ALLOWED_ORIGINS` allowlist를 사용하고, debug 모드에서만 전체 origin을 허용한다. `backend/fly.toml:15-18`에는 현재 운영 frontend origin만 설정되어 있다. CORS origin allowlist와 Private Network Access 요청 허용은 구별되는 설정이다. Private Network Access 문제의 실제 영향은 브라우저·클라이언트와 도달 가능한 private-network 서비스 조건에 의존한다.
- 영향: 특정 브라우저/경로/정책 조건에서 CORS 응답 정책이 예상과 달라질 수 있고, 사설 네트워크 관련 브라우저 요청의 보호가 약해질 수 있다. 이 보고서는 해당 조건을 실제 배포 트래픽에서 검증하지 않았다.
- 권고: 먼저 현재 4.0.0 package artifact의 실제 기본 `Access-Control-Allow-Private-Network` 응답과 upstream release note를 확인한다. 취약 동작이 존재하면 최소한 GHSA-hxwh의 패치 경계인 4.0.2 이상으로 올리고, `+` path advisory도 같이 해결하려면 해당 GitHub Reviewed advisory의 경계인 6.0.0 이상을 적용한다. wildcard·credentials·정규화 경로의 제품 계약도 확인한다.
- 후속 검증: 기존 허용 origin은 성공, 임의 origin과 Private-Network preflight는 거부되는 회귀 테스트를 추가한다. `+` 포함 경로가 의도한 CORS 정책에 매칭되는지도 확인한다.

### P2 — 공개 장소 검색 경로가 인증·호출량 제한 없이 제3자 API를 중계함

- 판정: 호출량/비용 남용 표면은 코드에서 확인됨. Naver API credential이 배포되어 있고 쿼터가 과금·제한되는 경우 피해 규모는 조건부.
- 근거: `backend/routes/search.py:10-40`의 `GET /api/search-places`는 인증이나 rate limit 확인 없이 요청마다 Naver API를 호출한다. `query` 길이 제한·캐시·사용자별 호출량 제한이 없으며, 한 요청에 최대 5개 결과를 받는 것만 제한한다(`:12-36`). 비밀값은 환경 변수에서 읽어 외부 요청 헤더로 보낸다(`:16-24`).
- 영향: 봇이 endpoint를 반복 호출하면 Naver quota/비용을 소진하고 backend worker 및 outbound 연결을 점유할 수 있다. 환경 변수 미설정이면 요청마다 500을 반환하지만, credential 값은 조사하지 않았다.
- 권고: gateway 또는 애플리케이션에서 IP/계정별 rate limit, 짧은 query 길이·문자 제한, 결과 캐시, 동시 outbound 요청 제한을 적용한다. 사용자에게 필요한 공개 동작을 유지하면서 proxy 쿼터를 별도로 설정한다.
- 후속 검증: credential을 mock 처리한 자동 테스트로 시간 구간당 외부 호출 한도, 동일 쿼리 캐시, 빈/과도한 query 거부, upstream timeout을 검증한다. 운영에서는 제공자 quota/알림이 설정되었는지 운영 담당자가 확인해야 한다.

### P2 — 레거시 쓰기 비활성화는 운영에서 확인됐지만 코드 기본값은 허용

- 판정: 현재 운영 취약점으로 보고하지 않음. 배포 설정 누락 시 재노출되는 설정 이탈 위험은 확인됨.
- 근거: `backend/config.py:106-108`은 `LEGACY_V1_WRITES_ENABLED` 기본값을 true로 둔다. `backend/app.py:395-406`은 이 값이 false일 때만 v1의 비-GET 요청을 410으로 차단한다. 반면 `/uploads/presign` 등 과거 쓰기 라우트는 로그인/멤버십 검사가 없다(`backend/routes/uploads.py:559-590`, `:593-652`). 루트 담당자가 운영값 false를 확인했으므로 이 라우트가 현재 운영에서 공개 쓰기 상태라고 결론내리지 않는다.
- 영향: 신규/복구/스테이징 배포가 설정을 누락하면 무인증 v1 write가 다시 허용될 수 있다. 레거시 업로드는 v2 쿼터·사용자 인증 경계를 통하지 않는다.
- 권고: 운영 기본값을 false로 바꾸거나 production에서 명시적 false가 아니면 시작을 거부한다. 배포 설정 검증 및 health/readiness 확인에 이 값을 포함한다.
- 후속 검증: 설정이 없는 production config가 fail-closed인지, false일 때 v1 모든 변경 경로가 handler 진입 전 410인지 확인한다. 현재 운영값은 별도 배포 체크에서 계속 확인한다.

### P2 — Vite 7.3.1은 개발 서버 취약점 공지 범위에 있음 (노출 조건부)

- 판정: lockfile 버전은 여러 advisory의 영향 범위 안이다. 모든 Vite 7.3.1 실행이 공격 가능하다는 뜻은 아니다. 각 GHSA는 개발 서버 노출 및 해당별 추가 조건을 요구한다.
- 근거: `frontend/package-lock.json:4999-5001`은 Vite 7.3.1이다. maintainer 원문 [GHSA-p9ff-h696-f583](https://github.com/vitejs/vite/security/advisories/GHSA-p9ff-h696-f583)은 네트워크 노출(`--host`/`server.host`)과 WebSocket 활성 상태에서의 파일 읽기를 설명하며, 영향 범위에 7.3.1을 포함한다. [GHSA-v2wj-q39q-566r](https://github.com/vitejs/vite/security/advisories/GHSA-v2wj-q39q-566r)는 네트워크 노출 외에도 민감 파일이 `server.fs.allow` 경로 안에 있고 `server.fs.deny`에 의해 차단되어 있는 조건을 요구한다. 2026-10-06 maintainer 원문 [GHSA-rq7h-c2jc-7f22](https://github.com/vitejs/vite/security/advisories/GHSA-rq7h-c2jc-7f22)은 네트워크 노출 외에 **non-Windows host**, root-relative import가 실제 host absolute path와 일치하는 경로 조건, 그리고 `server.fs.deny` 미차단 조건을 요구한다. 본 코드/작업공간은 Windows다. 이 세 번째 조건은 이 작업공간에서 성립하지 않지만 Linux 개발/CI host는 별도다. 같은 날짜의 [GHSA-9jrq-w75r-8gcw](https://github.com/vitejs/vite/security/advisories/GHSA-9jrq-w75r-8gcw)는 middleware mode 또는 custom `transformIndexHtml` URL 전달과 inline module script 등 구체 조건을 요구한다.
- 앱 도달성: `frontend/package.json:9-14`의 개발 명령은 일반 `vite`이고 `frontend/vite.config.js:5-8`에는 `server.host`, `server.ws`, custom middleware 설정이 없다. 따라서 기본 명령이 네트워크에 공개된다는 증거가 없다. 개발자가 `--host`로 노출하면 advisory별 조건을 다시 점검해야 한다. 운영 프론트는 정적 build 산출물로 제공된다(`frontend/package.json:11`, `frontend/nginx.conf:5-9`).
- 영향: 요구 조건을 충족한 취약 개발 서버/CI에서 host 파일 정보 유출이 가능할 수 있다. 현재 Windows 작업공간은 GHSA-rq7h의 non-Windows 조건을 충족하지 않는다. Linux 개발/CI 실행환경은 별도로 확인하지 않았다. 정적 production build 자체가 Vite dev server를 실행한다는 증거는 없다.
- 권고: Vite를 현재 유지 계열의 수정 버전으로 올린다. Vite 7 계열에 남는 최신 수정 경계로 확인한 7.3.7 이상을 최소로 검토하거나 현재 안정 계열로 올리고 lockfile을 갱신한다. dev server는 필요한 경우에만 제한된 네트워크에서 실행한다.
- 후속 검증: dependency advisory scan 및 build 확인, 기본 dev host가 loopback인지 확인한다. 노출 환경이 실제 존재하면 안전한 내부 테스트 환경에서 `server.fs` deny/allow와 WebSocket 접근 경계를 검증한다. 본 감사에서는 dev server를 실행하거나 스캔하지 않았다.

### P3 — PyJWT 2.10.1 pin 갱신 권고; 알려진 advisory별 패치 버전과 코드 도달성을 구분

- 판정: 고정 2.10.1이 오래된 것은 확인됨. 아래 advisory는 각각 영향 함수와 패치 릴리스가 다르다. 이 코드에서 인증 bypass가 가능하다는 근거는 찾지 못했다.
- 근거: `backend/requirements.txt:12`가 PyJWT 2.10.1을 고정한다. [GHSA-jq35-7prp-9v3f](https://github.com/advisories/GHSA-jq35-7prp-9v3f)의 영향은 `jwt.decode()`에 `PyJWK`/`PyJWKClient` 결과 객체를 전달하는 경우다. 공식 DB 표시는 `<2.13.0` / 2.13.0 수정으로 갱신돼 있다. 그러나 `backend/auth_service.py:173-184`는 `PyJWK` 객체가 아니라 `key.key`를 넘기고, 허용 알고리즘을 해당 JWKS key의 단일 `key.algorithm_name`으로 한정하므로 이 경로의 우회 조건과 다르다. 2026-09의 [GHSA-r6x4-923q-g947](https://github.com/advisories/GHSA-r6x4-923q-g947)은 2.13.0만 영향을 받고 2.14.0에서 수정된 BOM 우회다. 더 최근 [GHSA-42vr-xj54-vc7v](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-42vr-xj54-vc7v)은 `PyJWKClient.get_signing_key_from_jwt()` 또는 `verify_signature=False` pre-verification payload parse 경로를 다룬다. advisory의 현재 게시 내용은 2.15.0을 수정 릴리스로 표시한다. 현재 `auth_service.py`는 `PyJWKClient`를 쓰지 않고 `jwt.decode()`의 기본 signature verification을 끄지 않으므로 그 specific 경로가 도달한다는 근거가 없다. 다른 CVE도 각각 명시된 입력/key 구성 요건에 따라 별도 검토해야 한다.
- 영향: 현재 코드는 위의 specific algorithm confusion 및 pre-verification DoS 설명과 일치하지 않는다. 다만 2.10.1 pin은 후속 보안 수정을 놓치므로 업데이트 유지 비용이 커진다.
- 권고: 별도 호환성 검토 후 현재 지원 PyJWT 2.x인 2.15.0 이상으로 업데이트하고 lockfile/배포 산출물 버전을 확인한다. GHSA-jq35의 권고된 fix는 2.13.0, GHSA-r6x4의 fix는 2.14.0, GHSA-42vr의 fix는 2.15.0으로 서로 다르므로, 보고/추적시 하나의 advisory가 모두를 고쳤다고 쓰지 않는다. 업데이트만으로 현재 소스의 취약성이 입증되는 것은 아니다.
- 후속 검증: Supabase 발급 유효/만료/잘못된 issuer·audience·kid·algorithm JWT를 사용해 거부 동작을 검증하고, 허용 알고리즘이 trusted JWKS key에 따라 고정되어 있는지 확인한다. 테스트 토큰은 실제 운영 토큰을 사용하지 않는다.

### P3 — v2 업로드는 격리 경계가 강하지만 content type 값의 출처 검증은 별도 강화 여지

- 판정: 콘텐츠 타입 신뢰 경계는 확인됨. 이로 인한 앱 origin XSS나 브라우저 코드 실행은 확인되지 않았고, impact는 저장 객체 origin 및 응답 헤더에 따라 조건부.
- 근거: `backend/routes/uploads.py:343-355`, `:403-420`, `:462-475`는 일부 upload-init 경로에서 사용자 `content_type`을 허용하고 저장 객체의 PUT 서명에 사용한다. `backend/storage.py:54-63`은 이 값을 `ContentType`으로 서명한다. 완료 시 `backend/upload_record_service.py:7-19`는 확장자 분류와 파일 크기를 확인하지만 magic bytes/content-type 일치를 확인하지 않는다. 다운로드 URL 생성은 response content-disposition override 없이 객체 metadata를 사용한다(`backend/storage.py:46-52`). 허용 확장자는 `backend/validators.py:11-18`에서 제한되어 있고, 앱 코드에서 위험한 `dangerouslySetInnerHTML` 또는 일반 HTML 삽입 sink는 찾지 못했다.
- 영향: 확장자와 다른 MIME metadata를 가진 객체가 저장될 수 있다. 그 객체가 브라우저에서 inline 렌더링되는 외부 origin, 특히 app과 동일 site/origin을 공유하는 저장 도메인으로 제공되는 경우 악성 콘텐츠 전달 위험이 커질 수 있다. 현재 R2 custom domain/origin 배치는 이 검토에서 확인하지 않았다.
- 권고: 클라이언트 MIME을 확장자와 검증된 실제 형식에 따라 서버가 선택하고, 필요 형식에 magic-byte 검사를 적용한다. audio/video 재생은 기존 inline UX를 유지하되 안전한 allowlist MIME과 앱 origin에서 분리된 저장 origin을 사용한다. `Content-Disposition: attachment`는 위험 문서 및 일반 다운로드에 한정하고 `X-Content-Type-Options: nosniff` 등 브라우저 보호 헤더를 검토한다. 버킷/CORS 정책도 앱 origin과 분리한다.
- 후속 검증: 업로드 확장자와 실제 MIME 불일치 입력, HTML/SVG payload, audio/video inline 재생 회귀, 문서 attachment 동작, presigned GET 응답 헤더 및 실제 도메인 격리를 비운영 bucket에서 검증한다.

## 확인한 보호 조치와 미확인 영역

- Supabase JWT 검증: `backend/auth_service.py:173-184,239-270`에서 signature, issuer, audience, exp/sub/email을 확인한다. JWKS refresh는 cache, unknown `kid` negative cache 및 rotation cooldown을 갖는다(`:62-149`). 관리자 권한은 JWT metadata/email로 추정하지 않고 로컬 user 행의 active, verified, `is_platform_admin`을 확인한다(`backend/routes/bands.py:43-52`).
- 플랫폼 관리자 요청 처리: 관리자 목록 및 approve/reject route는 모두 `@require_auth`와 `require_platform_admin()`을 거친다(`backend/routes/bands.py:125-161`). 프론트는 `/api/v2/me` 서버 응답의 strict `true`로만 UI 권한 표시를 계산한다(`frontend/src/components/bands/usePlatformAdmin.js:5-27`). UI 보호가 API 인가를 대신하지 않으며 서버 측 확인이 존재한다.
- 테넌시: 읽기 scope는 URL band를 서버에서 해석하고, `visible_query`가 항상 `entity.band_id == scope.band_id`를 추가한다(`backend/band_access.py:154-198`). v2 변경은 인증 사용자와 URL band의 active membership을 확인한다(`backend/routes/content_writes.py:63-90`). 소스 검토에서 cross-band IDOR 경로는 확인하지 못했다.
- 업로드/R2: v2 upload initiation은 대상 리소스 소속, band storage quota 및 예약 byte를 체크한다(`backend/routes/uploads.py:327-379,448-492`). multipart/direct upload는 capability 검증과 완료 시 실제 객체 byte 확인을 수행한다. 레거시 다운로드도 record와 허용 원본 key 확인 후에만 signed URL을 발급한다(`backend/routes/songs.py:446-459`, `backend/routes/personal_logs.py:123-133`). 공개 R2 bucket 설정 자체는 운영에서 확인하지 않았다.
- 공개 GET / 비용: v2 page 요청은 bounded limit/offset을 사용한다(`backend/routes/content_reads.py:52-79`); 공개 band 검색은 한 페이지 최대 100건이다(`backend/routes/bands.py:84-104`). 다만 전체 route에 앱 차원의 rate limit을 찾지 못했고, 레거시 gallery GET은 모든 항목을 한 번에 반환한다(`backend/routes/gallery.py:18-22`). 공개 legacy GET도 운영 정책상 필요한지 확인하고, edge-level rate limit 및 pagination을 검토한다.
- CSRF/CORS: 서버 인증은 Bearer Authorization 헤더 기반이며 세션 cookie를 사용하는 보호 route는 확인하지 않아 classic cookie-CSRF 노출은 발견하지 못했다. 인증 응답에는 private/no-store와 `Vary: Authorization`가 추가된다. production CORS는 allowlist지만 debug 설정은 wildcard+credentials다(`backend/app.py:385-393`); production에서 debug가 켜지지 않는다는 배포값은 이 감사가 직접 조회하지 않았다.
- XSS: React 일반 텍스트 렌더링은 기본 escape를 이용한다. 외부 링크는 client helper의 HTTP(S), credential, control-character 검사를 거친다(`frontend/src/services/safeExternalUrl.js:1-38`); 여러 새 창 링크에 `noopener noreferrer`를 사용한다. source scan에서 `dangerouslySetInnerHTML`/`innerHTML` sink를 찾지 못했다. 그러나 Supabase browser session은 `localStorage`에 저장된다(`frontend/src/services/supabase.js:46-56`). 저장형/반사형 XSS가 새로 도입되면 token 탈취 영향이 크므로 CSP 및 HTML sink lint를 defense-in-depth로 권고한다. 실제 배포 응답 CSP·HSTS·nosniff 헤더는 외부 호스팅 설정을 확인하지 않아 미확인이다.
- 관리자 bootstrap: 설정된 verified email과 일치하는 신규 user에게 단 한 번 `AuthBootstrapState`를 claim하도록 한다(`backend/auth_service.py:273-317`). 사용 중인 운영 bootstrap email 값과 수신 계정 보호 상태는 비밀 취급 대상이므로 확인하지 않았다. 권한 변경 회수/감사 절차는 운영 runbook에서 확인해야 한다.

## 우선순위별 권고 작업 지시안

1. P2 — Python 의존성 담당: `Flask-Cors==4.0.0`을 공식 권고 수정 버전 이상으로 갱신하고, CORS allowlist·credentials·private-network 및 경로 매칭 회귀 테스트를 추가한다. 작업 전에 현재 최신 지원 버전과 Flask 호환성을 확인한다.
2. P2 — frontend 의존성 담당: Vite 7.3.1을 7.3.7 이상 또는 유지 중인 수정 릴리스로 갱신하고 lockfile을 커밋한다. Windows 개발환경과 Linux CI/공유 dev host를 구별해 각 advisory의 추가 조건 및 `--host` 사용 여부를 검사한다.
3. P2 — API 운영/백엔드 담당: `/api/search-places`에 edge 또는 앱 rate limit, 입력 상한, cache 및 provider quota alert를 준비한다. 레거시 전체 목록 GET의 필요성을 확인하고 페이지 제한을 계획한다.
4. P2 — 릴리스 담당: `LEGACY_V1_WRITES_ENABLED=false`를 배포 선언/자동 검증으로 고정하고, production 설정 누락 시 fail-closed하도록 검토한다. 이것은 현재 노출 보고가 아니라 설정 회귀 예방 작업이다.
5. P3 — 인증 담당: PyJWT를 현재 지원 2.x인 2.15.0 이상으로 갱신 검토한다. advisory마다 수정 버전이 다르므로 GHSA-jq35 2.13.0, GHSA-r6x4 2.14.0, GHSA-42vr 2.15.0을 구분해 영향 함수와 실제 앱 도달성을 확인하고, JWKS key type별 algorithm, key rotation 및 malformed JWT 거부 회귀를 검증한다.
6. P3 — 업로드 담당: MIME 결정 및 저장 객체 전달 헤더를 제품 저장 도메인 구조와 함께 검토한다. audio/video inline 재생은 유지하고 위험 문서만 attachment로 전달한다. 비운영 R2 bucket으로 확장자/MIME mismatch 및 cross-origin 격리를 검증한다.
7. P3 — 보안/프론트 담당: production headers(CSP, HSTS, `X-Content-Type-Options`)를 호스팅 계층에서 검증하고, inline HTML sink를 막는 lint/테스트를 검토한다. 브라우저 `localStorage` 토큰 접근 위험과 CSP 정책을 함께 평가한다.

이 문서는 취약점 완화 작업을 자동 승인하거나 구현하지 않는다. 작업 지시안은 main 담당자 검토 후 별도 승인된 구현 작업으로 진행해야 한다.
