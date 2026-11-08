import { MemoryRouter } from 'react-router-dom';
import AvailabilityCalendar from './AvailabilityCalendar.jsx';
import { monthDays, seoulToday } from './availabilityState.js';

const members = [
  { membership_id: 101, display_name: '민지' },
  { membership_id: 102, display_name: '도윤' },
  { membership_id: 103, display_name: '서준' },
];

const snapshot = ({ canReset = false } = {}) => {
  const dates = monthDays(seoulToday().slice(0, 7));
  return {
    band_id: 7,
    generation: 4,
    my_membership_id: 101,
    members,
    notes: [{ membership_id: 102, date: dates[3], period: 'evening', body: '저녁에는 조금 늦을 수 있어요.', visibility: 'BAND', revision: 2 }],
    can_reset: canReset,
    reset_summary: canReset ? { days: 24, notes: 3 } : undefined,
    days: members.flatMap((member, memberIndex) => dates.map((date, dateIndex) => ({
      membership_id: member.membership_id,
      date,
      revision: dateIndex + 1,
      morning: (dateIndex + memberIndex) % 3 === 0 ? 'AVAILABLE' : 'UNAVAILABLE',
      afternoon: (dateIndex + memberIndex) % 2 === 0 ? 'AVAILABLE' : 'UNAVAILABLE',
      evening: dateIndex % 4 === 0 ? 'UNAVAILABLE' : 'AVAILABLE',
    }))),
  };
};

const CalendarStory = ({ owner = false }) => {
  const data = snapshot({ canReset: owner });
  const api = {
    requestJson: async (_path, options = {}) => options.method ? { changed: true, generation: data.generation } : data,
  };
  const band = {
    id: 7,
    slug: 'night-wave',
    name: '나이트 웨이브',
    membership: { status: 'active', role: owner ? 'owner' : 'member' },
  };
  return <MemoryRouter initialEntries={['/bands/night-wave/availability']}><AvailabilityCalendar band={band} api={api} workspaceKey="storybook-availability" /></MemoryRouter>;
};

const meta = {
  title: 'Schedule/MemberAvailability/AvailabilityCalendar',
  component: CalendarStory,
  parameters: {
    docs: {
      description: {
        component: 'MemberAvailability 달력은 활성 BandMembership별 오전·오후·저녁 가능 여부를 조율하는 화면입니다. 여기서 보이는 후보는 확정 Rehearsal·예약·출석을 만들지 않습니다.',
      },
    },
  },
};

export default meta;

export const MemberOverview = {};

export const OwnerResetState = { args: { owner: true } };

export const NoActiveMembership = {
  render: () => <MemoryRouter><AvailabilityCalendar band={{ id: 7, slug: 'night-wave', name: '나이트 웨이브', membership: null }} api={{ requestJson: async () => snapshot() }} workspaceKey="storybook-no-membership" /></MemoryRouter>,
};
