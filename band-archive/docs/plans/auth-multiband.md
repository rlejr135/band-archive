# 계정·다중 밴드 전환 계획

## 목표와 경계

Anything를 단일 밴드·기기 식별 기반 서비스에서 이메일 인증을 사용하는
다중 밴드 서비스로 전환한다. 사용자는 여러 밴드의 멤버가 될 수 있고,
밴드별 공개 범위와 역할에 따라 콘텐츠·업로드·운영 기능에 접근한다.

이 문서는 목표 아키텍처와 단계별 전환 계약이다. **이 단계에서는 현재
SQLite 데이터, R2 객체, 기존 API 및 `db.create_all()` 시작 동작을 바꾸지
않는다.** 실제 운영 DB 전환과 레거시 제거는 아래의 검증 관문을 통과한
별도 배포에서만 한다.

## 목표 아키텍처

### 인증과 운영 역할

- Supabase Auth가 이메일/비밀번호, 중복 이메일 방지, 이메일 확인, 로그인,
  로그아웃과 비밀번호 재설정을 맡는다. Flask는 비밀번호를 저장하지 않고
  Supabase access JWT를 JWKS로 검증한다.
- 가입 후 사용자는 공개 밴드를 찾거나, 초대 코드로 가입하거나, 밴드 생성
  요청을 낸다. 가입 요청은 밴드 소유자 또는 관리자가 승인/거절한다.
- 밴드 생성 요청은 플랫폼 관리자가 승인하면 요청자가 소유자가 된다.
  최초 플랫폼 관리자는 `BOOTSTRAP_PLATFORM_ADMIN_EMAIL`로 한 번만
  부트스트랩한 뒤 DB 역할로 운영한다.
- 역할은 방문자, 멤버, 관리자, 소유자, 플랫폼 관리자로 구분한다. 방문자는
  공개 콘텐츠만 읽고, 활성 멤버만 쓰기·댓글·투표·업로드를 할 수 있다.
  관리자는 밴드 운영, 소유자는 관리자 지정/소유권 이전/밴드 폐쇄를 수행한다.
- 마지막 소유자는 소유권을 이전하기 전에는 탈퇴하거나 계정을 삭제할 수 없다.
  밴드 삭제는 30일 소프트 삭제이며, 계정 삭제는 작성물을 보존하고 작성자를
  익명화한다.

### 밴드와 콘텐츠 격리

- `AppUser`, `Band`, `BandMembership`, `BandCreationRequest`,
  `BandInviteCode`, `AuditEvent`를 추가한다. 초대 코드는 256비트 난수로
  발급하고 원문은 저장하지 않으며, 해시·폐기 상태·사용 감사 기록만 저장한다.
- 기존 곡, 합주, 미디어, 개인 기록, 갤러리, 공지, 추천, 댓글, 투표,
  업로드 세션에는 `band_id`를 추가한다. 작성 가능한 레코드는
  `created_by_membership_id`로 멤버십 소유자를 기록한다.
- `Member` 프로필은 로그인 계정과 분리해 유지하고 `band_id` 및 선택적
  연결 사용자 ID를 가진다. 기존 프로필은 보존하고 관리자가 새 계정에
  연결한다.
- 밴드 기본 공개 범위는 `PUBLIC`이며, 콘텐츠별 `PUBLIC`/`MEMBERS`
  공개 범위를 둘 수 있다. 댓글은 부모 콘텐츠 공개 범위를 상속한다.
  상위 항목이 멤버 전용이면 하위 미디어를 공개로 지정해도 멤버 전용이다.
- 새 댓글은 계정 소유로 저장하고 이름/비밀번호를 받지 않는다. 기존 댓글과
  기기 기반 투표는 `legacy`로 보존하며, 새 투표는 사용자당 대상별 한 표다.

### API, 클라이언트, 파일

- 새 API는 `/api/v2`에 둔다. `GET /me`, 내 밴드/공개 밴드 조회, 가입·생성
  요청, 초대 코드, 멤버십 승인과 `/bands/{bandId}/...` 도메인 API를 제공한다.
  Bearer 토큰을 사용하며, 공개 GET만 토큰 없이 `PUBLIC` 데이터를 반환한다.
  미인증은 401, 역할 부족은 403, 다른 밴드 객체는 404로 처리한다.
- 모든 도메인 조회·변경은 중앙 band-scope helper를 거쳐 `band_id` 조건을
  적용한다. 응답에는 `visibility`, `can_edit`, `can_delete`, `can_manage`를
  포함한다. 브라우저는 Supabase Data API로 도메인 테이블에 직접 접근하지
  않고 Flask API만 사용한다.
- 웹에는 로그인/가입/이메일 확인/비밀번호 재설정과 `/bands`,
  `/bands/:bandSlug/...`를 추가한다. `AuthProvider`, `BandProvider`와
  헤더 밴드 전환기를 두고, 업로드 센터는 밴드 ID·이름을 보존한다.
  네이티브 앱의 refresh token은 Capacitor 8 호환 Keychain/Keystore에
  별도로 보관한다.
- 새 R2 객체 키는 `bands/{bandId}/media/...`, `personal-logs/...`,
  `gallery/...`를 사용한다. 기존 객체를 복사·삭제하지 않고 명시적 기존
  object key를 DB에 보존한다. 공개 URL은 최대 1시간, 멤버 URL은 최대
  5분 서명 URL이며 R2 버킷은 비공개다. 업로드 세션은 밴드·생성자·예약
  용량을 기록하고 활성 멤버만 presign/multipart를 시작할 수 있다.

## 단계별 구현·전환

1. **계약과 마이그레이션 기반** — 본 문서와 현재 모델 기준 Alembic
   baseline, fresh DB upgrade 및 existing SQLite safe-stamp 도구를 추가한다.
   기존 시작 마이그레이션과 `db.create_all()`은 이후 전환까지 유지한다.
2. **Supabase 개발/스테이징** — Tokyo 프로젝트, 이메일 확인, Gmail SMTP,
   Cloudflare Turnstile, JWT JWKS 검증과 환경 설정을 추가한다.
3. **밴드/멤버십/RBAC** — 새 모델, migration, 가입·초대·승인·소유권 이전
   API와 역할 테스트를 구현한다.
4. **기존 테이블 확장** — nullable `band_id`, 작성자, 공개 범위, object key를
   expand → backfill → 검증 → NOT NULL 순으로 적용한다.
5. **이전 리허설** — Fly SQLite 복사본을 Supabase staging Postgres로 반복
   이관한다. 현재 데이터는 `들뜬` 밴드와 `PUBLIC`으로 연결하고, 기존 멤버는
   미연결 프로필, 댓글/투표는 legacy로 둔다.
6. **v2 API와 호환 계층** — 공개 읽기부터 RBAC 쓰기까지 순차 전환한다.
   v1은 `들뜬` 밴드로 매핑하고 `LEGACY_V1_WRITES_ENABLED`와 호출 측정으로
   기존 클라이언트 쓰기를 한시 보장한다.
7. **웹 UI 전환** — 인증, 밴드 선택, 공개/가입/관리 화면, 기존 도메인의
   band-aware 전환을 차례로 배포한다.
8. **업로드/네이티브 권한** — band-scoped 키, 용량 예약과 웹·Android·iOS
   업로드 큐의 band identity를 적용한다.
9. **운영 전환** — 점검 중 SQLite 쓰기를 멈추고 snapshot/R2 manifest를 만든
   뒤 최종 import와 검증 후 Postgres 백엔드를 배포한다.
10. **소유권 수령과 클라이언트 배포** — 기존 `들뜬` 밴드의 단기 claim token으로
    첫 소유자를 정하고, 백엔드 → Cloudflare → TestFlight → Play Store 순으로
    배포한다.
11. **레거시 종료** — 최소 2주간 새 앱 사용 및 v1 쓰기 소멸을 확인한 뒤 v1
    쓰기를 끄고, 기존 비밀번호 댓글/문자열 admin 경로와 SQLite를 별도 승인
    후 제거한다.

## 외부 선행 조건

- Supabase Tokyo 프로젝트를 만들고 staging과 production을 분리한다. 프로젝트
  URL, anon key, JWT issuer/audience/JWKS URL 및 Postgres 연결 정보는 환경
  변수/비밀 관리에만 저장한다.
- Supabase Auth에서 이메일 확인과 redirect URL을 Cloudflare Pages의 실제
  도메인으로 설정한다. Gmail SMTP app password는 저장소/Fly 이미지에 넣지
  않는다. 공개 서비스에서 일 30건 수준을 넘거나 전달 문제가 나면 Resend 등
  전용 발송 서비스로 교체한다.
- Cloudflare Turnstile 사이트/비밀 키와 허용 도메인을 준비한다. 서버만 secret
  key를 검증한다.
- Fly SQLite volume snapshot을 복구 가능하게 확보하고, R2 object key·크기
  manifest를 별도 안전 위치에 보관한다. 이 단계에서는 R2 삭제를 절대 하지
  않는다.
- Fly 환경 변수와 Cloudflare 환경 변수에는 해당 환경의 Supabase 값만 넣고,
  production service-role key는 Flask가 필요해지는 기능이 확정되기 전까지
  추가하지 않는다.

## 배포 관문과 완료 기준

- 각 expand migration은 빈 SQLite/Postgres DB, 현재 운영 schema 복사본,
  반복 실행에서 검증한다. 현재 schema와 다른 DB는 자동 stamp하지 않고
  명시적으로 거부한다.
- Postgres 이전 리허설은 테이블 건수, FK, 댓글 트리, 투표 점수, R2 key·크기
  manifest를 SQLite 원본과 대조한다. 실제 전환 전에 복구 리허설을 한다.
- 두 사용자·두 밴드·겹치는 숫자 ID로 교차 밴드 차단, 역할 매트릭스, JWT
  만료/위조, 가입 승인 경쟁, 공개 범위 상속, R2 URL 만료·탈퇴 뒤 재발급
  차단, 용량 예약 경쟁을 자동 테스트한다.
- 최종 전환은 백엔드 v2 smoke test, CORS, Fly 로그, Cloudflare 실제 bundle,
  Supabase 이메일과 연결을 확인한 뒤에만 쓰기를 재개한다. 쓰기 재개 전 실패는
  Fly v48 + SQLite로 되돌릴 수 있지만, 재개 후에는 역동기화 자동 롤백을 하지
  않는다.
