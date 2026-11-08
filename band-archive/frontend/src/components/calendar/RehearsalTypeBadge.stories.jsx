import RehearsalTypeBadge from './RehearsalTypeBadge.jsx';

const meta = {
  title: 'Schedule/Rehearsal/RehearsalTypeBadge',
  component: RehearsalTypeBadge,
  parameters: {
    docs: {
      description: {
        component: 'Rehearsal은 확정된 합주·공연 일정입니다. 공연은 별도 모델이 아니라 rehearsal_type=LIVE로 표시합니다.',
      },
    },
  },
};

export default meta;

export const RegularRehearsal = { args: { type: 'REGULAR' } };

export const ExtraRehearsal = { args: { type: 'EXTRA' } };

export const LivePerformance = { args: { type: 'LIVE' } };

export const OtherSchedule = { args: { type: 'OTHER' } };

export const Unclassified = { args: { type: 'unexpected-value' } };
