import { useState } from 'react';
import { Link } from 'react-router-dom';

export function PublicArchiveLinks({ path, label }) {
  const [message, setMessage] = useState('');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(new URL(path, window.location.origin).href);
      setMessage('공개 링크를 복사했습니다.');
    } catch {
      setMessage('링크를 복사하지 못했습니다. 공개 페이지를 열어 주소를 복사해 주세요.');
    }
  };

  return <>
    <Link className="secondary-btn" to={path}>{label}</Link>
    <button type="button" className="secondary-btn" onClick={copy}>공개 링크 복사</button>
    {message && <span role="status">{message}</span>}
  </>;
}
