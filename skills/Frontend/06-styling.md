# Styling 가이드

## 개요

- **방식**: 순수 CSS (프리프로세서 없음)
- **테마**: 다크 테마 (기본이자 유일한 테마)
- **CSS 모듈**: 미사용 (글로벌 CSS + BEM 유사 네이밍)
- **반응형**: 768px 브레이크포인트
- **폰트**: Outfit, Inter (sans-serif)

## CSS 변수 (Design Tokens)

**파일**: `src/index.css`

### 색상
```css
--primary-color: #ffd32a;       /* 강렬한 노란색 - 주요 액션 */
--primary-hover: #ffc048;       /* 주요 색 호버 */
--secondary-color: #0fbcf9;     /* 시안/블루 - 보조 액션 */
--accent-color: #ff5e57;        /* 레드/핑크 - 삭제/경고 */
--background-color: #1e272e;    /* 다크 블루그레이 - 배경 */
--surface-color: #2f3640;       /* 밝은 다크 - 카드/서피스 */
--text-primary: #f1f2f6;        /* 주요 텍스트 */
--text-secondary: #d2dae2;      /* 보조 텍스트 */
--border-color: #4b6584;        /* 테두리 */
```

### 그림자 & 둥글기
```css
--shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
--shadow-md: 0 4px 6px rgba(0,0,0,0.4);
--shadow-lg: 0 10px 20px rgba(0,0,0,0.5);
--radius-md: 12px;
```

### 폰트
```css
--font-family: 'Outfit', 'Inter', sans-serif;
```

## 색상 사용 가이드

| 용도 | 변수 | 색상 |
|------|------|------|
| 주요 버튼, 강조 | `--primary-color` | 노랑 #ffd32a |
| 보조 버튼, 링크 | `--secondary-color` | 시안 #0fbcf9 |
| 삭제, 경고, 위험 | `--accent-color` | 빨강 #ff5e57 |
| 페이지 배경 | `--background-color` | 어두운 회색 #1e272e |
| 카드/컨테이너 배경 | `--surface-color` | 밝은 회색 #2f3640 |
| 본문 텍스트 | `--text-primary` | 밝은 흰색 #f1f2f6 |
| 부가 텍스트 | `--text-secondary` | 연한 회색 #d2dae2 |
| 테두리 | `--border-color` | 중간 회색 #4b6584 |

## 레이아웃 구조

### 전체 앱
```css
.app-container {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.app-header { /* 고정 상단 헤더 */ }

.app-main {
  display: flex;
  flex: 1;
}

.sidebar { /* 왼쪽 사이드바 (곡 목록) */ }
.content-area { /* 오른쪽 메인 컨텐츠 */ }
```

### 버튼 클래스
```css
.primary-btn {
  background: var(--primary-color);
  color: #1e272e;
  /* 노란색 배경 + 어두운 글자 */
}

.secondary-btn {
  background: transparent;
  border: 1px solid var(--border-color);
  color: var(--text-primary);
}
```

## 애니메이션

```css
/* 페이드 인 */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.fade-in { animation: fadeIn 0.3s ease; }

/* 바운스 (업로드 아이콘) */
@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

/* 슬라이드 다운 (모달) */
@keyframes slideDown {
  from { transform: translateY(-20px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

/* 흔들림 (에러) */
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-5px); }
  75% { transform: translateX(5px); }
}
```

## 반응형 디자인

### 브레이크포인트
```css
@media (max-width: 768px) {
  /* 모바일 스타일 */
}
```

### 모바일 동작
- **사이드바**: 기본적으로 전체 너비, 곡 선택 시 숨김
- **콘텐츠**: 곡 선택 시 전체 너비로 표시
- **헤더**: 세로로 쌓임
- **그리드**: 단일 컬럼으로 변경
- **버튼**: 터치 친화적 크기 (12px+ 패딩)

### 모바일 토글 패턴
```css
/* 곡 선택 안 됨 */
.app-main .sidebar { display: block; }
.app-main .content-area { display: none; }

/* 곡 선택 됨 */
.app-main.has-selected-song .sidebar { display: none; }
.app-main.has-selected-song .content-area { display: block; }
```

## 공통 UI 패턴

### 로딩 상태
```jsx
<div className="loading">로딩 중...</div>
```

### 에러 상태
```jsx
<div className="error-state">
  <p>오류가 발생했습니다: {error}</p>
  <button onClick={retry}>다시 시도</button>
</div>
```

### 빈 상태
```jsx
<div className="empty-state-box">
  <p>데이터가 없습니다</p>
</div>
```
> 일부 컴포넌트에서 `empty-state` 클래스도 사용됨 (App.jsx 인라인).

### 카드 패턴
```css
.card {
  background: var(--surface-color);
  border-radius: var(--radius-md);
  padding: 20px;
  box-shadow: var(--shadow-sm);
}
```

## CSS 파일 관리 규칙

1. 각 컴포넌트마다 같은 이름의 `.css` 파일 생성
2. `index.css`에 글로벌 변수와 리셋 스타일
3. `App.css`에 레이아웃 관련 스타일
4. 클래스 이름: 케밥 케이스 (`song-detail-title`)
5. 중첩 클래스: 컴포넌트명-요소 패턴 (`member-card-name`)

## 새 스타일 추가 시 주의사항

- 항상 CSS 변수 사용 (하드코딩된 색상 금지)
- `var(--surface-color)` 배경에 `var(--text-primary)` 텍스트
- 호버 효과에 `transition: all 0.2s ease` 추가
- 버튼은 `cursor: pointer` 필수
- 모바일 브레이크포인트 (768px) 고려
