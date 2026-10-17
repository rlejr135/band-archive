# 가입 소개·최소 고지·봇 방지 구현 계약

작성일: 2026-10-17. 사용자 구현 승인. 구현은 BE/FE 서브에이전트, 총괄은 검토·검증·단위 커밋·배포를 담당한다.

## 범위

1. 밴드 가입 요청 시 선택 소개 최대 300자(평문)를 입력한다. 초대 코드의 즉시 가입 정책은 유지한다.
2. 같은 밴드 owner/admin만 pending 요청의 소개를 조회한다. 일반 명단·공개 API·감사 로그에는 본문을 넣지 않는다. 승인·거절·초대 가입 시 소개를 비운다. 중복 요청은 최초 소개를 덮어쓰지 않으며 재가입 요청은 새 소개를 쓴다.
3. 비로그인 열람 가능한 정책 페이지, 가입 핵심 안내, 약관 동의와 만 14세 이상 자기확인을 구현한다. 개인정보 고지를 포괄적 필수동의로 바꾸지 않는다. 미확정 운영 사실을 실제 정책으로 공표하지 않는다.
4. Cloudflare Turnstile을 Supabase Auth signup/signin/password recovery에 연동한다. frontend 검사만으로 보호했다고 주장하지 않는다. 웹 화면 준비·검증 후 Supabase 서버 검증을 활성화한다. 네이티브 호환은 별도 실기기 검증 전 보장하지 않는다.

## 중앙 설정

- 임시 서비스 이름: Band Archive.
- 운영자·개인정보 문의 담당자 표기: 황기덕(사용자 승인).
- 문의 이메일 기본값: rlejr135@gmail.com(사용자 승인).
- 발신 이메일은 명시적 별도 설정이 없으면 같은 중앙 연락처를 상속한다. 개별 FE 페이지·메일 템플릿에 주소를 하드코딩하지 않는다.
- 실제 메일 발송 활성화·기존 SMTP 자격정보 교체는 포함하지 않는다. Turnstile secret은 FE/저장소/로그/채팅에 넣지 않는다.

## API 및 호환성

- `POST /api/v2/bands/{id}/join-requests`: 기존 `{}`와 새 `{introduction?: string}`을 허용한다. 공백은 NULL, 300자 초과·비문자열·허용되지 않은 제어문자·과도한 body는 서버에서 거부한다.
- 기존 manager membership DTO의 pending 행에만 `introduction`을 추가한다.
- `GET /api/v2/policy`: 중앙 service 설정, 정책 readiness/버전/확정 사실, 공개 CAPTCHA 설정만 반환한다.
- `POST /api/v2/me/policy-acceptance`: 현재 서버 terms/privacy 버전, `terms_accepted: true`, `age_14_plus: true`를 확인하고 최소 receipt를 기록한다. privacy 버전은 고지 버전이며 별도 개인정보 동의라고 표현하지 않는다.
- `GET /api/v2/me`: 서버 DB/config/receipt에 근거한 `policy_acceptance_required`를 제공한다. 수정 가능한 Supabase user_metadata를 권한 증거로 사용하지 않는다.
- 새 정책 rollout cutoff 이후 생성된 로컬 AppUser에만 요구한다. 기존 로컬 계정의 강제 재동의는 하지 않는다. 이전 Supabase 계정이어도 로컬 계정이 처음 만들어지는 경우는 신규 로컬 사용자다.
- 필요한 새 계정만 v2 쓰기를 차단하며 로그인·읽기·확인 접점은 유지한다. 이미 발급된 업로드 세션의 capability/ACK/완료/취소 계약은 깨뜨리지 않는다. 신규 업로드 시작은 새 쓰기 정책을 따른다.
- 정책 enforcement와 CAPTCHA는 준비 전 기본 비활성이다. 부분 구현 배포만으로 현재 회원가입을 임의 차단하지 않는다. 정책 공표 전 실제 정보 확정, CAPTCHA 활성화 전 키·화면·서버 검증이 필요하다.

## 검증·운영 경계

- 사용자 2명·밴드 2개·역할별 소개 접근, 재가입·중복·승인 경합·본문 노출 없음.
- 정책 버전/동의/연령 assertion 누락·위조·재시도, 기존 계정 불변, 신규 계정 쓰기 차단·확인 후 해제, capability 계약 유지.
- CAPTCHA 토큰 누락·만료·재사용·실패·StrictMode·계정/화면 전환·장애 처리와 signup/login/recovery 회귀. 단순 버튼 비활성화를 서버 보호로 간주하지 않는다.
- 전체 BE pytest와 FE 테스트·빌드를 격리 환경에서 실행한다.
- 2026-10-17 사용자는 소개 필드와 약관·연령 receipt의 운영 DB 변경을 로컬 테스트 및 운영 백업·복구 검증 후 적용하도록 별도 승인했다. 기존 콘텐츠·계정·R2 객체는 유지한다.
- 원격 키 등록·Supabase CAPTCHA 활성화는 준비 후 별도 확인한다. 실제 R2 삭제·변환·스케일 변경은 범위 밖이다.

## 미확정 사항

- 공급자별 실제 처리·백업·로그 보유 사실과 정책 본문 공표 가능 상태.
- 계정 삭제의 이메일 접수·본인 확인 후 수동 처리 운영 절차(사용자 확인 요청 중).
- Turnstile 위젯 공개 Site Key, 허용 호스트와 실제 서버 검증 활성화.

참고: [Supabase CAPTCHA](https://supabase.com/docs/guides/auth/auth-captcha), [개인정보위 필수동의 관행 안내](https://pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS074&mCode=C020010000&nttId=10566).
