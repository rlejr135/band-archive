import { Link } from 'react-router-dom';
import GlobalShell from '../layout/GlobalShell.jsx';

const spaces = [
  { eyebrow: 'START HERE', title: '이용 안내', description: '가입부터 밴드 공간 사용, 공개 범위와 업로드 안내까지 확인하세요.', to: '/guide', label: '사용 방법 보기', icon: '01' },
  { eyebrow: 'COMMUNITY · RECRUITING', title: '밴드 구인구직', description: '함께할 멤버를 찾거나 합류할 밴드를 둘러보세요.', to: '/community/recruiting', label: '구인구직 게시판 열기', icon: '02' },
  { eyebrow: 'COMMUNITY · PLAY TOGETHER', title: '함께 공연해요', description: '합동 공연과 공연 교류를 함께할 밴드와 뮤지션을 찾아보세요.', to: '/community/together', label: '공연 게시판 열기', icon: '03' },
  { eyebrow: 'COMMUNITY · REVIEWS', title: '연습실 후기 / 공연 후기 남기기', description: '연습실과 공연 후기를 읽고 경험을 나누는 공간입니다.', to: '/community/reviews', label: '후기 게시판 열기', icon: '04' },
];

const PortalPage = () => <GlobalShell><section className="portal-intro"><p className="portal-eyebrow">YOUR MUSIC, TOGETHER</p><h1>오늘은 어떤 음악을<br /><em>함께 만들어 볼까요?</em></h1><p>밴드 공간으로 돌아가 기록을 이어가거나, 새로운 음악 친구와 만날 수 있어요.</p></section><section className="portal-spaces" aria-label="Band Archive 시작하기">{spaces.map((space) => <article className="portal-card" key={space.to}><span className="portal-card-index" aria-hidden="true">{space.icon}</span><p className="portal-eyebrow">{space.eyebrow}</p><h2>{space.title}</h2><p>{space.description}</p><Link to={space.to}>{space.label}<span aria-hidden="true">↗</span></Link></article>)}</section></GlobalShell>;

export default PortalPage;
export { PortalPage };
