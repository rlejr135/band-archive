# Data Models

## Song (곡)

```javascript
{
  id: number,               // PK
  title: string,            // 곡 제목 (필수)
  artist: string,           // 아티스트명 (필수)
  status: string,           // 'Practice' | 'Completed' | 'OnHold'
  genre: string,            // 장르
  difficulty: number,       // 난이도 (1~5)
  link: string,             // 원곡 URL
  chords: string,           // 코드 진행
  memo: string,             // 메모
  media: Media[],           // 첨부 미디어 목록
  created_at: string,       // 생성일시 (ISO8601)
  updated_at: string        // 수정일시 (ISO8601)
}
```

### Song Status 값
| 값 | 의미 | 대시보드 표시 |
|----|------|--------------|
| `'Practice'` | 연습 중 | 연습 중인 곡 수 |
| `'Completed'` | 완료됨 | 완료된 곡 수 |
| `'OnHold'` | 보류 | 보류 중인 곡 수 |

## Media (미디어)

```javascript
{
  id: number,               // PK
  song_id: number,          // FK → Song
  filename: string,         // 서버 저장 파일명
  original_filename: string,// 원본 파일명
  file_type: string,        // 'audio' | 'video' | 'image' | 'document'
  file_size: number,        // 파일 크기 (bytes)
  url: string               // 접근 URL
}
```

### file_type 판별
| file_type | 확장자 예시 |
|-----------|------------|
| `'audio'` | mp3, wav, ogg, m4a |
| `'video'` | mp4, avi, mov, webm |
| `'image'` | jpg, png, gif, webp |
| `'document'` | pdf, doc, txt 등 기타 |

## Practice Log (연습 로그)

```javascript
{
  id: number,               // PK
  song_id: number,          // FK → Song
  date: string,             // 날짜 (ISO8601)
  content: string,          // 연습 내용
  feedback: string,         // 피드백/메모
  recording: string | null  // 녹음 파일명 (optional)
}
```

## Suggestion (추천곡)

```javascript
{
  id: number,               // PK
  title: string,            // 곡 제목
  artist: string,           // 아티스트명
  link: string,             // 원곡 URL
  memo: string,             // 추천 이유/메모
  thumbs_up: number,        // 찬성 투표 수
  thumbs_down: number       // 반대 투표 수
}
```

### Score 계산
```javascript
score = thumbs_up - thumbs_down  // 프론트에서 계산
// 순위: score 기준 내림차순 정렬
```

## Member (멤버)

```javascript
{
  id: number,               // PK
  name: string,             // 이름
  instrument: string        // 악기
}
```

## Personal Log (개인 연습 로그)

```javascript
{
  id: number,               // PK
  member_id: number,        // FK → Member
  title: string,            // 로그 제목
  filename: string,         // 업로드된 파일명
  file_type: string,        // 'audio' | 'video'
  url: string,              // 접근 URL
  created_at: string        // 생성일시 (ISO8601)
}
```

## Dashboard Stats (대시보드 통계)

```javascript
{
  total_songs: number,             // 전체 곡 수
  total_practice_logs: number,     // 전체 연습 로그 수
  status_counts: {
    Practice: number,              // 연습 중 곡 수
    Completed: number,             // 완료된 곡 수
    OnHold: number                 // 보류 곡 수
  },
  recent_practice_logs: [          // 최근 연습 로그
    {
      id: number,
      song_id: number,
      song_title: string,
      content: string,
      date: string                 // ISO8601
    }
  ]
}
```

## Rehearsal (합주 일정)

```javascript
{
  id: number,               // PK
  title: string,            // 일정 제목
  date: string,             // 합주 날짜 (단일 일정용)
  start_date: string,       // 기간 시작일 (기간 일정용)
  end_date: string,         // 기간 종료일 (기간 일정용)
  time: string,             // 합주 시간 (예: "19:00")
  memo: string,             // 메모
  color: string,            // 달력 표시 색상 (기본: '#ffd32a')
  songs: Song[],            // 연결된 곡 목록
  created_at: string,       // 생성일시 (ISO8601)
  updated_at: string        // 수정일시 (ISO8601)
}
```

## Announcement (공지사항)

```javascript
{
  id: number,               // PK (항상 1, 단일 레코드)
  content: string,          // 공지 본문
  updated_at: string        // 마지막 수정 시각 (ISO8601)
}
```

## 폼 데이터 구조

### Song Form (SongForm.jsx)
```javascript
{
  title: '',
  artist: '',
  status: 'Practice',     // 기본값
  genre: '',
  difficulty: 3,           // 기본값
  link: '',
  chords: '',
  memo: ''
}
```

### Practice Log Form (PracticeLogSection.jsx)
```javascript
{
  date: new Date().toISOString().split('T')[0],  // 오늘 날짜
  content: '',
  feedback: ''
}
```

### Suggestion Form (SongSuggestion.jsx)
```javascript
{
  title: '',
  artist: '',
  link: '',
  memo: ''
}
```

### Member Form (MemberDashboard.jsx)
```javascript
{
  name: '',
  instrument: ''
}
```
