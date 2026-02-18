# 곡 상세 - 가사/코드/메모 영역 항상 표시 수정 계획

## 현재 문제

**파일:** `band-archive/frontend/src/components/songs/SongDetail.jsx` (135번째 줄)

```jsx
{(song.lyrics || song.chords || song.memo) && (
  <div className="song-content">
    {song.lyrics && ( <가사 영역> )}
    {song.chords && ( <코드 영역> )}
    {song.memo && ( <메모 영역> )}
  </div>
)}
```

- `lyrics`, `chords`, `memo` 세 필드가 **모두 비어있으면** `song-content` 블록 전체가 렌더링되지 않음
- 하나라도 값이 있어야(예: memo) 블록이 보임
- 사용자 입장에서는 "메모가 있어야만 뭔가 보인다"고 느끼게 됨

## 수정 방안

### SongDetail.jsx 수정

1. **`song-content` 래핑 조건 제거** — 항상 표시되도록 변경
2. **각 섹션(가사/코드/메모)은 값이 없으면 "-" 또는 안내 텍스트 표시**

```jsx
{/* 변경 전 */}
{(song.lyrics || song.chords || song.memo) && (
  <div className="song-content">
    {song.lyrics && ( ... )}
    {song.chords && ( ... )}
    {song.memo && ( ... )}
  </div>
)}

{/* 변경 후 */}
<div className="song-content">
  <div className="song-lyrics">
    <h4>가사</h4>
    <pre>{song.lyrics || '등록된 가사가 없습니다.'}</pre>
  </div>
  <div className="song-lyrics">
    <h4>코드</h4>
    <pre>{song.chords || '등록된 코드가 없습니다.'}</pre>
  </div>
  <div className="song-memo">
    <h4>메모</h4>
    <pre>{song.memo || '등록된 메모가 없습니다.'}</pre>
  </div>
</div>
```

### 변경 파일

| 파일 | 변경 내용 |
|------|----------|
| `SongDetail.jsx` (135~156줄) | 조건부 렌더링 제거, 빈 값일 때 안내 텍스트 표시 |
| `SongDetail.css` (필요 시) | 빈 상태 텍스트 스타일 추가 (연한 색상, 이탤릭) |
