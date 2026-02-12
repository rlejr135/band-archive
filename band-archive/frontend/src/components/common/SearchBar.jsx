import React, { useState } from 'react';
import './SearchBar.css';

const SearchBar = ({ onSearch }) => {
  const [query, setQuery] = useState('');

  const handleChange = (e) => {
    setQuery(e.target.value);
    onSearch(e.target.value);
  };

  return (
    <div className="search-bar">
      <input
        type="text"
        placeholder="곡 제목 또는 아티스트 검색..."
        value={query}
        onChange={handleChange}
      />
    </div>
  );
};

export default SearchBar;
