import { useMemo, useState } from 'react';
import AvailabilityCandidates from './AvailabilityCandidates.jsx';
import { candidates } from './availabilityState.js';
import './AvailabilityCalendar.css';

const dates = ['2026-11-14', '2026-11-15'];
const members = [
  { membership_id: 101, display_name: '민지' },
  { membership_id: 102, display_name: '도윤' },
  { membership_id: 103, display_name: '서준' },
];

const data = {
  generation: 4,
  my_membership_id: 101,
  members,
  days: [
    { membership_id: 101, date: dates[0], morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE', revision: 3 },
    { membership_id: 102, date: dates[0], morning: 'AVAILABLE', afternoon: 'UNAVAILABLE', evening: 'AVAILABLE', revision: 2 },
    { membership_id: 103, date: dates[0], morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'AVAILABLE', revision: 1 },
    { membership_id: 101, date: dates[1], morning: 'UNAVAILABLE', afternoon: 'AVAILABLE', evening: 'AVAILABLE', revision: 1 },
    { membership_id: 102, date: dates[1], morning: 'UNAVAILABLE', afternoon: 'UNAVAILABLE', evening: 'AVAILABLE', revision: 1 },
    { membership_id: 103, date: dates[1], morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE', revision: 1 },
  ],
};

const CandidateList = ({ canCreate = false, busy = false }) => {
  const [popover, setPopover] = useState(null);
  const [confirmed, setConfirmed] = useState(null);
  const dayIndex = useMemo(() => new Map(data.days.map((day) => [`${day.membership_id}:${day.date}`, day])), []);
  const ranked = useMemo(() => candidates(data, dates, members.map((member) => member.membership_id)), []);
  return <div style={{ maxWidth: 620 }}>
    <AvailabilityCandidates
      ranked={ranked}
      data={data}
      dayIndex={dayIndex}
      selectedMembers={members.map((member) => member.membership_id)}
      busy={busy}
      canCreate={canCreate}
      popover={popover}
      setPopover={setPopover}
      onConfirm={(candidate) => setConfirmed(candidate)}
    />
    {confirmed && <p role="status">선택한 조율 후보: {confirmed.date} · {confirmed.period}</p>}
  </div>;
};

const meta = {
  title: 'Schedule/MemberAvailability/AvailabilityCandidates',
  component: CandidateList,
  parameters: {
    docs: {
      description: {
        component: 'MemberAvailability에서 계산한 조율 후보입니다. 가능한 시간대의 집계일 뿐, 확정 Rehearsal·예약·출석을 만들지 않습니다.',
      },
    },
  },
};

export default meta;

export const CandidateDetails = {};

export const CanOpenRehearsalCreation = { args: { canCreate: true } };

export const Loading = { args: { busy: true } };
