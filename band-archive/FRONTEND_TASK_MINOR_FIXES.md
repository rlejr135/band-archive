# Frontend Task: 경미한 이슈 2건 수정

## 📋 작업 개요
다중 미디어 통합 작업 검수에서 발견된 경미한 버그 2건을 수정합니다.

---

## 1. 미디어 삭제 시 플레이어 초기화 로직 오류

**파일**: `frontend/src/components/SongDetail.jsx` (line 56)

### 현재 (버그)
```jsx
if (selectedMedia && selectedMedia.url.includes(mediaId)) {
  setSelectedMedia(null);
}
```

### 문제
`mediaId`는 숫자(예: `1`)이고, `selectedMedia.url`은 `"http://localhost:5000/uploads/1_20260208_123456_file.mp3"` 같은 문자열입니다.

`String.includes(1)` → `String.includes("1")`로 암묵적 변환이 발생하여, URL에 `"1"`이 포함되기만 하면 무조건 `true`가 됩니다. 결과적으로 **다른 미디어를 삭제해도 현재 재생 중인 플레이어가 닫힙니다**.

### 수정 방법
`handlePlay`/`handlePreview`에서 선택 시 `media.id`를 함께 저장하고, 삭제 시 ID로 정확히 비교합니다.

```jsx
// handlePlay, handlePreview 수정
const handlePlay = (media) => {
  setSelectedMedia({
    id: media.id,          // ← 추가
    name: media.filename,
    url: `${API_URL}${media.url}`,
    type: media.file_type,
  });
};

const handlePreview = (media) => {
  setSelectedMedia({
    id: media.id,          // ← 추가
    name: media.filename,
    url: `${API_URL}${media.url}`,
    type: media.file_type,
  });
};

// handleDeleteMedia 내부 수정
if (selectedMedia && selectedMedia.id === mediaId) {
  setSelectedMedia(null);
}
```

---

## 2. 업로드 힌트 텍스트 미갱신

**파일**: `frontend/src/components/FileUpload.jsx` (lines 85-87)

### 현재
```jsx
<p className="upload-hint">
  {accept.includes('audio') && '음원 파일 '}
  {accept.includes('video') && '영상 파일 '}
  업로드 가능
</p>
```

`accept`가 `"audio/*,video/*,image/*,.pdf"`로 변경되었지만, 힌트 텍스트는 "음원 파일 영상 파일 업로드 가능"만 표시됩니다.

### 수정
```jsx
<p className="upload-hint">
  {accept.includes('audio') && '음원 '}
  {accept.includes('video') && '영상 '}
  {accept.includes('image') && '이미지 '}
  {accept.includes('.pdf') && '문서 '}
  파일 업로드 가능
</p>
```

---

## ✅ 완료 체크리스트

- [ ] `SongDetail.jsx`: `handlePlay`, `handlePreview`에 `id: media.id` 추가
- [ ] `SongDetail.jsx`: `handleDeleteMedia` 내 비교를 `selectedMedia.id === mediaId`로 변경
- [ ] `FileUpload.jsx`: 업로드 힌트에 이미지/문서 텍스트 추가
