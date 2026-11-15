import { Link } from 'react-router-dom';

const publicArchive = {
  eyebrow: 'ARCHIVE',
  title: '공개 아카이브',
  description: '밴드가 공개한 곡과 연주 영상·음원을 만나보세요.',
  to: '/archive',
  label: '공개 아카이브 열기',
};

export function PortalCard({ space }) {
  return <article className="portal-card">
    {space.icon && <span className="portal-card-index" aria-hidden="true">{space.icon}</span>}
    <p className="portal-eyebrow">{space.eyebrow}</p>
    <h2>{space.title}</h2>
    <p>{space.description}</p>
    <Link to={space.to}>{space.label}<span aria-hidden="true">↗</span></Link>
  </article>;
}

export function PublicArchiveCard() {
  return <PortalCard space={publicArchive} />;
}
