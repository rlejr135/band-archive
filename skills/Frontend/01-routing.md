# Routing 구조

## 라우터 설정

- **라이브러리**: React Router v7 (`react-router-dom`)
- **라우터 타입**: `BrowserRouter` (with `basename={import.meta.env.BASE_URL}`)
- **BrowserRouter 위치**: `src/main.jsx`
- **Routes 정의 위치**: `src/App.jsx`

## 라우트 맵

| 경로 | 컴포넌트 | 설명 |
|------|----------|------|
| `/` | `Dashboard` | 홈페이지 - 통계 & 빠른 링크 & 합주 달력 |
| `/songs` | `SongPage` | 곡 목록 (사이드바) + 곡 상세 (메인) |
| `/songs/:id` | `SongPage` → `SongDetail` | 특정 곡 상세 보기 |
| `/suggestions` | `SongSuggestion` | 다음 연습곡 추천/투표 |
| `/members` | `MemberDashboard` | 멤버 목록 (그리드) |
| `/members/:id` | `MemberDetail` | 멤버 프로필 & 개인 연습 로그 |
| `/gallery` | `Gallery` | 갤러리 (이미지 업로드/조회/대표 설정) |
| `*` | `Navigate to /` | 폴백 (404 → 홈으로 리다이렉트) |

## 라우트 정의 코드 (App.jsx)

```jsx
// src/main.jsx:
<BrowserRouter basename={import.meta.env.BASE_URL}>
  <App />
</BrowserRouter>

// src/App.jsx (App 컴포넌트):
<SongProvider>
  <MainContent />
</SongProvider>

// MainContent 내부:
<Header />
<AnnouncementToast />
<Routes>
  <Route path="/" element={<Dashboard />} />
  <Route path="/songs" element={<SongPage />} />
  <Route path="/songs/:id" element={<SongPage />} />
  <Route path="/suggestions" element={<SongSuggestion />} />
  <Route path="/members" element={<MemberDashboard />} />
  <Route path="/members/:id" element={<MemberDetail />} />
  <Route path="/gallery" element={<Gallery />} />
  <Route path="*" element={<Navigate to="/" replace />} />
</Routes>
```

## 네비게이션

### Header 네비게이션
- `useNavigate()` 훅 사용
- 버튼 5개: 대시보드(`/`), 곡 관리(`/songs`), 멤버(`/members`), 추천(`/suggestions`), 갤러리(`/gallery`)
- 현재 경로에 따라 `.active` 클래스 토글 (`useLocation()`)

### URL ↔ Context 동기화 (SongPage)
```
URL 변경 (/songs/:id)
  → useParams()로 id 추출
  → songs 배열에서 song 객체 find
  → SongContext.selectSong(song) 호출
  → currentSong 상태 업데이트
  → SongDetail 렌더링
```

### 프로그래밍 방식 네비게이션
- `navigate('/songs')` - 곡 목록으로 이동
- `navigate(`/songs/${id}`)` - 특정 곡으로 이동
- `navigate(`/members/${id}`)` - 멤버 상세로 이동

## 레이아웃 패턴

### SongPage 2-패널 레이아웃
```
┌──────────────────────────────────────────┐
│                 Header                    │
├────────────┬─────────────────────────────┤
│  Sidebar   │       Content Area          │
│ (SongList) │  (SongDetail / SongForm)    │
│            │                             │
│  검색바     │  곡 상세 정보               │
│  곡 목록    │  미디어 관리                │
│  추가 버튼  │  연습 로그                  │
└────────────┴─────────────────────────────┘
```

### 모바일 (768px 이하)
- 곡 선택 안 됨: 사이드바(곡 목록)만 표시
- 곡 선택 됨: 콘텐츠 영역만 표시 + 뒤로가기 버튼
- CSS 클래스: `.app-main.has-selected-song`로 토글

## 새 페이지 추가 가이드

1. `src/components/{feature}/` 에 컴포넌트 생성
2. `src/App.jsx`의 `<Routes>` 안에 `<Route>` 추가
3. `MainContent`의 Header에 네비게이션 버튼 추가 (필요 시)
4. 필요한 API 함수를 `src/services/`에 추가
