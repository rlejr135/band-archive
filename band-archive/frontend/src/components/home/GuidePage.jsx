import GlobalShell from '../layout/GlobalShell.jsx';

const GuidePage = () => <GlobalShell><article className="guide-page"><header className="guide-heading"><p className="portal-eyebrow">BAND ARCHIVE GUIDE</p><h1>밴드의 시간을<br /><em>차근차근 기록해요</em></h1><p>계정을 만들고 밴드에 참여하면 곡과 미디어, 합주, 일정을 한곳에서 함께 관리할 수 있습니다.</p></header>
  <ol className="guide-steps">
    <li><span>01</span><div><h2>계정을 만들고 이메일을 확인해요</h2><p>회원가입 후 인증 메일의 안내를 따라 이메일을 확인합니다. 초대 링크를 받았다면 로그인한 뒤 링크를 다시 열어 가입을 이어갈 수 있어요.</p></div></li>
    <li><span>02</span><div><h2>밴드에 참여해요</h2><p>공개 밴드에 가입 요청을 보내거나, 밴드 관리자에게 받은 초대 링크를 사용하세요. 공개 밴드 찾기에서 밴드 소개와 가입 상태를 확인할 수 있습니다.</p></div></li>
    <li><span>03</span><div><h2>곡과 영상·음원을 모아요</h2><p>밴드 공간의 곡 메뉴에서 레퍼토리를 관리하고 관련 영상이나 음원을 기록에 첨부할 수 있습니다. 업로드 중에는 페이지를 닫거나 이동해도 전송이 계속된다고 보장되지 않으니, 업로드 상태가 완료인지 확인해 주세요.</p></div></li>
    <li><span>04</span><div><h2>합주를 기록하고 일정을 조율해요</h2><p>합주 메뉴에 연습 내용을 남기고, 일정 메뉴에서 가능한 시간을 모아 다음 연습이나 공연을 정해 보세요.</p></div></li>
  </ol>
  <section className="guide-visibility"><p className="portal-eyebrow">ACCESS & VISIBILITY</p><h2>권한과 공개 범위를 확인하세요</h2><p>밴드 안의 기능과 관리 작업은 계정의 멤버십 및 서버가 확인한 권한에 따라 달라집니다. 곡, 사진, 공지, 멤버 명단은 각각의 공개 설정에 따라 보이는 사람이 달라질 수 있어요. 민감한 개인정보나 공개를 원하지 않는 자료는 올리기 전에 공개 범위를 확인하고, 가입 요청의 자기소개에는 전화번호나 주소를 적지 마세요.</p></section>
</article></GlobalShell>;

export default GuidePage;
export { GuidePage };
