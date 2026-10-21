# Cloudflare R2 위치·국외 처리 확인 메모

작성일: 2026-10-21. 운영자가 Cloudflare 지원 또는 개인정보 문의 채널에 복사해 보낼 질문 초안이다. 아직 발송하지 않았으며 답변을 받거나 계약 변경을 완료했다는 뜻이 아니다. 계정·버킷 식별자는 공개 문서에 쓰지 않고 발송 시 필요한 범위에서만 `<ACCOUNT_ID>`, `<BUCKET_NAME>` 자리를 채운다.

[Cloudflare R2의 공식 위치 안내](https://developers.cloudflare.com/r2/reference/data-location/)에 따르면 `apac`는 Asia-Pacific 위치 힌트이며 위치 힌트는 최선 노력 방식으로 특정 국가의 저장을 보장하지 않는다. 같은 문서는 별도의 jurisdictional restriction을 구분한다. 따라서 현재 버킷의 APAC 설정만으로 일본·한국 등 특정 국가나 관할을 개인정보 고지에 적지 않는다.

## 보낼 곳

- 이 개인정보·위치·국외 이전 근거 문의는 [Cloudflare 개인정보 정책의 6·7절](https://www.cloudflare.com/privacypolicy/)에 안내된 `privacyquestions@cloudflare.com`을 사용할 수 있다. 아래 영문 초안을 운영자가 검토해 직접 보낸다. 발송·회신 여부나 답변 시점은 보장하지 않는다.
- 기술 지원 티켓 이용 가능 범위는 실제 Cloudflare 계정 플랜에 따라 [공식 지원 안내](https://developers.cloudflare.com/support/contacting-cloudflare-support/)에서 확인한다. 현재 확인된 Free 플랜은 Supabase의 플랜이며 Cloudflare 플랜으로 추정하지 않는다. 이 문의를 위해 유료 전환할 필요는 없다.

## 발송 전·답변 후 확인

- 운영자가 현재 버킷의 실제 위치 힌트·관할 제한 설정을 비공개 화면에서 확인하고, 필요하면 계정·버킷 식별자를 비공개 문의에만 제공한다. 비밀키·원본 파일·이용자 개인정보는 첨부하지 않는다.
- 답변에서 현재 버킷의 보장된 저장 국가 또는 가능한 처리 국가, 관할 제한의 유무와 범위, 객체·메타데이터·보안 로그의 경로를 구분한다. 공급자가 국가를 보장하지 않으면 그 사실을 그대로 기록한다.
- 실제 처리 주체·재수탁자, 국외 이전의 분류·목적·보유·삭제와 법정 고지에 쓸 공식 자료 또는 담당 연락처를 받는다. 전 세계 모든 서버 도시 목록이나 별도 GDPR DPA 서명을 일률적으로 요구하지 않는다.

## English draft — copy for Cloudflare Support or Privacy team

**Subject:** R2 data location and cross-border processing details for a Korean hobby web service

Hello Cloudflare Support / Privacy team,

We operate a free hobby band archive web service for users in South Korea aged 14 and over. We use an existing R2 bucket with an APAC location hint to store user-uploaded photos, video/audio objects, and derived media. We are preparing an accurate Korean privacy notice. Our account and bucket references, if needed for a private support ticket, are `<ACCOUNT_ID>` and `<BUCKET_NAME>`.

Could you please help us confirm the following for this **current bucket and service configuration**?

1. What country or countries may store the R2 objects and object metadata? Is any specific country or jurisdiction guaranteed for this bucket, or is APAC only a best-effort location hint? Please distinguish object storage from access, replication, support, and security-log processing where applicable.
2. Which Cloudflare entity and relevant processors or subprocessors receive or process these data, and where can we find their current names and contact information suitable for a Korean cross-border transfer notice? A link to an authoritative, current list is welcome.
3. For the object contents, object metadata, and service/security logs, what are the processing purposes, possible processing countries or jurisdictional paths, retention and deletion behavior, and any relevant support or backup paths? Please distinguish completed objects from incomplete multipart uploads.
4. Which official privacy or contractual materials describe the applicable cross-border processing/transfer category and legal basis, and which details can we accurately disclose to Korean users (recipient, country, data categories, purpose, transfer timing/method, and retention)? If a country cannot be guaranteed, please say so explicitly.

We are seeking information for our existing setup and notice. We are not requesting a paid-plan change, new bucket, migration, deletion, or contract amendment through this inquiry. If another Cloudflare team handles any item, please direct us to the correct public material or contact channel.

Thank you.

## 답변 후 남은 작업

1. 현재 버킷의 국가 보장 여부와 객체·메타데이터·로그 처리 경로, 고지 자료·법적 근거를 확인한다.
2. 확인된 범위로 최종 정책 문구를 작성·검토한다. 미확인 국가나 기간을 추정해 넣지 않는다.
3. 사전 백업·로컬 복원·원본 보존 검증이 성공한 뒤 운영 DB v22를 적용하고 BE·FE를 함께 한 번 배포한다. 실제 HTTP·CORS·라이브 번들을 확인한다.
