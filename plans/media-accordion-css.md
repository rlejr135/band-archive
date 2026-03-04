# 미디어 아코디언 CSS 작업 내역

## 개요

`plans/media-accordion-player.md` 계획에서 **CSS 스타일링 전담** 부분만 수행.
JSX 로직 변경, 백엔드 API 추가는 별도 agent 담당.

---

## 수정 파일 및 내역

### 1. SongMedia.css — 아코디언 구조 스타일 추가

**변경 사항:**

- `.media-item`: `display: flex` 제거 → 블록 컨테이너로 전환, `border-radius` 토큰화 (`var(--radius-default)`)
- `.media-item.expanded`: 확장 시 `border-color: var(--primary-color)` 강조
- `.media-item-header` (신규): 기존 `.media-item` 내부 레이아웃을 이동 (flex row, cursor pointer, hover 배경)
- `.media-item.expanded .media-item-header`: 확장 시 하단 border-radius 제거
- `.expand-indicator` (신규): ▼/▲ 토글 표시자 (margin-left: auto, opacity transition)
- `.media-item-body` (신규): 확장 영역 (padding, border-top, fadeIn 애니메이션)

**기존 `.play-btn` 스타일은 유지** — JSX agent가 버튼 제거 후 정리 가능.

### 2. SongDetail.css — inline-player-wrapper 제거

**제거된 스타일:**

- `.song-media .inline-player-wrapper` — 상단 인라인 플레이어 래퍼 (배경, 테두리, 패딩)
- `.song-media .close-player-btn` — 닫기 버튼 (절대 위치, 스타일)
- `.song-media .close-player-btn:hover` — 호버 효과

**영향 없음:** `MemberDetail.css`의 `.inline-player-wrapper`는 스코핑 없이 독립 정의되어 있으므로 영향 없음.

### 3. Dashboard.css — 최근 미디어 카드 스타일 추가

> **참고**: 계획 변경으로 Dashboard Part B가 RehearsalDetail로 교체됨. 이 스타일은 현재 미사용 상태이나 유지 중.

**추가된 스타일:**

- `.recent-media-card`: `grid-column: span 2` (tips-card와 동일 패턴)
- `.recent-media-card .media-item`: Dashboard 내 배경을 `var(--background-color)` 사용
- `.recent-media-card .media-item-header`: 10px 12px 패딩 (약간 컴팩트)
- `.recent-media-card .media-icon`: 1.3rem
- `.recent-media-card .media-name`: ellipsis 처리
- `.recent-media-card .media-meta`: 곡명-아티스트 표시용 보조 텍스트
- 모바일 반응형: `grid-column: span 1`

### 4. RehearsalDetail.css — 아코디언 구조 스타일 추가 + 인라인 플레이어 제거

**추가된 스타일:**

- `.rd-media-item`: `display: flex` 제거 → 블록 컨테이너, `border` + `border-radius` 토큰화
- `.rd-media-item.expanded`: 확장 시 `border-color: var(--primary-color)` 강조
- `.rd-media-item-header` (신규): 기존 `.rd-media-item` 내부 레이아웃 이동 (flex row, gap 8px, cursor pointer, hover 배경)
- `.rd-media-item.expanded .rd-media-item-header`: 확장 시 하단 border-radius 제거
- `.rd-media-item-body` (신규): 확장 영역 (padding, border-top, fadeIn 애니메이션)

**제거된 스타일:**

- `.rd-player-wrapper` — 상단 고정 플레이어 래퍼
- `.rd-close-player` + `:hover` — 닫기 버튼
- `.rd-play-btn` + `:hover` — 재생 버튼 (헤더 클릭으로 대체)

---

## 디자인 토큰 활용

모든 새 스타일에서 기존 디자인 토큰 사용:
- `var(--radius-default)`: border-radius
- `var(--primary-color)`: 확장 상태 강조
- `var(--background-color)`, `var(--surface-color)`: 배경
- `var(--border-color)`: 구분선
- `var(--text-primary)`, `var(--text-secondary)`: 텍스트
- `fadeIn` 키프레임: 확장 애니메이션 (index.css 글로벌 정의)

---

## JSX agent 참고사항

CSS가 준비된 상태에서 JSX agent가 적용할 구조:

```jsx
<div className={`media-item ${isExpanded ? 'expanded' : ''}`}>
  <div className="media-item-header" onClick={...}>
    <span className="media-icon">...</span>
    <div className="media-info">...</div>
    <span className="expand-indicator">{isExpanded ? '▲' : '▼'}</span>
  </div>
  {isExpanded && (
    <div className="media-item-body">
      <MediaPlayer ... />
      <CommentSection ... />
    </div>
  )}
</div>
```

RehearsalDetail에서는 `rd-` 접두사 사용:
```jsx
<div className={`rd-media-item ${isExpanded ? 'expanded' : ''}`}>
  <div className="rd-media-item-header" onClick={...}>
    <span className="rd-media-icon">...</span>
    <div className="rd-media-info">...</div>
    <span className="expand-indicator">{isExpanded ? '▲' : '▼'}</span>
  </div>
  {isExpanded && (
    <div className="rd-media-item-body">
      <MediaPlayer ... />
      <CommentSection ... />
    </div>
  )}
</div>
```
