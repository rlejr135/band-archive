# 갤러리 CSS 클래스 목록

## Gallery.css (신규)

### 페이지 레이아웃
| 클래스 | 용도 |
|--------|------|
| `.gallery` | 페이지 컨테이너 (padding, fadeIn, overflow scroll) |
| `.gallery-header` | 제목 + 버튼 영역 (flex row) |
| `.gallery-header h2` | 페이지 제목 |

### 업로드 영역
| 클래스 | 용도 |
|--------|------|
| `.gallery-upload-area` | 드래그&드롭 / 파일 선택 영역 (dashed border) |
| `.gallery-upload-area:hover` | 호버 시 primary 강조 |
| `.gallery-upload-area.dragging` | 드래그 중 배경색 변경 |
| `.gallery-upload-icon` | 업로드 아이콘 (2rem) |
| `.gallery-upload-text` | "이미지를 드래그하세요" 텍스트 |
| `.gallery-upload-hint` | 파일 형식 안내 (작은 텍스트) |

### 이미지 그리드
| 클래스 | 용도 |
|--------|------|
| `.gallery-grid` | CSS grid (auto-fill, minmax 200px) |
| `.gallery-empty` | 빈 상태 메시지 |

### 이미지 카드
| 클래스 | 용도 |
|--------|------|
| `.gallery-card` | 카드 컨테이너 (surface bg, hover lift) |
| `.gallery-card.featured` | 대표 이미지 강조 (primary border) |
| `.gallery-thumbnail` | 썸네일 래퍼 (1:1 비율, overflow hidden) |
| `.gallery-thumbnail img` | 이미지 (object-fit: cover) |
| `.gallery-featured-badge` | 대표 이미지 뱃지 (좌상단 절대 배치, primary pill) |
| `.gallery-card-info` | 파일명 영역 |
| `.gallery-card-name` | 파일명 (ellipsis) |
| `.gallery-card-actions` | 버튼 컨테이너 (flex row) |
| `.gallery-featured-btn` | "대표로 설정" 버튼 |
| `.gallery-card.featured .gallery-featured-btn` | 이미 대표인 경우 (primary 배경, 비활성) |
| `.gallery-delete-btn` | 삭제 버튼 (hover 시 accent 강조) |

---

## Dashboard.css (추가)

| 클래스 | 용도 |
|--------|------|
| `.featured-image-card` | 대시보드 대표 이미지 카드 (padding 제거, overflow hidden) |
| `.featured-image-card h3` | 카드 제목 (패딩 복원) |
| `.featured-image` | 대표 이미지 (max-height 300px, cover) |

---

## JSX 참고 구조

### Gallery.jsx
```jsx
<div className="gallery">
  <div className="gallery-header">
    <h2>갤러리</h2>
  </div>

  <div className="gallery-upload-area {dragging ? 'dragging' : ''}">
    <div className="gallery-upload-icon">📷</div>
    <div className="gallery-upload-text">이미지를 드래그하거나 클릭하세요</div>
    <div className="gallery-upload-hint">PNG, JPG, GIF, WebP</div>
  </div>

  <div className="gallery-grid">
    <div className={`gallery-card ${img.is_featured ? 'featured' : ''}`}>
      <div className="gallery-thumbnail">
        <img src={img.url} alt={img.filename} />
        {img.is_featured && <span className="gallery-featured-badge">대표</span>}
      </div>
      <div className="gallery-card-info">
        <div className="gallery-card-name">{img.filename}</div>
      </div>
      <div className="gallery-card-actions">
        <button className="gallery-featured-btn">대표로 설정</button>
        <button className="gallery-delete-btn">삭제</button>
      </div>
    </div>
  </div>
</div>
```

### Dashboard.jsx (대표 이미지 카드)
```jsx
<div className="dashboard-card featured-image-card">
  <h3>📷 대표 사진</h3>
  <img src={featuredImage.url} alt="대표 이미지" className="featured-image" />
</div>
```
