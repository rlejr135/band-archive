import { useRef, useState } from 'react';
import { RehearsalCreateDialog } from './RehearsalCreateDialog.jsx';

const DialogExample = ({ saving = false }) => {
  const [open, setOpen] = useState(true);
  const trigger = useRef(null);
  return <>
    <button ref={trigger} type="button" className="secondary-btn" onClick={() => setOpen(true)}>새 합주 열기</button>
    {open && <RehearsalCreateDialog saving={saving} onClose={() => setOpen(false)} returnFocusRef={trigger}>
      <form className="workspace-form">
        <label>제목<input defaultValue="11월 정기 합주" /></label>
        <label>날짜<input type="date" defaultValue="2026-11-14" /></label>
        <label>시간<input type="time" defaultValue="19:30" /></label>
        <p className="workspace-muted">이 대화상자는 확정 Rehearsal을 추가하는 흐름입니다. 가능 일정 후보만으로 예약이나 출석이 확정되지는 않습니다.</p>
        <div className="form-actions"><button type="button" className="secondary-btn" onClick={() => setOpen(false)} disabled={saving}>취소</button><button type="button" className="primary-btn" disabled={saving}>{saving ? '저장 중…' : '저장'}</button></div>
      </form>
    </RehearsalCreateDialog>}
  </>;
};

const meta = {
  title: 'Schedule/Rehearsal/RehearsalCreateDialog',
  component: DialogExample,
  parameters: {
    layout: 'fullscreen',
    docs: { description: { component: '확정 Rehearsal 생성 대화상자의 포커스 유지·닫기·저장 중 잠금 상태입니다.' } },
  },
};

export default meta;

export const NewRehearsal = {};

export const Saving = { args: { saving: true } };
