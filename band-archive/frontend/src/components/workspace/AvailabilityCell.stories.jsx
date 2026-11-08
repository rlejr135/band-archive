import { useState } from 'react';
import AvailabilityCell from './AvailabilityCell.jsx';
import './AvailabilityCalendar.css';

const date = '2026-11-14';
const members = [
  { membership_id: 101, display_name: '민지' },
  { membership_id: 102, display_name: '도윤' },
  { membership_id: 103, display_name: '서준' },
];

const availabilityData = (statuses) => ({
  my_membership_id: 101,
  members,
  days: members.map((member, index) => ({
    membership_id: member.membership_id,
    date,
    morning: statuses[index],
    afternoon: 'UNAVAILABLE',
    evening: 'AVAILABLE',
    revision: 1,
  })),
});

const Cell = ({ mode = 'view', statuses = ['AVAILABLE', 'UNAVAILABLE', 'AVAILABLE'], disabled = false, pending = false }) => {
  const [popover, setPopover] = useState(null);
  const [toggles, setToggles] = useState(0);
  return <div style={{ width: 180 }}>
    <AvailabilityCell
      data={availabilityData(statuses)}
      dayIndex={new Map()}
      date={date}
      period="morning"
      mode={mode}
      disabled={disabled}
      pending={pending}
      popover={popover}
      setPopover={setPopover}
      onDate={() => {}}
      onToggle={() => setToggles((value) => value + 1)}
    />
    {mode === 'mine' && <p style={{ color: 'var(--text-secondary)' }}>변경 요청: {toggles}회</p>}
  </div>;
};

const meta = {
  title: 'Workspace/Availability/AvailabilityCell',
  component: Cell,
  parameters: {
    docs: { description: { component: 'MemberAvailability의 오전·오후·저녁 슬롯입니다. 확정 Rehearsal 일정이 아니라 멤버별 가능 여부를 표시합니다.' } },
  },
};

export default meta;

export const MixedAvailability = {};

export const EveryoneAvailable = { args: { statuses: ['AVAILABLE', 'AVAILABLE', 'AVAILABLE'] } };

export const NobodyAvailable = { args: { statuses: ['UNAVAILABLE', 'UNAVAILABLE', 'UNAVAILABLE'] } };

export const MyAvailability = { args: { mode: 'mine', statuses: ['AVAILABLE', 'UNAVAILABLE', 'UNAVAILABLE'] } };

export const Pending = { args: { mode: 'mine', pending: true } };
