import { useState } from 'react';
import SearchBar from './SearchBar.jsx';

const meta = {
  title: 'Common/SearchBar',
  component: SearchBar,
  parameters: {
    docs: { description: { component: '곡 레퍼토리에서 제목 또는 아티스트로 항목을 좁히는 검색 입력입니다.' } },
  },
};

export default meta;

const InteractiveSearch = () => {
  const [query, setQuery] = useState('');
  return <div style={{ maxWidth: 480 }}>
    <SearchBar onSearch={setQuery} />
    <p aria-live="polite" style={{ color: 'var(--text-secondary)' }}>현재 검색어: {query || '없음'}</p>
  </div>;
};

export const Empty = { render: () => <InteractiveSearch /> };

export const WithQuery = {
  render: () => <InteractiveSearch />,
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole('textbox'), '여름밤');
  },
};
