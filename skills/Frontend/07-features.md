# Features (기능 상세)

## 1. 대시보드 (Dashboard)

**경로**: `/`
**컴포넌트**: `components/dashboard/Dashboard.jsx`
**Props**: `onSelectSong`, `onViewSongs`
**API**: `fetchDashboardStats()`

### 표시 내용
- 전체 곡 수 (숫자 카드)
- 곡 상태 분포 차트 (Practice / Completed / OnHold)
- 연습 팁 섹션
- 빠른 액션 버튼 (새로고침, 전체 곡 보기)

### 동작
1. 마운트 시 `fetchDashboardStats()` 호출
2. 로딩/에러/데이터 상태에 따라 렌더링

---

## 2. 곡 관리 (Songs)

**경로**: `/songs`, `/songs/:id`
**컴포넌트**: SongPage (App.jsx 인라인), SongList, SongDetail, SongForm
**Context**: `SongContext` (useSongs 훅)

### 곡 목록 (SongList)
- 전체 곡 목록 표시 (순수 props 컴포넌트)
- **대표 미디어 정렬**: `has_featured_media === true` 곡이 최상단, ⭐ 뱃지 + `.has-featured` 강조
- 검색은 SongPage에서 SearchBar + 클라이언트 사이드 필터링 후 props 전달
- 곡 클릭 → `onSelectSong(song)` → URL 변경
- 새 곡 추가 버튼 → `onAddSong()`
- 곡 삭제 → PasswordModal (클라이언트 검증) → `onDeleteSong(id)`

### 곡 상세 (SongDetail)
- 곡 정보 표시: 제목, 아티스트, 상태, 장르, 난이도, 링크, 코드, 메모
- **YouTube 임베드**: link가 YouTube URL이면 iframe 임베드 플레이어 표시 (16:9 반응형)
- 코드/메모는 항상 표시 (`<pre>` 태그), **인라인 편집** 지원 (편집 버튼 → textarea → 저장/취소)
- 수정 버튼 → `startEdit()` → SongForm으로 전환
- **미디어 관리 (아코디언 방식)**:
  - FileUpload로 파일 첨부
  - 각 미디어 항목이 아코디언(확장/축소) 방식으로 동작 — 여러 개 동시 확장 가능
  - 헤더 클릭으로 확장 토글 → 확장 시 MediaPlayer + CommentSection 인라인 표시
  - 미디어별 합주 연결 배지 표시 (📅 날짜 제목) 또는 "합주 연결" 버튼
  - 인라인 합주 연결 피커로 연동 변경/해제 가능 (`linkMediaToRehearsal`)
  - 미디어 이름 변경 (인라인 편집)
  - 미디어 삭제
  - **대표 미디어 설정**: 아코디언 바디에 "⭐ 대표로 설정" 버튼, 헤더에 ⭐ 뱃지 (곡당 1개)
### 곡 생성/수정 (SongForm)
- 순수 props 컴포넌트 (Props: `song`, `onSave`, `onCancel`)
- 폼 필드: title*, artist*, status, genre, difficulty(1-5), link (YouTube URL 전용, 검증), chords, memo
- 생성 모드: `song` prop이 null
- 수정 모드: `song` prop이 존재
- 저장 → `onSave(formData)`
- 취소 → `onCancel()`

---

## 3. 추천곡 (Suggestions)

**경로**: `/suggestions`
**컴포넌트**: `components/songs/SongSuggestion.jsx`
**API**: `fetchSuggestions()`, `createSuggestion()`, `deleteSuggestion()`, `voteSuggestion()`

### 기능
- 추천곡 목록 (score 기준 내림차순 정렬)
- 순위 표시 (#1, #2, ...)
- **YouTube 임베드**: YouTube 링크인 경우 카드 내 iframe 임베드 표시, 비YouTube 링크는 🔗 외부 링크 유지
- 투표: 👍 thumbs_up / 👎 thumbs_down
- score = thumbs_up - thumbs_down
- 추천곡 추가: 제목, 아티스트, 링크, 메모
- 추천곡 삭제: PasswordModal로 비밀번호 확인 후 삭제

### 독립적 상태
- Context를 사용하지 않음 (자체 useState로 관리)
- 컴포넌트 마운트 시 API 호출로 데이터 로딩

---

## 4. 멤버 관리 (Members)

**경로**: `/members`, `/members/:id`
**컴포넌트**: `components/members/MemberDashboard.jsx`, `MemberDetail.jsx`
**API**: `memberApi.js` 전체

### 멤버 목록 (MemberDashboard)
- 그리드 형태로 멤버 카드 표시
- 멤버 카드: 이름, 악기
- 멤버 추가 폼: 이름, 악기 입력
- 멤버 클릭 → `/members/:id`로 이동

### 멤버 상세 (MemberDetail)
- 멤버 프로필 (이름, 악기)
- 개인 연습 로그 업로드 (FileUpload)
  - 제목 입력 + 파일 선택 (audio/video)
- 업로드된 로그 그리드 표시
  - 각 로그에 MediaPlayer 연동
  - 개별 로그 삭제 가능
- 멤버 삭제 (확인 후)

---

## 5. 공지 토스트 (Announcement Toast)

**컴포넌트**: `components/common/AnnouncementToast.jsx`
**위치**: 모든 페이지 — 헤더 아래에 항상 표시
**API**: `fetchAnnouncement()`, `updateAnnouncement()`

### 기능
- 헤더 아래에 항상 표시되는 토스트형 공지 배너
- 공지 텍스트 + 인라인 편집 기능
- DB에 단일 레코드(id=1)로 유지, 누구나 수정 가능

---

## 6. 합주 달력 (Rehearsal Calendar)

**컴포넌트**: `components/calendar/RehearsalCalendar.jsx`, `RehearsalDetail.jsx`, `RehearsalModal.jsx`
**위치**: 대시보드 내부
**API**: `rehearsalApi.js` — `fetchRehearsals()`, `getRehearsal()`, `createRehearsal()`, `updateRehearsal()`, `deleteRehearsal()`

### 기능
- `react-calendar` 기반 월간 달력
- 일정 있는 날짜에 컬러 도트, 기간 일정은 배경 하이라이트
- 날짜 클릭 시 해당 날짜 일정 목록(RehearsalDetail) 표시
- 일정 추가/수정 모달(RehearsalModal): 제목, 날짜, 기간, 시간, 장소(+지도), 색상 선택, 곡 연결, 메모
- **장소 + 지도 연동**: LocationPicker(Naver Map API v3)로 주소 검색/지도 클릭 → 좌표 저장
- 장소에 좌표가 있으면 RehearsalDetail에서 네이버 지도 링크로 표시
- 월 이동 시 해당 월 데이터 자동 재조회
- **미디어 아코디언**: 연결된 미디어를 확장/축소로 열어 MediaPlayer + CommentSection 인라인 표시
- **일괄 미디어 업로드**: 여러 파일 동시 선택 → 파일별 곡 태그 → 순차 업로드 (파일별 진행률/상태 표시)

---

## 7. 파일 업로드 시스템

**컴포넌트**: `components/common/FileUpload.jsx`

### 동작 방식
1. 드래그앤드롭 또는 클릭으로 파일 선택
2. 파일 크기 검증 (최대 200MB)
3. XMLHttpRequest로 업로드 (진행률 추적)
4. 진행률 바 실시간 표시
5. 완료 시 `onUpload` 콜백 호출
6. 다중 파일 동시 업로드 지원

### 사용 위치
- SongDetail: 곡에 미디어 파일 첨부
- MemberDetail: 개인 연습 로그 (audio/video) 업로드

---

## 8. 비밀번호 보호

**컴포넌트**: `components/common/PasswordModal.jsx`

### 사용 위치
- 곡 삭제 (SongList)
- 추천곡 삭제 (SongSuggestion)

### 동작
1. 삭제 버튼 클릭 → PasswordModal 표시
2. 비밀번호 입력
3. `checkPassword` prop이 있으면 커스텀 검증, 없으면 클라이언트에서 `'admin'`과 비교
4. 검증 성공 → `onConfirm(password)` 콜백 호출
5. 곡 삭제: 클라이언트 검증만 (백엔드에 password 미전송)
6. 추천곡 삭제: 백엔드에 password 전송하여 서버 검증

### 주의사항
- 멤버 삭제는 PasswordModal 없이 `window.confirm()` 사용
- 보안 수준이 낮음 (실제 인증 시스템 아님)

---

## 9. 갤러리 (Gallery)

**경로**: `/gallery`
**컴포넌트**: `components/gallery/Gallery.jsx`
**API**: `galleryApi.js` — `fetchGalleryImages()`, `uploadGalleryImage()`, `deleteGalleryImage()`, `setFeaturedImage()`, `fetchFeaturedImage()`

### 기능
- 이미지 업로드 (FileUpload 재사용, 이미지 파일만 허용)
- 이미지 그리드 목록 표시
- 대표 이미지 설정 — 클릭 시 `PATCH /gallery/<id>/featured`, 현재 대표면 뱃지 표시
- 이미지 삭제 (confirm 후)
- **대시보드 연동**: `fetchFeaturedImage()`로 대표 이미지 1장을 대시보드 상단에 표시

### 독립적 상태
- Context 미사용 (자체 useState로 관리)
- 대시보드의 대표 이미지는 별도 `featuredImage` state

---

## 기능 확장 시 참고사항

### 새 CRUD 기능 추가 체크리스트
1. `services/` 에 API 함수 추가
2. `components/{feature}/` 에 컴포넌트 생성
3. `App.jsx`에 라우트 추가
4. 필요 시 Header에 네비게이션 버튼 추가
5. 전역 상태 필요 시 Context 추가 또는 확장
6. CSS 파일 생성 (CSS 변수 사용)
7. 로딩/에러/빈 상태 UI 구현
