# Band Archive 도메인 용어집

현행 코드 지도 갱신: **2026-11-04**. 최초 분석은 2026-10-24 `b2b5610`이며, 이후 경계 추출을 반영했다. 도메인·DB·API 이름을 전면 변경하는 문서가 아니다.

현재 제품 기준은 **`d5cae87`**(댓글·투표·시작 복구 추출)이다. 전체 BE **1,738 passed·978 warnings·0 failed·0 skipped**, 제품 일반 push와 **Fly v91 배포·HTTP/CORS/시작 로그·기존 Pages 번들 확인을 완료**했다. 범위·실행 증거·한계는 [단일 최종 보고서](docs/reports/2026-11-04-architecture-refactoring-final.md)에 기록한다. 문서 커밋은 제품 커밋과 구분한다.

업무 명칭은 아래 구분을 따르고, 현재 경로는 관련 표와 `ARCHITECTURE_CONTEXT.md`에서 찾는다. 과거 Wave별 진행 과정은 기존 `docs/reports/2026-10-25-architecture-wave*-implementation.md`, `2026-10-26-architecture-wave{5,6,7}-*.md`에 보존돼 있다. 그 보고서의 당시 대기 상태를 현재 상태로 해석하지 않는다.

과거 리뷰·Wave 보고서는 로컬 보조 기록이며 새 clone에 없을 수 있다. 현재 용어·코드 경로·호환성 판단은 이 용어집과 아키텍처 지도·계획/보완서 및 실제 코드만으로 읽을 수 있으며, 이번 작업의 최종 검증·배포 증거는 단일 최종 보고서에 모은다.

새 서비스명은 실제 내부 코드 위치이며 DB 모델·테이블·URL rename을 승인한 것이 아니다. 표의 옛 모델 행 번호는 최초 분석의 탐색 기준이므로 현재 파일에서는 심볼을 검색한다. 비밀 설정·운영 데이터·의존성 문서는 조사 범위에서 제외했다.

## 1. 작업 지시에서 먼저 지킬 구분

1. **계정 → 밴드 가입 관계 → 멤버 프로필**은 각각 `AppUser`, `BandMembership`, 현재 모델 `Member`다. 같은 사람을 나타내더라도 ID·수명·역할이 다르다.
2. **곡**은 레퍼토리 정보 `Song`, **영상·음원**은 곡에 붙인 `Media`다. 음원 파일 자체를 `Song`이라고 지시하지 않는다.
3. **다음 곡 제안**, **미디어 투표**, **대표 미디어 선정**은 서로 다른 기능이다. “추천 기능”만으로 지시하지 않는다.
4. 현재 `Rehearsal`은 **합주·공연 일정**을 포괄한다. 공연은 `rehearsal_type=LIVE`이며 별도 `Performance` 테이블이 없다.
5. **가능 일정**은 멤버의 시간대별 가능 여부다. 확정된 합주·공연 일정, 출석, 예약과 다르다.
6. **밴드 대표 사진**, **밴드 로고**, **곡 대표 미디어**는 각각 다른 선택 관계다.
7. **서비스 포털**, **계정 관리**, **밴드 대시보드**, **밴드 관리**를 “메인/관리 페이지” 하나로 부르지 않는다.

### 관계 요약

```text
인증 주체(Supabase UUID) ─ AppUser(내부 정수 ID)
                              │
                              ├─ BandMembership ─ Band
                              │   가입 상태·권한      │
                              └─ Member ──────────────┘
                                  밴드별 이름·소개·포지션
                                  └─ PersonalLog

Band ─ Song ─ Media ─ MediaVote / Comment
        │       └─ 원본·720p·추출 M4A 재생 소스
        └─ rehearsal_songs ─ Rehearsal(합주·공연 일정)
Band ─ SongSuggestion ─ SongSuggestionVote
        └─ 채택(proceed) → Song 생성 + 제안 제거
Band ─ GalleryImage ← BandLogo
        └─ is_featured: 밴드 대표 사진
```

위 선은 업무 관계 요약이며 모든 관계가 같은 FK나 같은 생명주기를 갖는다는 뜻은 아니다. `Member.linked_user_id`는 과거 프로필 보존을 위해 NULL일 수 있다.

## 2. 주요 도메인과 현재 구현 이름

API 표에서 `B`는 `/api/v2/bands/{bandId}`다. FE 경로의 `{bandSlug}`와 API/DB의 정수 `{bandId}`를 혼용하지 않는다. `M:번호`는 `backend/models.py`의 근거 줄이다. 각 표의 웹 데이터는 별도 TS interface가 아니라 **JS 객체/props와 API JSON 계약**이며, 실제 명명 타입은 5절에 별도로 적었다.

### 2.1 계정·밴드·가입·프로필

| 권장 업무 명칭 / 영문 | 현재 Frontend 명칭 | 현재 Backend 명칭 | API / JSON | DB Model / Table |
|---|---|---|---|---|
| 사용자 계정 / **AppUser** | `AuthContext`, `useAuth().user`; 계정 화면 `BandsPage` | `authenticate_request`, `current_app_user`, `VerifiedIdentity` | `GET /api/v2/me`; 응답 계정 `id`는 Supabase UUID | `AppUser` / `app_user` (M:9) |
| 밴드 / **Band** | `BandProvider`, `BandContext`, `BandSwitcher` | `band_dict`, 중앙 band-scope 조회 | `/api/v2/bands`, `/api/v2/public/bands`, `/api/v2/bands/by-slug/{slug}` | `Band` / `band` (M:72) |
| 밴드 주소명 / **BandSlug** | UI “밴드 영어명”, `bandSlug` | `creation_slug`, `Band.slug` | `/api/v2/band-slugs/availability`; JSON `slug` | `Band.slug`, 생성 요청의 `slug` (M:77,196) |
| 밴드 가입 관계 / **BandMembership** | `band.membership`, `activeBands`, `pendingMemberships`, 관리 명단 | `membership_dict`, `require_band_role` | `B/memberships`, `/api/v2/memberships/{membershipId}` | `BandMembership` / `band_membership` (M:105) |
| 밴드 가입 요청 / **BandJoinRequest** | `BandJoinRequest`, `joinMode` | `join_request`, `approve_membership` | `POST B/join-requests`; `name`, `introduction`, `positions`, `custom_position` | **별도 모델 없음**. 대기 중인 `BandMembership`와 `requested_*` 필드 |
| 밴드 탈퇴 / **LeaveBand** | `BandsPage`의 탈퇴 처리 | `leave_band` | `POST B/leave` | `BandMembership.status=left`; 계정·기존 콘텐츠 삭제와 다름 |
| 멤버 추방 / **RemoveMembership** | `MemberRemovalButton` | `remove_membership` | `POST B/memberships/{membershipId}/remove` | 가입 관계를 비활성화; 프로필·콘텐츠 삭제와 다름 |
| 소유권 이전 / **TransferBandOwnership** | `BandsPage`의 이전 후 탈퇴 흐름 | `transfer_ownership` | `POST B/ownership-transfer`; `membership_id` | 활성 멤버십의 owner 역할 이전. 구 최초 수령 토큰과 무관 |
| 밴드 폐쇄 / **CloseBand** | 관리 capability `can_close_band` (버튼 존재와 별개) | `close_band` | `DELETE B` | `Band.deleted_at/purge_after`; 즉시 객체 삭제가 아닌 소프트 삭제 |
| 밴드 멤버 프로필 / **MemberProfile** | `MembersWorkspace`, `MemberProfileSummary`, `MemberProfileEditor` | `ensure_member_profile`, `activate_member_profile`, `serialize_member` | `B/members`는 `{members: [...], pagination}`; `B/members/{memberId}`, `B/members/me`는 `{member: ...}` | 현재 `Member` / `member` (M:810) |
| 밴드별 이름 / **MemberName** | 가입 폼 `name`, 프로필 `name` | `requested_name` → 활성화 시 `Member.name` | 가입 요청 `name`, 심사 응답 `requested_name`, 프로필 `name` | `BandMembership.requested_name`, `Member.name` |
| 가입 심사용 소개 / **JoinIntroduction** | 가입 폼 `introduction` | `join_introduction`, 관리용 `membership_dict` | 가입 요청·관리 응답 `introduction` | `BandMembership.join_introduction` (M:117) |
| 멤버 프로필 소개 / **MemberBiography** | 프로필 편집 `biography` | `profile_fields`, `own_profile` | `B/members/me`의 `biography` | `Member.biography` (M:816) |
| 연주 포지션 / **MemberPosition** | `MemberPositionPicker`, `memberPositions`, `positions` | `member_position_values`, `POSITION_LABELS` | `positions`, `custom_position`; 호환 `instrument` | `Member.positions_json`, `custom_position`, `instrument` (M:815) |
| 밴드 권한 역할 / **BandRole** | `bandRoleLabel`, 역할 선택 | `MANAGER_ROLES`, 멤버십 권한 검사 | `role=owner/admin/member` | `BandMembership.role` |
| 플랫폼 관리자 / **PlatformAdmin** | UI `superadmin`, `usePlatformAdmin` | 활성·이메일 인증 계정의 `is_platform_admin` 검사 | `/api/v2/platform/...`, 기능별 capability | `AppUser.is_platform_admin` |
| 밴드 생성 요청 / **BandCreationRequest** | `BandCreationRequestPage`, `BandCreationRequestsAdmin` | `create_request`, `review_request`, `creation_request_dict` | `/api/v2/band-creation-requests`, `/api/v2/platform/band-creation-requests` | `BandCreationRequest` / `band_creation_request` (M:189) |
| 밴드 초대 링크 / **BandInviteLink** | `InviteLinkRoute`, `InviteContinueRoute`, `inviteLinks` | `invite_link_service`, `invite_introduction` | `B/invite-links`, `/api/v2/invite-links/{preview,resume-context,resume,accept}` | `BandInviteLink` / `band_invite_link` (M:349) |
| 초대 가입 복귀 문맥 / **InviteResumeContext** | `inviteLinks`의 context 생성·복귀 처리 | `InviteResumeContext`, 복귀 검증 | 위 `resume-context`, `resume`; 초대 링크 토큰과 별개 | `InviteResumeContext` / `invite_resume_context` (M:367) |

보충:

- `BandSlug`는 URL용 식별자다. UI가 “영어명”이라고 해도 영문 번역명이나 별도의 밴드 표시 이름 필드라고 해석하지 않는다.
- 가입 심사용 소개는 프로필 소개가 아니다. 심사 결과 후 삭제하는 임시 소개를 공개 프로필에 자동 복사하는 기능으로 지시하면 안 된다.
- `MemberPosition`은 보컬·악기·기타 역할을 포함하는 복수 선택이다. 권한 `role`과 다르다. `instrument`는 기존 값을 보존하거나 포지션으로부터 만든 표시용 호환 문자열이다.
- 현재 명명은 `PlatformAdmin`이 코드와 가장 잘 맞으며 사용자 표현 `superadmin`은 그 별칭이다. `superadmin`이라는 밴드 멤버십 역할을 새로 만들지 않는다.
- 이 프로젝트의 “owner가 가능” 요구는 해당 기능에 **밴드 owner 또는 적격 전역 superadmin**을 포함하라는 뜻이다. 모든 API에서 전역 관리자가 자동 우회한다는 뜻은 아니며, 서버의 기능별 검사·응답 capability가 기준이다.
- `Band.created_by_user_id`는 생성자이며 현재 소유자를 결정하는 필드가 아니다. 소유자는 활성 `BandMembership.role=owner`로 판별한다.

### 2.2 곡·제안·미디어·반응

| 권장 업무 명칭 / 영문 | 현재 Frontend 명칭 | 현재 Backend 명칭 | API / JSON | DB Model / Table |
|---|---|---|---|---|
| 곡 / **Song** | `workspace/songs/{SongWorkspace,SongDetail,SongForm}.jsx`; 과거 `SongList`, `SongContext` | `song_service.py::{create_song,update_song,delete_song}`, `serialize_song` | `B/songs`, `B/songs/{songId}` | `Song` / `song` (M:505) |
| 곡 진행 상태 / **SongStatus** | `Practice/Completed/OnHold` 선택 | `Song.status`, 곡 payload 검증 | JSON `status` | `Song.status` |
| 곡 미디어 / **SongMedia** (문맥 명확하면 **Media**) | 미디어 목록, `MediaPlayer`, `MediaEngagement` | `serialize_media`, `Media` | 조회 `B/songs/{songId}/media`, `B/rehearsals/{rehearsalId}/media`; 삭제 `DELETE B/media/{mediaId}` | `Media` / `media` (M:585) |
| 미디어 표시명 / **MediaDisplayName** | v2 `display_name` → player의 `name` | `_media_display_name`, 원본 이름 기반 정제 | `display_name`; v1은 `filename`도 사용 | 원본 메타데이터 `original_filename` 등에서 파생; 별도 테이블 없음 |
| 서버 미디어 재생 정보 / **MediaPlayback** | `practicePlayback` 등에서 player용 `PlaybackMedia` 형태로 변환 | `media_playback`, URL 발급 | v2 `playback`; `url/video_url/audio_url` 등 | object key·파생본 필드에서 만들어지는 응답; 독립 엔티티 아님 |
| 음원 추출 / **AudioExtraction** | 상태 설명 “음원 추출 중”, 기존 `transcoding_status` adapter | `AudioWorker`, `ProcessingSpec`, `media_processing` | `B/media/{id}/processing`, `B/personal-logs/{id}/processing` | `Media`·`PersonalLog`의 `transcoding_status`, `processing_*`, `audio_*` |
| 라디오 재생 모드 / **RadioMode** | `MediaPlayer.isRadioMode`, 추출 음원 재생 | 추출 음원 URL 제공 | `playback.audio_url` 등 | **별도 Radio/Audio 모델 없음**. 원래 콘텐츠의 음원 소스를 선택 |
| 곡 대표 미디어 / **FeaturedMedia** | `RepresentativeMediaWorkspace`, `FeaturedMediaEditor` (`components/workspace/featured-media/FeaturedMediaEditor.jsx`), `RepresentativeMediaManagementRoute` | route의 대표 지정 → `featured_media_service.py::{set_song_featured_media,clear_song_featured_media}` | `B/featured-media`, `B/songs/{id}/featured-media`, 하위 `/candidates`; `is_featured` | `Media.is_featured`; `(band_id,song_id)` 부분 고유 인덱스 |
| 미디어 투표 / **MediaVote** | 추천/아쉬워요, `mediaVoting`, `voteTarget` | v2 route → `v2_vote_service.py::set_media_vote`; legacy는 별도 `vote_service.py` | `B/media/{mediaId}/vote`; v2 요청 `{value}` | `MediaVote` / `media_vote` (M:673) |
| 다음 곡 제안 / **SongSuggestion** | 현재 탭 “추천곡” (과거 표현 “다음 곡 추천”), `SuggestionsWorkspace` (`components/workspace/suggestions/SuggestionsWorkspace.jsx`) | `song_suggestion_service.py`의 CRUD·채택, `serialize_suggestion` | `B/suggestions`, `B/suggestions/{id}` | `SongSuggestion` / `song_suggestion` (M:783) |
| 곡 제안 투표 / **SongSuggestionVote** | 제안의 추천/아쉬워요 | v2 route → `v2_vote_service.py::set_song_suggestion_vote` | `B/suggestions/{id}/vote`; `thumbs_up/down`, `vote_score`, `viewer_vote` | `SongSuggestionVote` / `song_suggestion_vote` (M:884) |
| 곡 제안 채택 / **ProceedSongSuggestion** | “이 곡 진행”, `suggestionProceed` | route `proceed_suggestion_v2` → 업무 `proceed_song_suggestion` | `POST B/suggestions/{id}/proceed` | `Song` 생성 후 `SongSuggestion` 제거; 별도 승인 모델 없음 |
| 댓글·답글 / **Comment / CommentReply** | `MediaEngagement`, `MediaDiscussion`, `buildCommentThreads` | `comment_service.py`의 생성·수정·삭제; route callback의 `serialize_comment`·`serialize_personal_comment` | `B/media/{id}/comments`, `B/personal-logs/{id}/comments`, `B/comments/{id}` | `Comment` / `comment` (M:1091); 답글은 `parent_id`, 별도 Reply 테이블 없음 |

반드시 유지할 의미:

- `Song`은 연습·연주할 레퍼토리 항목이다. `artist`는 곡의 아티스트 정보이지 업로더·멤버 이름이 아니다. `sheet_music`는 악보 첨부 정보이며 별도 악보 모델은 없다.
- `Media.song_id`는 필수이고 `rehearsal_id`는 선택이다. 일반 파일 처리 helper에서 쓰는 media라는 말이 모두 이 DB 모델을 뜻하지는 않는다.
- 원본 영상·720p 영상·추출 M4A는 **한 Media에 연결된 재생 소스**일 수 있다. 파일 세 개가 있다고 DB 미디어 세 개를 생성한 것으로 해석하지 않는다. `PersonalLog`도 추출 음원을 가질 수 있지만 곡 Media와 다른 레코드다.
- 대표 미디어는 곡마다 영상 또는 음원 **합쳐서 최대 하나**다. 대표 지정 해제는 콘텐츠나 파일 삭제가 아니다. 점수순 정렬과도 독립적이다.
- 투표 값은 `+1 / -1 / 취소 0`이다. DB 투표 행은 ±1만 보관하는 구조이며, `0`은 API에서 취소를 표현한다. `vote_score`는 찬성-반대, `viewer_vote`는 현재 이용자의 선택이다.
- 곡 제안의 `thumbs_up/down`과 미디어의 `upvote_count/downvote_count`는 대응하는 집계지만 wire 이름을 통일할 이유만으로 바꾸지 않는다. 곡 목록 자체를 이 점수로 정렬하는 기능이라고 해석하지 않는다.
- 곡 제안 채택은 `title/artist/link/memo`를 복사한 `Song`을 만들고 제안을 제거한다. 단순 `status=approved` 업데이트가 아니다.
- 댓글의 `timestamp_seconds`는 재생 위치다. `created_at`의 시간대와 다르며, `author` 표시 문자열을 권한 ID로 사용하지 않는다.

### 2.3 일정·연습 기록·사진·공지

| 권장 업무 명칭 / 영문 | 현재 Frontend 명칭 | 현재 Backend 명칭 | API / JSON | DB Model / Table |
|---|---|---|---|---|
| 합주·공연 일정 / **Rehearsal** | `workspace/rehearsals/{RehearsalWorkspace,RehearsalDetail,RehearsalForm}.jsx`; 과거 화면에 `RehearsalCalendar`, `RehearsalModal` | `rehearsal_service.py`의 생성·수정·삭제·곡 연결, `serialize_rehearsal` | `B/rehearsals`, `B/rehearsals/{id}` | `Rehearsal` / `rehearsal` (M:956) |
| 일정 유형 / **RehearsalType** | `REHEARSAL_TYPES`, `RehearsalTypeBadge` | `validate_rehearsal_type`, `REHEARSAL_TYPES` | `rehearsal_type` | `Rehearsal.rehearsal_type` |
| 일정 장소 / **RehearsalLocation** · 장소 검색 결과 / **PlaceSearchResult** | `BandPlacePicker`, 기존 `LocationPicker` | `place_search_service.py::search_places_for_member`, `place_search.py`의 guard·결과 정리·NAVER 연동 | `GET B/search-places?query=...`; 결과 배열 | 일정의 `location/latitude/longitude`; **별도 Place/Venue 모델 없음** |
| 일정에 연결된 곡 목록 / **RehearsalSongs** | 일정 `song_ids`, 응답 `songs` | `Rehearsal.songs`, `Song.rehearsals` | 일정 쓰기의 `song_ids`, 읽기의 `songs` | 다대다 연결 `rehearsal_songs` (M:950) |
| 합주 가능 일정 / **MemberAvailability** | `AvailabilityWorkspace`, `AvailabilityCalendar`, `availabilityState` | `availability_service.snapshot/update_days` | `B/availability`, `/me/batch`, `/me/{date}`, `/reset` | `BandAvailabilityDay` / `band_availability_day` (M:150) |
| 일정 조율 후보 / **AvailabilityCandidate** | 날짜·시간대별 가능/불가/미정 인원 집계 | 가능 일정 데이터 제공 | FE 집계용 `{date,period,available,unavailable,unknown,all,total}` | **저장 모델 없음**. UI 계산 결과 |
| 개인 일정 메모 / **PersonalScheduleNote** | 가능 일정 화면 메모 | `availability_service.update_note` | `B/availability/me/notes/{date}/{period}` | `BandPersonalScheduleNote` / `band_personal_schedule_note` (M:169) |
| 개인 연습 기록 / **PersonalLog** | `MemberDetailWorkspace`, `PersonalLogPlayback` | `serialize_personal_log`, 개인 기록 업로드 | 조회 `B/members/{id}/logs`; 삭제 `DELETE B/personal-logs/{id}` | `PersonalLog` / `personal_log` (M:836) |
| 갤러리 사진 / **GalleryImage** | `workspace/gallery/GalleryWorkspace.jsx` (`BandWorkspace.jsx`에서 import·재수출), `BandUploadInput`은 UploadContext가 있을 때만 `FileUpload`를 렌더링 | `serialize_gallery_image` | 조회 `B/gallery`; 삭제 `DELETE B/gallery/{id}` | `GalleryImage` / `gallery_image` (M:1031) |
| 밴드 대표 사진 / **FeaturedGalleryImage** | `FeaturedGalleryManager`, 소개의 `featured_gallery` | read query·`gallery_featured_service.py::set_gallery_featured` | `B/gallery/featured`, `B/gallery/{id}/featured` | `GalleryImage.is_featured`; 밴드당 최대 하나 |
| 밴드 로고 / **BandLogo** | `BandLogoManagementRoute`, `bandLogoApi`, 헤더 로고 | `band_logo_service.py::set_band_logo`, 전용 관리 조회 | `PUT B/logo`; `/api/v2/bands/by-slug/{slug}/logo-management`; `{gallery_image_id}` | `BandLogo` / `band_logo` (M:1075); 갤러리 사진 참조 |
| 밴드 공지 / **Announcement** | `AnnouncementEditor` (`components/workspace/announcements/AnnouncementEditor.jsx`), 대시보드 공지 | `announcement_service.py::{put_announcement,clear_announcement}`, `serialize_announcement` | 단수 `B/announcement` | `Announcement` / `announcement` (M:1010); 밴드당 하나 |
| 밴드 소개문 / **BandDescription** | 소개·관리 폼의 `description` | `update_band_profile` | `PATCH B/profile`, JSON `description` | `Band.description` |
| 밴드 소개 페이지 / **BandIntroduction** | `BandIntroduction`, `BandIntroductionRoute` | `invite_introduction`, 소개 집계 응답 | `/api/v2/bands/by-slug/{slug}/introduction` | Band·대표 사진·곡 집계·공지의 **조회 구성**, 별도 소개 모델 없음 |

보충:

- 공연은 현재 `LIVE` 일정이다. `Performance`, `Gig`, `BandEvent`를 **이미 구현된 별도 엔티티**로 사용하지 않는다. 업무 설명은 “합주·공연 일정”, 코드 계약 명칭은 기존 `Rehearsal`을 유지한다.
- 현재 `rehearsal_songs`에는 곡 순서 필드가 없다. 이를 순서 보장되는 “셋리스트” 기능이라고 약속하지 않는다.
- 가능 일정은 **날짜 × 밴드 가입 관계 × 오전/오후/저녁** 상태다. `member_id` 프로필 대신 `membership_id`를 사용하는 점이 중요하다. 후보 집계는 예약·출석을 보장하지 않는다.
- 가능 일정 API의 `members[].membership_id`는 `BandMembership` ID이며 `display_name`은 동일 밴드에 연결된 `MemberProfile.name`을 우선하고 없으면 메일형을 제외한 계정 표시명 → `멤버 #id` 순으로 대체하며, 프로필 ID로 일정을 연결하지 않는다.
- 개인 일정 메모는 개인 연습 기록 `PersonalLog`와 무관하다. “개인 기록”이라고만 지시하지 말고 어느 것인지 적는다.
- 로고는 같은 밴드 갤러리 사진을 가리키는 관계다. 대표 사진과 같을 수도, 다를 수도 있으며 선택할 때 파일을 복제하는 개념이 아니다.

## 3. 화면·탐색 용어: DB 엔티티와 분리

근거: `frontend/src/App.jsx:96` 이후 라우트, `frontend/src/components/community/CommunityBoardPage.jsx:5`.

| 권장 명칭 | 현재 화면 코드 / URL | 정의 및 혼동 방지 |
|---|---|---|
| 서비스 시작 화면 / WelcomeLanding | `WelcomeLanding`, `/`, `/login` | 비로그인 소개·로그인. 특정 밴드 브랜드가 아님 |
| 서비스 포털 / Portal | `PortalPage`, `/portal` | 로그인 후 서비스 소개·커뮤니티 진입 화면. 계정 관리와 분리 |
| 계정 관리 / Account | 현재 `BandsPage`, `/account` | 계정과 가입 밴드·신청 상태 등을 관리. `/bands`는 이곳으로 호환 redirect |
| 밴드 공간 / BandWorkspace | `BandWorkspaceRoute`, `/bands/{bandSlug}/...` | 선택한 밴드 안의 기능 묶음. `/band/{slug}/...`는 복수형 경로로 redirect하는 별칭 |
| 밴드 대시보드 / BandDashboard | `BandDashboard`, `/bands/{slug}` | 사진·공지·다가오는 일정 등 밴드 활동 요약. `B/dashboard` 집계 조회 사용 |
| 밴드 관리 / BandManagement | `BandManageRoute`, `/bands/{slug}/manage` | 밴드 프로필·가입·초대·권한 등 운영 화면 |
| 밴드 소개 / BandIntroduction | `/bands/{slug}/about` | 공개 소개용 조회 화면. 설명 텍스트 `BandDescription`과 구별 |
| 밴드 가입 요청 / BandJoinRequest | `/bands/{slug}/join` | 같은 소개 구성을 재사용하고 가입 이름·소개·포지션을 입력 |
| 공개 밴드 찾기 / BandDiscovery | `DiscoverBandsPage`, `/bands/discover` | 밴드 검색. 가입 신청 입력 화면과 분리 |
| 밴드 구인구직 / RecruitingBoard | `CommunityBoardPage`, `/community/recruiting` | FE 준비중 틀만 존재. 구인 글 모델·쓰기 API 없음 |
| 함께 공연해요 / TogetherBoard | 같은 컴포넌트, `/community/together` | FE 준비중 틀. 실제 밴드 `Rehearsal` 공연 일정과 별개 |
| 연습실·공연 후기 / ReviewsBoard | 같은 컴포넌트, `/community/reviews` | FE 준비중 틀. 장소·후기·게시글 모델 없음 |

`components/dashboard/Dashboard.jsx`, `members/MemberDashboard.jsx`, `songs/SongSuggestion.jsx`, `context/SongContext.jsx` 같은 과거 화면 코드도 남아 있다. 현재 App에서 직접 라우팅하지 않는 것과 삭제해도 된다는 것은 다르다. `MediaPlayer`처럼 현재 workspace에서도 사용하는 공용 컴포넌트는 실제 호출처로 구분한다. `RehearsalCalendar`·`RehearsalModal`은 과거 Dashboard의 호출 경로이며, 현행 일정 폼은 `workspace/rehearsals/RehearsalForm.jsx`다.

## 4. 겹치는 이름과 중요한 필드 경계

### 4.1 같은 `id`, `member`, `user`여도 같은 대상이 아니다

| 현재 명칭 | 실제 대상 | 앞으로 구분할 로컬 명칭 |
|---|---|---|
| `AppUser.id`, DB FK `user_id`, `linked_user_id` | 앱 내부 정수 계정 ID | `appUserId` |
| Supabase `user.id`, JWT subject, `AppUser.supabase_user_id`, 공개 계정 JSON `id` | 인증 주체 UUID 문자열 | `supabaseUserId` 또는 `authSubject` |
| `BandMembership.id` | 가입 관계 ID | `membershipId` |
| `Member.id`, `/members/{id}`, 개인 기록 `member_id` | 밴드별 프로필 ID | `memberProfileId`; 외부 키는 `member_id` 유지 |
| 밴드 응답의 `members` (`band_dict(include_roster=True)`) | 멤버십 DTO 목록 | `memberships` 또는 `membershipRoster`로 로컬 구분 |
| `B/members`의 항목 | 멤버 프로필 DTO 목록 | `memberProfiles` |
| FE `useAuth().user` | Supabase 세션 사용자 | 필요한 경계에서 `authUser`; 서버 AppUser와 동일 객체로 취급 금지 |

근거: `backend/models.py:9`, `backend/band_service.py:129`, `backend/band_service.py:176`, `backend/v2_remaining_read_serializers.py:67`, `frontend/src/context/AuthContext.jsx:93`.

### 4.2 표현이 같아도 통합하면 안 되는 개념

| 겹치는 표현 | 분리할 용어 | 이유 |
|---|---|---|
| 소개 | `BandDescription` / `BandIntroduction` / `JoinIntroduction` / `MemberBiography` | 밴드 텍스트·조회 화면·가입 심사 자료·프로필 소개로 수명과 공개 대상이 다름 |
| 추천 | `SongSuggestion` / `MediaVote` / `SongSuggestionVote` / `FeaturedMedia` | 제안 작성·평가·수동 대표 선정은 서로 다른 행동 |
| 대표, `is_featured` | `featuredMedia` / `featuredGalleryImage` | 곡당 하나와 밴드당 하나로 제약 범위가 다름. BandLogo는 별도 관계 |
| 역할 | `BandRole` / `MemberPosition` | 권한과 음악적 역할의 차이 |
| 일정·달력 | `Rehearsal` / `MemberAvailability` / `PersonalScheduleNote` | 확정 일정·가능 상태·선택 메모의 차이 |
| availability | `memberAvailability` / `slugAvailability` | 합주 시간 조율과 URL 중복 확인은 별개 |
| file | `sourceFile` / `playerMedia` / `objectKey` | 브라우저 File·재생 DTO·저장 객체 식별자를 구분 |
| title, filename, name | `songTitle` / `mediaDisplayName` / `originalFilename` / `objectKey` | 곡 제목·사용자 표시명·원래 파일명·저장 경로는 서로 대체 불가 |
| session | `authSession` / `uploadSession` / `inviteResumeContext` | 로그인 세션·업로드 권한 경계·초대 복귀 문맥은 별개 |
| processing / transcoding | `audioExtraction` / `video720Conversion` / `uploadCompletion` | M4A 추출·영상 변환·전송 완료를 구별. 전송 완료만으로 추출 완료는 아님 |
| visibility | `visibility` / `effectiveVisibility` / 일정 메모 공개 범위 | 자기 설정과 부모·밴드 정책을 합친 실제 읽기 범위가 다름 |
| 삭제·차단·탈퇴 | `contentDeletion` / `contentRestriction` / `leaveBand` / `removeMembership` | 파일 삭제·노출 제한·본인 탈퇴·관리자 추방은 다른 행동 |

`filename`, `object_key`, `original_filename`, `video_720_*`, `audio_*`, `original_pruned_at`는 저장·재생·삭제·감사 호환성에 연결된다. 표시명 문제를 해결한다며 기계적으로 합치거나 지우면 안 된다. URL은 만료 가능한 재생 권한 표현이고 영구 객체 식별자는 아니다.

### 4.3 같은 `status`를 하나의 enum으로 통합하지 않는다

| 대상 | 현재 값 / 해석 |
|---|---|
| 밴드 가입 관계 | `pending`, `active`, `rejected`, `left`. 탈퇴와 관리자 추방 모두 최종 `left`이며 행동은 감사 기록으로 구분 |
| 밴드 생성 요청 | `pending`, **`active`**, `rejected`. 승인 상태를 코드에서 임의로 `approved`로 보내면 안 됨 |
| 생성 요청 메일의 결정 | `approved`, `rejected`. 위 요청 status와 다른 필드 |
| 곡 진행 | `Practice`, `Completed`, `OnHold` |
| 일정 유형 | `REGULAR` 정기합주, `EXTRA` 추가합주, `LIVE` 라이브공연, `OTHER` 기타, `UNCLASSIFIED` 미분류 |
| 가능 여부 / 시간대 | `AVAILABLE`, `UNAVAILABLE`, `UNKNOWN` / `morning`, `afternoon`, `evening` |
| 콘텐츠 공개 범위 | `PUBLIC`, `MEMBERS`; `effective_visibility`는 부모·밴드 범위까지 반영 |
| 개인 일정 메모 공개 범위 | `PRIVATE`, `BAND`. 위 enum과 억지 통합 금지 |
| 음원 추출 | `not_required`, `queued`, `processing`, `completed`, `failed` |
| 직접 업로드 세션 | `initiated`, `completing`, `expiring`, `completed`, `expired`, `failed` |
| 멀티파트 업로드 세션 | 직접 업로드와 별도 상태기계. 중단 처리에는 `aborting/aborted`도 사용 |
| 업로드함 작업 | `preparing`, `queued`, `uploading`, `retry_wait`, `completing`, `processing`, `auth_required`, `completed`, `failed`, `cancelled` 등 transport 상태 |
| 곡 제안 응답 `status` | 현재 serializer가 주는 `pending` 상수. **DB 상태 칼럼이 아님** |

근거: `backend/models.py:135`, `:217`, `:265`, `:772`; `backend/voc_validation.py:17`; `backend/availability_service.py:13`; `backend/multipart_upload_service.py:97`; `backend/v2_remaining_read_serializers.py:182`; `frontend/src/services/uploadTransport.js:1`.

## 5. Type / Interface 및 DTO의 실제 존재 여부

### 웹·API 응답

- 웹 `src/`는 JS/JSX이며 `Song`, `Band`, `MemberProfile` 등의 선언된 TypeScript `type/interface`는 없다. `capacitor.config.ts`는 설정 파일이지 도메인 타입 정의가 아니다.
- `Band`, `BandMembership`, `BandCreationRequest` 응답은 `band_dict`, `membership_dict`, `creation_request_dict`가 만든 Python dict다 (`backend/band_service.py:129`, `:176`, `:215`).
- `Song`, `Media`, `Rehearsal`, `Comment` 응답은 `serialize_song/media/rehearsal/comment`가 만든 dict다 (`backend/v2_content_serializers.py:183`, `:209`, `:232`, `:274`).
- 프로필·개인 연습 기록·사진·공지·제안은 `serialize_member/personal_log/gallery_image/announcement/suggestion` dict다 (`backend/v2_remaining_read_serializers.py:67`, `:90`, `:137`, `:153`, `:164`).
- FE `bandWorkspacePaths`, `songPayload`, `suggestionPayload`, `rehearsalPayload` 등이 이 계약을 사용하는 경계다 (`frontend/src/services/bandWorkspaceApi.js:5`, `:55` 이후).
- `MediaPlayback`은 서버의 `playback` 응답, `PlaybackMedia`는 이를 `MediaPlayer`의 `{type,name,url,audio_url,...}` 형태로 변환한 FE 재생 DTO를 가리키는 문서상 명칭이다. 같은 데이터 경계의 양쪽 표현이지 두 콘텐츠 엔티티가 아니다.
- `PlaybackMedia`, `MemberProfile`, `AvailabilityCandidate`는 이 문서에서 구분하는 **업무·데이터 형태 명칭**이다. TS 타입이 이미 선언됐다고 가정하지 않는다.
- v1 `Model.to_dict()`와 v2 serializer는 공개 범위·권한·파일명/서명 URL 계약이 다르다. 이름이 비슷하다는 이유로 단순 병합하지 않는다 (`backend/v2_content_serializers.py:1`).

### 실제 명명 타입·구조체

| 현재 Type / Interface | 연결 개념 / 성격 | 근거 |
|---|---|---|
| `VerifiedIdentity` | 검증된 인증 주체; 앱 계정 모델과 별도 dataclass | `backend/auth_service.py:55` |
| `RequestAudience`, `MembershipAudience`, `BandReadScope` | 요청자·활성 가입 관계·밴드 읽기 범위 스냅샷 | `backend/band_access.py:28`, `:43`, `:56` |
| `VoteResult` | legacy 곡·미디어 공통 투표 트랜잭션 결과; 모든 v2 투표의 반환 타입은 아님 | `backend/vote_service.py:13` |
| `MultipartTargetSpec` | 곡 미디어/개인 기록 업로드 대상별 설정 | `backend/multipart_upload_service.py:28` |
| `ProcessingSpec` | 음원 추출 대상 설정 | `backend/media_processing.py:33` |
| `PreparedUpload` | 폼 업로드 준비 결과 | `backend/upload_helpers.py:8` |
| `BackgroundUploadState`, `DurableUploadFile`, `IOSUploadTask`, `IOSPartAttempt` | 저장소에 남아 있는 iOS 업로드 기술 계약 | `frontend/ios/App/App/BackgroundUpload/BackgroundUploadFoundation.swift:12`, `:53`, `:250`, `:280` |
| Java `UploadStore.Task`, `UploadEngine.Progress` | Android 업로드 작업 데이터와 전송 진행 callback. 업무 엔티티 아님 | `frontend/android/app/src/main/java/com/deutteun/archive/UploadStore.java:120`, `UploadEngine.java:194` |

네이티브 코드의 존재는 설치형 앱이 출시·운영 중이라는 뜻이 아니다. 현재 요청 대상은 웹이며, 웹 transport는 `supportsBackground: false`다 (`frontend/src/services/uploadTransport.js:79`). 네이티브 계약을 웹의 백그라운드 업로드 보장으로 해석하지 않는다.

## 6. 업로드·운영·정책 보조 도메인: 나머지 모델 전체 목록

아래는 핵심 콘텐츠와 다른 수명·권한을 가진 지원 모델이다. “용어 통일”을 이유로 핵심 콘텐츠 모델에 합치지 않는다. M은 `backend/models.py`다.

| 개념 / 권장 명칭 | 현재 Model / Table | FE·BE·API 의미 및 유지 이유 |
|---|---|---|
| 업로드 작업 / UploadTask | 웹은 DB 모델 없음; FE `UploadContext`, `bandUploadTaskStore`; 네이티브는 로컬 `tasks` | 업로드함의 진행 상태. 서버 업로드 세션과 다름. 웹 작업 저장은 File 자체 복원을 보장하지 않음 |
| 곡 미디어 재개 업로드 세션 / MediaMultipartUploadSession | `MultipartUploadSession` / `multipart_upload_session` (M:697) | `B/uploads/multipart/...`; `MultipartTargetSpec.kind=media`. 기존 테이블명 유지 |
| 곡 미디어 업로드 파트 / MediaMultipartUploadPart | `MultipartUploadPart` / `multipart_upload_part` (M:726) | 세션의 `part_number`, etag, ack. 완성 콘텐츠가 아님 |
| 개인 기록 재개 업로드 세션 / PersonalLogMultipartUploadSession | 동명 / `personal_log_multipart_upload_session` (M:913) | 공통 multipart 서비스이지만 저장 테이블은 곡 미디어와 별개 |
| 개인 기록 업로드 파트 / PersonalLogMultipartUploadPart | 동명 / `personal_log_multipart_upload_part` (M:938) | 같은 파트 프로토콜이지만 FK·테이블 호환 유지 |
| 직접 업로드 세션 / DirectUploadSession | 동명 / `direct_upload_session` (M:739) | `B/uploads/direct/...`, `B/gallery/direct/...`; kind `media/personal_log/gallery`. multipart R2 upload ID와 구분 |
| 업로드 capability / UploadCapability | 세션 `capability_token_hash`; 독립 모델 없음 | `X-Upload-Capability`, 최초 발급 토큰. 로그인 JWT도, 세션 ID도, 초대 토큰도 아님 |
| 저장 용량 한도·예약 / StorageQuota / UploadReservation | `Band.storage_quota_bytes`, 세션의 `declared_bytes` | 진행 중 업로드를 포함한 예약 계산. 별도 Quota/Reservation 테이블이 있다고 가정하지 않음 |
| 삭제 작업 / R2DeletionTask | 동명 / `r2_deletion_task` (M:281) | `B/deletion-tasks/{id}`, platform 삭제 API, `deletion_service/worker`; 파일 정리와 완료 확인을 위한 내구성 작업 |
| 콘텐츠 일시 차단 / ContentRestriction | 동명 / `content_restriction` (M:446) | 권리 요청 등의 차단·해제. `active/released`, 사유 copyright/privacy/portrait/other_rights. 삭제와 별개 |
| 감사 이벤트 / AuditEvent | 동명 / `audit_event` (M:425) | 가입·권한·콘텐츠 관련 행위 기록. 개인 연습 기록 PersonalLog와 다름 |
| 가입 약관·연령 확인 기록 / PolicyAcceptanceReceipt | 동명 / `policy_acceptance_receipt` (M:47) | `/api/v2/me/policy-acceptance`; 약관 버전·연령 확인. 업로드 권리 확인과 별개 |
| 업로드 권리 확인 / UploadPolicyConfirmation | **별도 UploadConsent 모델 없음** | `policy_service.py:204` 이후, 감사 이벤트 `upload_policy_confirmed`와 version/batch/rights/visibility 확인. 서명 URL·본인 인증과 다른 개념 |
| 생성 요청 결정 메일 대기열 / BandRequestEmailOutbox | 동명 / `band_request_email_outbox` (M:221) | 결정 알림 발송 기반. 테이블 존재가 실제 메일 발송 활성화를 의미하지 않음 |
| 가능 일정 전체 초기화 상태 / AvailabilityResetState | `BandAvailabilityState` / `band_availability_state` (M:140) | reset generation과 기록. 오전/오후/저녁 값 자체를 담는 Day 모델과 분리 |
| 최초 운영자 등록 표식 / AuthBootstrapState | 동명 / `auth_bootstrap_state` (M:38) | 초기 관리자 등록 제어. 매 요청의 권한을 이 표식만으로 판별하지 않음 |
| 구 초대 코드 / LegacyBandInviteCode | `BandInviteCode` / `band_invite_code` (M:333) | 기록 보존. 구 발급·가입 라우트는 410 (`routes/bands.py:554`). 현행 초대 링크와 혼용 금지 |
| 구 밴드 소유권 수령 / LegacyBandClaimToken | 동명 / `legacy_band_claim_token` (M:389) | 단일 밴드 이전의 최초 소유권 수령용 보존 모델. 일반 가입·현재 소유권 이전 기능과 다름 |
| 구 데이터 밴드 귀속 상태 / TenantBackfillState | 동명 / `tenant_backfill_state` (M:482) | 데이터 이전 작업의 상태. 별도 사용자용 Tenant/Team 엔티티 아님 |
| 구 곡 자체 투표 / LegacySongVote | 현재 `SongVote` / `song_vote` (M:566) | 과거 곡 단위 투표. legacy handler도 남음 (`routes/songs.py:137`). 신규 MediaVote와 다르며 삭제·410이라고 단정 금지 |

저장소의 SQLAlchemy 모델 **36개**는 2절과 이 절에서, 별도 다대다 연결 테이블 `rehearsal_songs`는 2.3절에서 다룬다. Alembic의 `alembic_version`은 마이그레이션 원장이지 업무 엔티티가 아니다. R2는 객체 저장소이고 별도의 콘텐츠 Domain/DB 모델 명칭이 아니다.

## 7. 기존 명칭 → 권장 명칭 및 rename 판단

**이번 분석으로 즉시 실행할 rename은 없다.** 아래 “정리 권고”는 다음 관련 개발에서 내부 식별자를 고칠 후보이며, DB/API migration 승인이 아니다. 의미 설명만으로 충분한 이름을 억지로 바꾸지 않는다.

| 기존 명칭·표현 | 앞으로 사용할 명칭 | 실제 코드 rename 판단 |
|---|---|---|
| 계정·가입 관계·프로필을 모두 `member` | `appUser/authUser`, `membership`, `memberProfile` | **경계의 모호한 로컬 변수·함수 인자는 정리 권고**. 기존 DB `Member`와 `/members` 유지 |
| `/account`의 `BandsPage` | 계정 관리 / `AccountPage` 또는 `AccountHubPage` | **FE 내부 파일·export rename 우선 후보**. 현재 화면 의미와 이름이 다름. `/bands` 호환 redirect 유지 |
| `RepresentativeMediaWorkspace` 등 representative/featured 혼용 | 대표 미디어 / `FeaturedMedia` | FE 내부를 손댈 때 `FeaturedMediaWorkspace`, `FeaturedMediaManagement`로 정리 가능. 기능이 명확하므로 긴급 전면 rename 불필요 |
| 문맥 없는 `featured` | `featuredMedia` 또는 `featuredGalleryImage` | **동시에 다루는 화면·서비스의 로컬명은 구분 권고**. 각 `is_featured` 칼럼·API는 범위가 달라 그대로 유지 |
| MediaPlayer의 prop `file` | `playerMedia` 또는 `playbackMedia` | 실제 File과 동시에 쓰는 경계부터 내부 rename 권고. 공용 player 전체 변경은 별도 회귀 검증 필요 |
| 포지션을 `instrument`만으로 표현 | 연주 포지션 / `positions`, `customPosition` | 새 구조는 이미 존재. **호환 필드 instrument는 유지**, 데이터 전면 rename 불필요 |
| `transcoding`이라는 일반 설명 | `audioExtraction` 또는 `video720Conversion` | 신규 설명·로컬명 개선. 기존 `transcoding_status`, `processing` API, 저장 상태 literal은 유지 |
| “공연” → 별도 Performance 모델 추정 | 합주·공연 일정 / `Rehearsal`, 유형 `LIVE` | **모델·테이블·URL rename 불필요**. 현재 저장 의미를 문서·라벨로 명확히 함 |
| “추천곡/추천” 전반 | 다음 곡 제안 / `SongSuggestion`, 대상별 Vote, 대표 선정 / FeaturedMedia | 대상별 작업 지시만 정확히 함. `/suggestions`, `/vote`, `/featured-media` 유지 |
| `thumbs_up/down` vs `upvote_count/downvote_count` | 신규 로컬 계산은 `upvoteCount/downvoteCount`, 점수 `voteScore` | 기존 JSON/칼럼은 외부 계약이므로 유지. adapter에서만 명확히 대응 |
| 밴드 영어명 | 밴드 주소명 / `BandSlug` | 설명 개선 후보. **slug 필드·라우트 유지**; 별도 영어 이름 모델을 만들지 않음 |
| URL 중복 확인 로컬 `availability` | `slugAvailability` | **해당 로컬 state rename 권고**. 합주 가능 일정과 이름 충돌 방지. API suffix는 유지 가능 |
| 이름·소개·ID·상태의 문맥 없는 변수 | `memberName/joinIntroduction/...`, 도메인별 ID·상태 | 여러 Domain을 넘는 함수 경계에서만 명료화. 모든 지역 `name/status`를 일괄 치환하지 않음 |
| 팀 / Team / Tenant | 밴드 / `Band` | 신규 업무 표현은 Band. 마이그레이션의 `tenant_*`, band-scope 기술 용어는 유지 |
| PersonalLog를 단순 “로그” | 개인 연습 기록 / `PersonalLog` | 이름 자체는 유지. AuditEvent·개인 일정 메모와 설명만 분리 |

### 호환성 때문에 보존할 이름

- `Member/member/member_id`: 프로필·개인 기록·기존 프로필 연결·마이그레이션에 걸친 계약이다. 더 명확한 업무명 `MemberProfile`을 쓰는 것만으로 모델·테이블 전면 변경이 필요하지 않다.
- `Rehearsal/rehearsal/rehearsal_id/rehearsal_songs`: 공연도 포함하는 현재 의미를 명시하면 된다. 새 Event 모델로 재설계하는 것은 별도 제품 결정이다.
- `/api/v2/bands/...`, `/members`, `/rehearsals`, `/suggestions`, `/featured-media`, JSON 키·enum literal: 웹·남아 있는 네이티브 코드·테스트·운영 스키마와 호환된다. 바꾼다면 버전/adapter/마이그레이션을 따로 설계한다.
- `filename/object_key` 및 파생본 필드, 업로드 세션·파트 테이블, legacy 투표·초대·이전 표식: 데이터 수명과 복구·감사·보안에 영향을 준다. 이름 정리만으로 삭제·병합하지 않는다.
- 현재 명확한 `Song`, `Band`, `Comment`, `GalleryImage`, `Announcement`, `BandMembership`, `BandInviteLink`는 유지한다. 집합/항목, 소속 scope가 문맥에서 명확하면 매번 Band 접두사를 추가할 필요가 없다.

## 8. 앞으로 Agent에게 작업을 지시하는 예시

- “`BandJoinRequest`의 필수 이름이 승인 후 `MemberProfile`의 `name`에 반영되는 흐름을 수정해줘. `AppUser.display_name`은 변경하지 마.”
- “`MediaVote` 점수로 곡 내부 미디어를 정렬해줘. `Song` 목록 순서와 `FeaturedMedia` 지정은 유지해.”
- “라이브공연은 현재 `Rehearsal.rehearsal_type=LIVE`로 처리해줘. 별도 Performance 모델은 만들지 마.”
- “`MemberAvailability`에서 오전·오후·저녁을 선택하게 해줘. 실제 `Rehearsal`을 자동 생성하거나 예약하지 마.”
- “`FeaturedGalleryImage`는 유지하고 `BandLogo` 선택만 바꿔줘.”
- “owner와 적격 플랫폼 superadmin에게 이 기능을 허용하되, 기존 모든 API에 포괄 우회 권한을 추가하지 마.”
- “`AudioExtraction` 실패 표시를 고쳐줘. 720p 재변환이나 R2 원본 삭제는 하지 마.”
- “`AccountPage`에 설정을 추가해줘. 서비스 `Portal`과 `BandDashboard`는 변경하지 마.”

## 9. 근거 탐색 안내와 유지 규칙

위 파일 경로는 `E:\Anything\band-archive` 기준이다. 줄 번호는 분석 시점 기준이며 코드 변경 후 달라질 수 있다. 근거를 갱신할 때 이름뿐 아니라 serializer·라우트·호출처·제약을 함께 확인한다.

- 계정·권한·가입: `backend/models.py:9`, `backend/auth_service.py:55`, `backend/band_access.py:28`, `backend/band_service.py:129`, `backend/routes/bands.py:75`.
- 프로필·포지션·가입 이름: `backend/member_profile_service.py:32`, `backend/voc_validation.py:8`, `frontend/src/components/workspace/MemberProfile.jsx:8`, `frontend/src/components/bands/BandJoinRequest.jsx:31`.
- 미디어·곡·투표·대표: 읽기는 `backend/routes/content_reads.py`, `v2_content_serializers.py::serialize_song/serialize_media`와 `media_access_service.py::prepare_media_access/media_playback`; 곡 쓰기는 `song_service.py::create_song/update_song/delete_song`; 투표는 `v2_vote_service.py::set_media_vote/set_song_suggestion_vote`; 대표 선정은 `featured_media_service.py::set_song_featured_media/clear_song_featured_media`를 따른다. FE 요청 경계는 `frontend/src/services/mediaVoting.js`·`bandWorkspaceApi.js`다.
- 일정 조율: `backend/routes/availability.py:10`, `backend/availability_service.py:13`, `frontend/src/components/workspace/availabilityState.js:1`, `frontend/src/services/rehearsalTypes.js:1`.
- 장소 검색: `backend/place_search.py:12`, `backend/routes/bands.py:539`, `frontend/src/components/workspace/BandPlacePicker.jsx:10`. 검색 결과와 장소 후기를 같은 구현으로 간주하지 않는다.
- 화면·소개·갤러리·로고: `frontend/src/App.jsx:96`, `frontend/src/components/bands/BandIntroduction.jsx:41`, `frontend/src/services/bandLogoApi.js:1`.
- 업로드와 추출: `backend/multipart_upload_service.py:28`, `backend/media_processing.py:33`, `frontend/src/services/uploadTransport.js:1`, `frontend/src/services/bandUploadTaskStore.js:1`.
- 정책·차단·삭제: `backend/policy_service.py:204`, `backend/content_restrictions.py`, `backend/deletion_service.py`, `backend/routes/deletion_tasks.py`.

새 기능을 추가할 때는 기존 개념의 확장인지 새 개념인지 먼저 구별한다. 실제 구현되지 않은 게시글·공연 모델·타입을 이 문서에 현재 구현처럼 기재하지 않는다. 이 문서는 보안 정책이나 공개 범위, 법적 동의, 삭제 정책의 효력을 바꾸지 않는다.
