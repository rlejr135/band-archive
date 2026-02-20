# Band Archive - Frontend Skills Index

> 이 폴더는 AI agent가 프론트엔드 코드베이스를 빠르게 이해하기 위한 참조 문서입니다.

## 문서 목록

| 파일 | 내용 | 언제 읽어야 하나 |
|------|------|-------------------|
| [00-architecture.md](./00-architecture.md) | 기술 스택, 프로젝트 구조, 빌드 설정 | **항상 먼저 읽기** |
| [01-routing.md](./01-routing.md) | 라우팅 구조, 페이지별 컴포넌트 매핑 | 새 페이지 추가 / URL 변경 시 |
| [02-components.md](./02-components.md) | 컴포넌트 계층구조, props, 사용법 | UI 수정 / 새 컴포넌트 작성 시 |
| [03-state-management.md](./03-state-management.md) | Context API 구조, 상태 흐름 | 상태 로직 변경 / 디버깅 시 |
| [04-api-integration.md](./04-api-integration.md) | API 엔드포인트, 호출 패턴, 에러 처리 | API 연동 / 백엔드 변경 시 |
| [05-data-models.md](./05-data-models.md) | 데이터 모델, 타입 정의 | 데이터 구조 변경 / 새 모델 추가 시 |
| [06-styling.md](./06-styling.md) | CSS 변수, 테마, 반응형, 애니메이션 | 디자인 변경 / 스타일 작업 시 |
| [07-features.md](./07-features.md) | 주요 기능 상세 설명 | 기능 확장 / 버그 수정 시 |

## 빠른 시작 가이드

```bash
# 개발 서버 실행
cd frontend
npm install
npm run dev          # http://localhost:5173

# 빌드
npm run build        # dist/ 폴더에 빌드 결과물 생성
npm run preview      # 빌드 결과물 미리보기
```

## 핵심 요약 (30초 이해)

- **프레임워크**: React 19 + Vite 7 + React Router v7
- **상태관리**: Context API (SongContext) + 컴포넌트 로컬 상태
- **스타일링**: 순수 CSS + CSS 변수 (다크 테마)
- **API 통신**: Native Fetch API (axios 미사용)
- **파일 업로드**: XMLHttpRequest (진행률 추적용)
- **달력**: react-calendar 5.0.0 (합주 일정)
- **인증**: 없음 (삭제 시 비밀번호 확인만 있음, 기본값: 'admin')
- **반응형**: 768px 브레이크포인트, 모바일 대응
- **주요 기능**: 곡 관리, 연습 로그, 멤버 관리, 곡 추천 투표, 공지 토스트, 합주 달력
