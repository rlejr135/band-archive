# 가사(lyrics) 필드 삭제 - Frontend 변경 계획

## 변경 대상 파일 및 내용

### 1. `frontend/src/components/songs/SongForm.jsx`

| 줄 | 현재 코드 | 변경 |
|----|----------|------|
| 17 | `lyrics: '',` (초기 state) | 삭제 |
| 31 | `lyrics: song.lyrics \|\| '',` (편집 state) | 삭제 |
| 43 | `lyrics: '',` (리셋 state) | 삭제 |
| 104-105 | `<label>가사</label>` + `<textarea name="lyrics" ...>` | 삭제 |

### 2. `frontend/src/components/songs/SongDetail.jsx`

| 줄 | 현재 코드 | 변경 |
|----|----------|------|
| 161-165 | 가사 표시 블록 (`<div className="song-lyrics"><h4>가사</h4>...`) | 삭제 |

> 주의: 167줄의 코드(chords) 섹션도 `className="song-lyrics"`를 사용 중 → `song-chords` 등으로 변경하거나 기존 유지

### 3. `frontend/src/components/songs/SongDetail.css`

| 줄 | 현재 코드 | 변경 |
|----|----------|------|
| 48 | `.song-lyrics, .song-memo { ... }` | 코드 섹션이 계속 `.song-lyrics` 클래스를 쓴다면 유지, 아니면 클래스명 정리 |
| 52 | `.song-lyrics h4, .song-memo h4 { ... }` | 위와 동일 |
| 60 | `.song-lyrics pre, .song-memo pre { ... }` | 위와 동일 |
| 236 | `.song-lyrics pre, .song-memo pre { ... }` (모바일) | 위와 동일 |

> CSS 클래스 `.song-lyrics`는 코드(chords) 영역에서도 공유 사용 중이므로 삭제하면 안 됨. 필요 시 코드 영역 클래스를 `.song-chords`로 rename 후 CSS 정리.

## 요약

- 변경 파일: 3개 (`SongForm.jsx`, `SongDetail.jsx`, `SongDetail.css`)
- SongForm: lyrics 관련 state 3곳 + JSX 입력 필드 삭제
- SongDetail: 가사 표시 블록 삭제
- SongDetail.css: `.song-lyrics` 클래스가 코드 영역에서도 쓰이므로 클래스명 정리 검토
