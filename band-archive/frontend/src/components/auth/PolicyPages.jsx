import { Link } from 'react-router-dom';
import { useServicePolicy } from '../../context/ServicePolicyContext.jsx';
import './AuthPages.css';

export const PolicyLinks = () => <nav className="policy-links" aria-label="서비스 정책"><Link to="/terms">이용약관</Link><Link to="/privacy">개인정보처리방침</Link></nav>;

export const PolicyChecks = ({ terms, age, onTerms, onAge }) => <div className="policy-checks"><label><input type="checkbox" required checked={terms} onChange={(event) => onTerms(event.target.checked)} /><span><Link to="/terms" target="_blank" rel="noopener noreferrer">이용약관</Link>에 동의합니다. (필수)</span></label><label><input type="checkbox" required checked={age} onChange={(event) => onAge(event.target.checked)} /><span>만 14세 이상입니다. (연령 자기확인, 필수)</span></label><p className="auth-help"><Link to="/privacy" target="_blank" rel="noopener noreferrer">개인정보처리방침</Link>에서 계정·가입 요청 정보의 처리와 문의 방법을 확인해 주세요. 방침 열람은 별도의 포괄 동의가 아닙니다.</p></div>;

export const PolicyPage = ({ kind }) => {
  const { config, loading, error, retry } = useServicePolicy();
  const privacy = kind === 'privacy';
  const facts = config?.policies;
  const service = config?.service;
  return <main className="policy-page"><Link className="bands-return-link" to="/">← 처음으로</Link><article><h1>{privacy ? '개인정보처리방침' : '이용약관'}</h1>{loading ? <p aria-live="polite">안내를 불러오는 중입니다…</p> : error ? <><p role="alert">{error}</p><button className="secondary-btn" onClick={retry}>다시 시도</button></> : !facts?.ready ? <><p role="status">정식 정책을 준비하고 있습니다. 운영자 정보와 보관 기준을 확정한 뒤 게시합니다.</p>{service?.contact_email && <p>문의: <a href={`mailto:${service.contact_email}`}>{service.contact_email}</a></p>}</> : <>
    <p>{service.name} · 버전 {privacy ? facts.privacy_version : facts.terms_version}</p>
    {privacy ? <>
      <h2>운영자와 문의</h2><p>{service.operator_name} · <a href={`mailto:${service.contact_email}`}>{service.contact_email}</a></p>
      <h2>처리하는 정보와 목적</h2><p>이메일과 인증 정보는 가입·로그인·이메일 확인에, 표시 이름과 밴드 멤버십은 서비스 이용과 권한 관리에 사용합니다. 비밀번호 인증은 Supabase Auth가 처리하며 앱 백엔드는 비밀번호를 저장하지 않습니다.</p><p>밴드 가입 요청의 선택 자기소개는 해당 밴드의 owner·admin이 가입 심사에만 사용하며 승인·거절 시 삭제합니다. 전화번호·주소 등 불필요한 정보는 적지 마세요.</p><p>업로드한 사진·영상·음원, 댓글과 활동 기록은 밴드 기록 제공에 사용합니다. 공개 범위에 따라 다른 이용자가 볼 수 있습니다.</p>
      <h2>보관 및 삭제</h2><p className="policy-facts">{facts.retention_notice}</p><p>밴드를 탈퇴해도 작성 콘텐츠는 남습니다. 콘텐츠 삭제는 밴드 owner 또는 superadmin만 할 수 있으며, 삭제 문의는 운영자에게 보내 주세요.</p>
      <h2>외부 서비스와 처리 위치</h2><p className="policy-facts">{facts.providers_notice}</p><p>봇 방지 기능을 사용할 때 Cloudflare Turnstile이 보안 확인을 처리합니다. <a href="https://www.cloudflare.com/privacypolicy/" target="_blank" rel="noopener noreferrer">Cloudflare 개인정보 안내</a></p>
      <h2>이용자의 권리</h2><p>개인정보 열람·정정·삭제·처리 정지 및 계정 삭제에 관한 요청은 문의 이메일로 보내 주세요. 본인 확인 후 적용 가능한 범위와 절차를 안내합니다. 만 14세 미만은 가입할 수 없습니다.</p>
    </> : <>
      <h2>계정과 밴드 이용</h2><p>만 14세 이상만 이메일을 확인한 뒤 가입할 수 있습니다. 자신의 계정을 관리하고 타인의 계정·권한을 무단으로 사용하지 마세요. 밴드 참여는 가입 승인 또는 초대 코드로 이루어집니다.</p>
      <h2>콘텐츠와 공개 범위</h2><p>본인에게 게시·공유 권한이 있는 콘텐츠만 올려 주세요. 타인의 저작권·초상권·개인정보를 침해하거나 불법 콘텐츠를 게시하지 마세요. 멤버 전용 콘텐츠를 무단으로 외부에 공유하지 마세요.</p>
      <h2>탈퇴와 삭제</h2><p>밴드를 탈퇴해도 올린 콘텐츠는 자동으로 삭제되지 않습니다. 모든 콘텐츠 삭제는 해당 밴드 owner 또는 superadmin만 할 수 있습니다. owner는 소유권을 이전한 뒤 탈퇴할 수 있습니다. 계정 삭제·콘텐츠 권리 침해 신고는 문의 이메일로 요청해 주세요.</p>
      <h2>서비스 운영과 문의</h2><p>위법·침해·서비스 남용 신고를 검토하여 필요한 조치를 안내합니다. 서비스 변경이나 정책 개정 사항은 서비스 내 안내를 통해 알립니다.</p><p>{service.operator_name} · <a href={`mailto:${service.contact_email}`}>{service.contact_email}</a></p>
    </>}
    </>}<PolicyLinks /></article></main>;
};
