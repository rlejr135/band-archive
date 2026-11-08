import { fn } from 'storybook/test';
import { ErrorState } from './WorkspaceFeedback.jsx';

const meta = {
  title: 'Workspace/Feedback/ErrorState',
  component: ErrorState,
  args: { message: '밴드 데이터를 불러오지 못했습니다.', retry: fn() },
  parameters: {
    docs: { description: { component: '밴드 작업 공간에서 재시도 가능한 읽기 실패를 알립니다.' } },
  },
};

export default meta;

export const Retryable = {};

export const WithoutRetry = {
  args: { message: '요청한 밴드 정보를 현재 표시할 수 없습니다.', retry: undefined },
};
