import { MEMBER_POSITIONS, toggleMemberPosition } from './memberPositions.js';
import './MemberProfile.css';

const MemberPositionPicker = ({ positions = [], customPosition = '', onChange, disabled = false, idPrefix = 'member-position' }) => {
  const selected = Array.isArray(positions) ? positions : [];
  const toggle = (key) => {
    const nextPositions = toggleMemberPosition(selected, key);
    onChange?.(nextPositions, nextPositions.includes('OTHER') ? customPosition : '');
  };

  return <fieldset className="member-position-picker" disabled={disabled}>
    <legend>포지션 · 여러 개 선택 가능</legend>
    <div className="member-position-options">
      {MEMBER_POSITIONS.map(([key, label]) => <label className="check-row" key={key} htmlFor={`${idPrefix}-${key.toLowerCase()}`}>
        <input id={`${idPrefix}-${key.toLowerCase()}`} type="checkbox" checked={selected.includes(key)} onChange={() => toggle(key)} />{label}
      </label>)}
    </div>
    {selected.includes('OTHER') && <label htmlFor={`${idPrefix}-custom`}>기타 악기·역할 직접 입력<input id={`${idPrefix}-custom`} maxLength={50} value={customPosition} onChange={(event) => onChange?.(selected, event.target.value)} disabled={disabled} /></label>}
  </fieldset>;
};

export default MemberPositionPicker;
