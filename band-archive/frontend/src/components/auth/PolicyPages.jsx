import { Link } from 'react-router-dom';
import { useServicePolicy } from '../../context/ServicePolicyContext.jsx';
import './AuthPages.css';

export const PolicyLinks = () => <nav className="policy-links" aria-label="서비스 정책"><Link to="/terms">이용약관</Link><Link to="/privacy">개인정보처리방침</Link><Link to="/upload-policy">업로드·권리 요청 안내</Link></nav>;

const RightsRequestNotice = ({ service }) => <>
  <h2>개인정보·초상권·저작권 요청</h2><p>비회원, 탈퇴자와 영상 출연자도 <a href={`mailto:${service.contact_email}`}>{service.contact_email}</a>로 요청할 수 있습니다. 대상 밴드·게시물 URL이나 식별 정보, 사유, 원하는 조치와 회신 연락처를 알려 주세요. 주민등록번호나 신분증 사본을 먼저 보내지 마세요. 본인·권리 관계 확인에 필요한 최소 자료는 개별 안내합니다.</p>
  <p>일반 화면의 owner·superadmin 삭제 권한은 정당한 개인정보·저작권 등 권리 요청을 거절하는 기준이 아닙니다. 요청 종류에 적용되는 법률에 따라 검토·접근 제한·삭제·필요한 통지와 이의제기를 처리합니다. 법이 요구하는 조치를 owner 승인 때문에 늦추지 않으며, 게시자의 주장만으로 자동 재개하지 않습니다. 모든 요청에 같은 법정 기간이나 동일 조치가 적용되는 것은 아닙니다.</p>
</>;

export const PolicyChecks = ({ terms, age, onTerms, onAge }) => <div className="policy-checks"><label><input type="checkbox" required checked={terms} onChange={(event) => onTerms(event.target.checked)} /><span><Link to="/terms" target="_blank" rel="noopener noreferrer">이용약관</Link>에 동의합니다. (필수)</span></label><label><input type="checkbox" required checked={age} onChange={(event) => onAge(event.target.checked)} /><span>만 14세 이상입니다. (연령 자기확인, 필수)</span></label><p className="auth-help"><Link to="/privacy" target="_blank" rel="noopener noreferrer">개인정보처리방침</Link>에서 계정·가입 요청 정보의 처리와 문의 방법을 확인해 주세요. 방침 열람은 별도의 포괄 동의가 아닙니다.</p></div>;

export const PolicyPage = ({ kind }) => {
  const { config, loading, error, retry } = useServicePolicy();
  const privacy = kind === 'privacy';
  const upload = kind === 'upload';
  const facts = config?.policies;
  const service = config?.service;
  return <main className="policy-page"><Link className="bands-return-link" to="/">← 처음으로</Link><article><h1>{upload ? '업로드·권리 요청 안내' : privacy ? '개인정보처리방침' : '이용약관'}</h1>{loading ? <p aria-live="polite">안내를 불러오는 중입니다…</p> : error ? <><p role="alert">{error}</p><button className="secondary-btn" onClick={retry}>다시 시도</button></> : !facts?.ready || (upload && !facts.upload?.version) ? <><p role="status">정식 정책을 준비하고 있습니다. 운영자 정보와 보관 기준을 확정한 뒤 게시합니다.</p>{service?.contact_email && <p>문의: <a href={`mailto:${service.contact_email}`}>{service.contact_email}</a></p>}</> : <>
    <p>{service.name} · 버전 {upload ? facts.upload.version : privacy ? facts.privacy_version : facts.terms_version}</p>
    {upload ? <>
      <h2>파일을 선택한 뒤 확인하고 시작</h2><p>파일 선택만으로 전송하지 않습니다. 대상 밴드·파일 묶음·공개 범위를 확인하고 업로드 시작을 누릅니다. 영상·음원·사진에 게시·공유 권한이 있고 필요한 출연자·권리자의 허락을 받았는지 확인해 주세요. 무료·비영리·밴드 내부 공유라는 이유만으로 커버곡, 원곡 음원, 반주나 외부 이미지의 사용이 허용되는 것은 아닙니다.</p>
      <h2>자동 처리와 공개 범위</h2><p>파일은 기록 제공을 위해 저장·백업되고 영상은 재생 호환성이나 데이터 절약을 위해 압축·변환하거나 M4A 음원을 추출할 수 있습니다. 새 업로드의 기본값은 정책 활성화 시 멤버 전용이며, 전체 공개를 선택하면 비회원도 볼 수 있습니다. 부모 곡·합주의 멤버 전용 제한은 미디어에도 적용됩니다. 부모 공개 범위나 연결 관계를 바꾸면 기존 파일의 공개 범위가 넓어질 수 있어 다시 확인합니다.</p>
      <h2>허락은 공개 범위에 맞게</h2><p>촬영 허락, 밴드 내부 공유 허락과 인터넷 전체 공개 허락은 다릅니다. 업로더의 체크 하나가 타인의 동의나 음악 권리자의 이용허락을 만들어 주지는 않습니다. 필요한 동의·이용허락이 불분명하면 먼저 확인하고 공개하지 마세요.</p>
      <h2>탈퇴·삭제와 문의</h2><p>밴드 탈퇴 후에도 공동 콘텐츠는 자동 삭제되지 않습니다. 계정 삭제와 콘텐츠·작성자 정보 처리는 별도로 검토하며, 자동 영구 보존이나 전체 콘텐츠 일괄 삭제를 뜻하지 않습니다. 보관·백업 파기 기준은 <Link to="/privacy">개인정보처리방침</Link>의 실제 운영 안내를 확인해 주세요.</p><RightsRequestNotice service={service} />
    </> : privacy ? <>
      <h2>운영자와 문의</h2><p>{service.operator_name} · <a href={`mailto:${service.contact_email}`}>{service.contact_email}</a></p>
      <h2>처리하는 정보와 목적</h2><p>이메일과 인증 정보는 가입·로그인·이메일 확인에, 표시 이름과 밴드 멤버십은 서비스 이용과 권한 관리에 사용합니다. 비밀번호 인증은 Supabase Auth가 처리하며 앱 백엔드는 비밀번호를 저장하지 않습니다.</p><p>밴드 가입 요청의 선택 자기소개는 해당 밴드의 owner·admin이 가입 심사에만 사용하며 승인·거절 시 삭제합니다. 전화번호·주소 등 불필요한 정보는 적지 마세요.</p><p>업로드한 사진·영상·음원, 댓글과 활동 기록은 밴드 기록 제공에 사용합니다. 공개 범위에 따라 다른 이용자가 볼 수 있습니다.</p>
      <p>가입 이름·선택 소개·포지션과 업로드 자료에 포함된 얼굴·목소리 등 식별 가능한 정보는 계정·밴드 기록과 선택한 범위의 공유 제공을 위해 처리합니다. 업로드 확인의 버전·사용자·밴드·세션·공개 범위·확인 시각은 안내 확인과 요청 처리에 필요한 범위에서 기록합니다. 얼굴·목소리로 신원을 인식하는 분석이나 AI 학습을 위한 수집은 하지 않습니다. 개인정보 처리 근거와 필요한 개별 동의는 구분하며, 방침 열람이나 업로더의 체크를 제3자 개인정보 처리의 포괄 동의로 보지 않습니다.</p>
      <h2>보관 및 삭제</h2><p className="policy-facts">{facts.retention_notice}</p><p>밴드를 탈퇴해도 공동 작성 콘텐츠는 자동 삭제되지 않습니다. 일반 화면의 삭제는 밴드 owner 또는 superadmin이 담당합니다. 계정 삭제는 본인 확인 후 계정·멤버십·작성자 연결과 소유권을 별도로 검토하며, 공동 기록 보존과 개인정보 삭제 필요성을 구분합니다.</p>
      <h2>외부 서비스와 처리 위치</h2><p className="policy-facts">{facts.providers_notice}</p><p>봇 방지 기능을 사용할 때 Cloudflare Turnstile이 보안 확인을 처리합니다. <a href="https://www.cloudflare.com/privacypolicy/" target="_blank" rel="noopener noreferrer">Cloudflare 개인정보 안내</a></p>
      <h2>이용자의 권리</h2><p>개인정보 열람·정정·삭제·처리 정지 및 계정 삭제에 관한 요청은 문의 이메일로 보내 주세요. 본인 확인 후 적용 가능한 범위와 절차를 안내합니다. 만 14세 미만은 가입할 수 없습니다.</p>
      <RightsRequestNotice service={service} />
    </> : <>
      <h2>계정과 밴드 이용</h2><p>만 14세 미만은 가입할 수 없습니다. 이메일을 확인한 뒤 자신의 계정을 관리하고 타인의 계정·권한을 무단으로 사용하지 마세요. 밴드 참여는 가입 승인 또는 초대 링크로 이루어집니다. 미성년자는 해당 계약·콘텐츠 이용허락에 법정대리인의 동의가 필요한 경우 이를 받아야 하며, 만 14세 자기확인만으로 모든 미성년자 계약 문제가 해결되는 것은 아닙니다.</p>
      <h2>콘텐츠와 공개 범위</h2><p>본인에게 게시·공유 권한이 있는 콘텐츠만 올려 주세요. 타인의 저작권·초상권·개인정보를 침해하거나 불법 콘텐츠를 게시하지 마세요. 멤버 전용 콘텐츠를 무단으로 외부에 공유하지 마세요.</p>
      <h2>서비스 제공을 위한 제한적 이용허락</h2><p>콘텐츠의 권리는 업로더와 정당한 권리자에게 유지됩니다. 서비스 제공에 필요한 기간과 사용자가 선택한 공개 범위에서 저장·백업·압축·변환·M4A 음원 추출·전송·재생할 수 있는 비독점적 이용허락을 받습니다. 권리를 양도받거나 영구·무제한 사용을 허락받는 것이 아닙니다. 서비스와 무관한 홍보·재판매·AI 학습 권한은 포함하지 않습니다. 삭제와 백업 잔존은 실제 파기 기준에 따릅니다.</p>
      <h2>탈퇴와 삭제</h2><p>밴드를 탈퇴해도 올린 공동 콘텐츠는 자동으로 삭제되지 않습니다. 일반 화면의 삭제는 해당 밴드 owner 또는 superadmin이 담당하며, 법정 권리 요청은 이 UI 제한과 별개입니다. owner는 소유권을 이전한 뒤 탈퇴할 수 있습니다. 계정 삭제·콘텐츠 권리 침해 신고는 문의 이메일로 요청해 주세요.</p><RightsRequestNotice service={service} />
      <h2>서비스 운영과 문의</h2><p>위법·침해·서비스 남용 신고를 검토하여 필요한 조치를 안내합니다. 서비스 변경이나 정책 개정 사항은 서비스 내 안내를 통해 알립니다.</p><p>{service.operator_name} · <a href={`mailto:${service.contact_email}`}>{service.contact_email}</a></p>
    </>}
    </>}<PolicyLinks /></article></main>;
};
