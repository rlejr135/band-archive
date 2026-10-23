import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import GlobalShell from '../layout/GlobalShell.jsx';

const boards = {
  recruiting: { title: '밴드 구인구직', eyebrow: 'BAND RECRUITING', intro: '함께할 멤버를 찾거나 새로운 밴드에 합류해 보세요.', topics: ['찾는 포지션과 활동 지역', '밴드의 음악 장르와 연습 일정', '합류 전에 서로 알아두면 좋을 점'] },
  together: { title: '함께 공연해요', eyebrow: 'PLAY TOGETHER', intro: '합동 공연과 공연 교류를 함께할 밴드와 뮤지션을 찾아보세요.', topics: ['공연을 제안하는 날짜와 장소', '함께 무대에 설 밴드와 뮤지션', '공연 형식과 준비에 필요한 내용'] },
  reviews: { title: '연습실 후기 / 공연 후기 남기기', eyebrow: 'VENUE & SHOW REVIEWS', intro: '연습실과 공연에 다녀온 경험을 나누고 다음 선택에 도움을 주세요.', topics: ['연습실 위치와 시설, 예약 경험', '공연장 접근성과 관람 환경', '현장에서 기억에 남은 점과 참고할 정보'] },
};

export const CommunityBoardPage = () => {
  const { board = '' } = useParams();
  const config = boards[board];
  const [notice, setNotice] = useState('');
  const showComingSoon = () => setNotice('준비중입니다!');

  if (!config) return <GlobalShell><section className="community-page"><h1>게시판을 찾을 수 없습니다</h1><Link to="/portal">포털로 돌아가기</Link></section></GlobalShell>;
  return <GlobalShell><section className="community-page" aria-labelledby="community-title">
    <Link className="community-back" to="/portal">← Band Archive 포털</Link>
    <header className="community-heading"><p className="portal-eyebrow">{config.eyebrow}</p><h1 id="community-title">{config.title}</h1><p>{config.intro}</p></header>
    <div className="community-actions"><button type="button" className="primary-btn" onClick={showComingSoon}>글쓰기</button></div>
    <section className="community-empty" aria-labelledby="community-empty-title"><span className="community-empty-icon" aria-hidden="true">♫</span><h2 id="community-empty-title">아직 등록된 글이 없습니다</h2><p>게시판이 준비되면 이곳에서 다른 밴드와 뮤지션의 글을 확인할 수 있습니다.</p></section>
    <section className="community-template" aria-labelledby="community-template-title"><p className="portal-eyebrow">POST IDEAS</p><h2 id="community-template-title">게시판이 열리면 이런 내용을 나눠보세요</h2><ul>{config.topics.map((topic) => <li key={topic}>{topic}</li>)}</ul></section>
    {notice && <div className="community-toast" role="status" aria-live="polite">{notice}<button type="button" aria-label="알림 닫기" onClick={() => setNotice('')}>확인</button></div>}
  </section></GlobalShell>;
};

export default CommunityBoardPage;
