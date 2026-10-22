# 업로드·권리 보호 정책 배포 전 준비 보고

작성: 2026-10-21 (KST) · 상태: **운영 DB v22·BE v64·FE 기술 릴리스 검증 완료 / 정책 활성화 보류**

최종 갱신: 2026-10-23 (KST). 첫 전체 회귀와 2026-10-22의 기술 선배포 결정 기록은 유지한다. 이 갱신은 root가 확인한 운영 도구의 검증 결과를 문서화한 것이며, 문서 작성자가 DB 적용이나 배포를 실행한 것은 아니다.

이 문서는 로컬 소스·테스트, root가 전달한 2026-10-23 운영 DB 검증 결과, 이전에 확인한 Fly 기준선을 구분해 기록한다. 비공개 백업·보고서 원문을 공개하거나 정책의 법적 적합성을 인증하지 않는다.

## 구현 범위와 사용자 결정

- 6단계 정책 구현 범위에는 가입·업로드 확인, 신규 업로드의 MEMBERS 기본값, 공개 범위 확대 확인, 콘텐츠 권리 제한 및 요청 처리에 필요한 백엔드·프런트엔드 계약, 빈 `content_restriction` 테이블과 인덱스 추가가 포함된다. 기존 공개 콘텐츠의 일괄 변경이나 실제 제한 사례 등록은 범위에 없다.
- 업로드 확인 기록은 콘텐츠 유지 동안 보존하고 삭제 확정 후 30일 이내 정리한다. 계정 삭제 시 감사 기록의 개인 연결을 제거하거나 익명화한다. 문의·신고 처리 기록은 종료 후 30일 이내 정리하되 진행 중인 분쟁과 법정 보존에 필요한 최소 자료는 구분한다. 삭제 확정 개인정보가 포함된 운영자 DB 백업 잔존분은 확정 후 30일 이내 수동 정리한다. 이 기준은 승인된 운영 원칙이며, 이번 변경으로 실물 삭제 자동화가 작동한다고 주장하지 않는다.
- 최종 국외 이전 고지와 `ready=true` 활성화는 별도 검토 대상이다. R2의 APAC 위치 힌트를 특정 국가 보장으로 설명하지 않는다.
- 사용자 최신 결정: Cloudflare 위치 문의는 생략하고 승인된 기술 변경만 먼저 릴리스한다. 미확정 정책은 공표하지 않고 `ready=false`, `POLICY_SIGNUP_ENABLED=OFF`, `UPLOAD_POLICY_REQUIRED=OFF`를 유지한다. 실제 확인되지 않은 R2 국가를 한국 전용 저장으로 가정하거나 고지하지 않는다. 실메일 발송·비밀 설정 변경·R2 쓰기/삭제·재변환은 이번 범위에 없다.

## 현재까지의 검증 이력

| 항목 | 기록된 결과 | 한계·후속 |
|---|---|---|
| 프런트엔드 | 345/345 테스트 통과, 변경 파일 lint 및 production build 통과 | 실제 브라우저 화면의 시각 확인은 수행하지 않음 |
| 백엔드 focused migration | 32개 통과 | 전체 회귀 결과와 별도 기록 |
| 백엔드 focused upload/restriction | 48개 통과 | 전체 회귀 결과와 별도 기록 |
| 첫 백엔드 전체 회귀 | 1088개 통과, 8개 실패 | 7개는 레거시 보존 복원 도구의 신규 테이블 분류 누락, 1개는 SQL 계약 테스트의 alias 고정 기대값. 두 수정은 리뷰됐고 아래 최종 전체 회귀에서 재검증함 |
| 후속 focused 재검증 1차 | 93개 통과, 2개 실패 | 새 PostgreSQL 거부 테스트의 준비 INSERT가 동일 `:state` 매개변수를 상태 컬럼과 `CASE`에 재사용해 `AmbiguousParameter`로 실패. 거부·보존 assertion에 도달하기 전의 fixture 오류 |
| INSERT 수정 후 대상 2건 | 2개 통과, 28.36초 | 출력 SHA-256은 전달된 발췌에서 `3844c5…`로 시작. 전체 해시는 이 보고서에 기록하지 않음 |
| INSERT 수정 후 focused 재검증 | 95개 통과, 경고 582개, 266.25초 | stdout SHA-256 `57d1806ad2a485a5600862beaafcfb14ad03fed4b50e0b07c96cae753d1c1811` |
| 최종 백엔드 전체 회귀 | 1098개 통과, 0개 실패, 0개 건너뜀, 경고 954개 | pytest 883.79초, 실행기 887.984초, 종료 코드 0. stdout SHA-256 `9856defff27ccdc47cf7a28c734c7ad7ecafddc1d15d348aad659a268fb4116b`; 빈 stderr SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 운영 helper 첫 사전 gate | 백업 전에 중단, `source table topology differs` | 앱 메타데이터 36개에 별도 `alembic_version`이 빠진 whitelist 구성 오류. 이 시도에서 private 작업 디렉터리·보고서·백업은 생성되지 않음 |
| helper 수정 후 테스트 1차 | 9개 통과, 1개 실패 | 실제 pinned inventory의 named cursor 경로를 테스트 fake가 구현하지 못한 오류. 안전 gate를 완화하지 않고 fake만 보완 |
| helper 수정 후 로컬 테스트 | 10개 통과, 실패 0개, 건너뜀 0개 | 운영 helper 수정 커밋 `7d71302`; 검증된 실행 소스 커밋 `7d71302ab53314c1d791fd8153000e0185626fa0` |
| 운영 읽기 전용 백업·소유 로컬 복원 리허설 | verified, `remote_writes=false` | 원본 37개 public 테이블(앱 36개+Alembic ledger 1개) 복원 및 로컬 v22 적용·38개 테이블·원래 행/보안/키 보존 확인, 소유 로컬 PG 정상 종료(`pg_ctl` exit 0) |
| 승인된 운영 DB v22 적용 | verified, `committed=true`, `fresh_readonly_postcheck=true` | 사전 조건 성공 후 빈 `content_restriction`과 인덱스만 추가. 새 제한 테이블 0행, 기존 기록 보존 검증 통과; BE·FE 배포 성공을 뜻하지 않음 |

이전 PostgreSQL migration 테스트의 진단 실행은 ORM 읽기 트랜잭션이 별도 Alembic 연결의 잠금을 막아 취소됐다. 그 진단 취소는 통과가 아니라 실패 이력이다. 테스트 경계에 `db.session.remove()`와 로컬 테스트 연결 전용 5초 `lock_timeout`을 추가해 자체 잠금 문제를 수정했다. 운영 migration의 잠금·시간 제한은 바꾸지 않았다.

레거시 보존 복원 도구는 `content_restriction`을 반드시 비어 있어야 하는 테이블로 분류한다. 활성·해제된 제한 이력 모두 기존 콘텐츠 ID에 잘못 다시 연결될 수 있으므로, 한 건이라도 있으면 원본을 바꾸기 전에 거부한다. SQL 계약 테스트는 변할 수 있는 alias 번호를 고정하지 않으면서 동일한 부모 alias의 밴드 ID, 개인 기록 ID, 부모 댓글 연결과 `EXISTS` 조건을 계속 확인한다.

## 운영 DB와 기술 릴리스 결과

- **운영 DB:** 2026-10-23 root의 리허설·적용·별도 읽기 전용 사후 검증 결과 schema v22. **Fly:** 기존 v63에서 v64로 배포·확인 완료. NRT의 기존 machine 1대(`shared 2 CPU`, 2048 MB)는 그대로다. 이미지 `deployment-01M5J2XJ9512B3A31S3J8BWNW8`, 소스 `7d71302`.
- 검증 이력의 로컬 단위 커밋은 프런트엔드 `cea4d6e`, 백엔드 `4b5661b`, 최초 운영 검증 도구 `40b755b`다. ledger whitelist를 바로잡은 운영 helper는 `7d71302`에서 추가 검증됐다. 운영 실행 소스는 전체 SHA `7d71302ab53314c1d791fd8153000e0185626fa0`로 고정했다.
- 리허설 증거 SHA-256: report `d1d8a55112eeaed398acf87203d06154787b92b1dcd14cd4e937131a4508eb2b`, manifest `8027e1f6295c8b3768c3c87e74f54d585d1b278f7796e5b6ff7dc91ac734f0b0`, public dump `70f9ce9efc9b782047cc9af6d2e49917027bff6f476bc462f686a631685ae133`. 운영 적용 report SHA-256은 `d38253528f4e49e8d2a459d43637c4a2ed079dc7312917e85447ef4ef5c62e26`. 비공개 원문·경로는 이 문서에 싣지 않는다.
- 배포 후보 소스는 Git archive로 추출해 사용자 소유 untracked script와 백업을 빌드 컨텍스트에서 제외했다. 공개 CA 해시를 검증했고 빌드 컨텍스트는 1.23 MB다. BE 배포 성공 여부는 아래 릴리스·운영 응답 검증으로 별도 확인했다.
- BE 배포 뒤 시작 로그 5건을 포함한 표준 JSON 로그 스트림 29건을 파싱해 Gunicorn 시작 1건·listen 1건·worker boot 1건, 오류 후보 0건을 확인했다. 운영 `/api/v2/policy`는 HTTP 200과 정확한 Pages origin CORS를 반환했다. 응답의 `policies.ready=false`, `policy_acceptance_enabled=false`, `upload.enabled=false`이며 신규 upload 응답 구조는 존재한다. `/members`도 HTTP 200과 정확한 Pages origin CORS를 확인했다. 초기 sandbox DNS 제한은 권한 있는 읽기 전용 네트워크 검증에서 해소됐으며 API 오류로 기록하지 않는다.
- 운영 DB DDL 전후 Supabase advisor의 경고 2개는 동일하여 새 경고가 관찰되지 않았다. 기존 개선 과제인 [`function_search_path_mutable`](https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable)과 [`auth_leaked_password_protection`](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)은 별도 검토 대상으로 남긴다. 이 결과만으로 모든 보안·법적 적합성을 인증하지 않는다.
- `origin/master` 일반 push는 `912d011`에서 `0999381ac846b282c02b1f1e1ca1d811ee78271c`로 완료됐다. 이 커밋의 런타임 기술 변경은 라이브에서 확인됐다. 이 보고서 자체의 후속 문서 커밋 ID는 자기참조를 피하기 위해 적지 않는다.
- Cloudflare 자동 배포의 새 라이브 번들 `/assets/index-B5n_c4WL.js`는 HTTP 200이며 `업로드 전 확인` feature marker와 `https://band-archive.fly.dev` API 대상이 확인됐다. 공개 웹 `/`, `/terms`, `/privacy`, `/upload-policy`는 각각 HTTP 200. `/members`도 HTTP 200이고 CORS는 정확히 `https://band-archive.pages.dev`였다. 정책 API의 세 값 `policies.ready`, `policy_acceptance_enabled`, `upload.enabled`는 모두 실제 boolean `false`로 재확인했다.
- production public 환경값에 따라 로컬 번들 `index-CKj0b_Hs.js`와 라이브 번들의 이름이 달라질 수 있으므로, 이름·hash 동일성 대신 라이브 feature marker와 API 대상을 검증했다. 실제 브라우저 시각 검증과 로그인·업로드 실동작 테스트는 수행하지 않았다. 이는 위 기술 릴리스 검증 결과와 구분한다.
- R2 원본 쓰기·삭제, 재변환, 실제 메일 발송, 비밀 설정 변경은 수행하지 않았다. 초대 링크 버그와 VOC는 다음 작업으로 남아 있으며 이 릴리스에서 착수하지 않았다.
- 현재 R2 버킷의 가능한 처리 국가·관할, 국가 보장 여부와 국외 이전 고지 자료는 여전히 미확인이다. [Cloudflare 문의 초안](../policies/provider-location-confirmation.md)은 미발송 참고 문안일 뿐이고, 사용자는 이번 기술 릴리스의 선행 조건으로 문의를 요구하지 않기로 했다.
- 기술 릴리스 후에도 공급자별 실제 경로와 법적 근거를 확인하기 전에는 한국 전용 저장이라는 주장이나 최종 정책 공표·필수 확인 활성화를 하지 않는다.

## 완료 증거와 남은 정책 과제

- 최종 로컬 회귀 검증: **완료.** 프런트엔드 345/345와 lint/build, 백엔드 1098/1098 통과. 실제 브라우저 화면의 시각 확인은 수행하지 않음
- 검증된 로컬 단위 커밋: **FE `cea4d6e`, BE `4b5661b`, 운영 검증 도구 `40b755b`**. 이 보고서의 후속 커밋 ID는 자기참조를 피하기 위해 기록하지 않음
- 운영 읽기 전용 백업·소유 로컬 복원 리허설과 승인된 DB v22 적용: **root의 검증 결과 완료.** 비공개 증거 원문은 별도 보관
- BE 릴리스: **Fly v64 배포·시작 로그·운영 HTTP/CORS 확인 완료.** 정책 확인·업로드 필수 플래그 비활성 확인
- FE 릴리스: **일반 push·Cloudflare 자동 배포·HTTP·라이브 번들·feature marker·API 대상·CORS 검증 완료.** 브라우저 시각 검증과 로그인·업로드 실동작 테스트는 미실시(이번 숨김 기술 릴리스의 별도 완료 조건은 아님)
- 정책 최종 검토·공표, `ready=true` 및 필수 확인 활성화: **국외 이전 사실·법적 근거 미확정으로 보류**
