import { useEffect, useRef, useState } from 'react';
import { loadTurnstile } from '../../services/turnstile.js';

export const TurnstileChallenge = ({ siteKey, onToken, resetKey = 0 }) => {
  const container = useRef(null);
  const callback = useRef(onToken);
  useEffect(() => { callback.current = onToken; }, [onToken]);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true; let widget; let api;
    callback.current('');
    if (!siteKey) return undefined;
    loadTurnstile().then((turnstile) => {
      if (!current) return;
      api = turnstile;
      widget = api.render(container.current, {
        sitekey: siteKey, theme: 'auto',
        callback: (token) => { if (current) { setError(''); callback.current(token); } },
        'expired-callback': () => { if (current) { callback.current(''); setError('확인이 만료되었습니다. 다시 확인해 주세요.'); } },
        'error-callback': () => { if (current) { callback.current(''); setError('봇 방지 확인에 실패했습니다. 다시 시도해 주세요.'); } },
      });
    }).catch(() => { if (current) { callback.current(''); setError('봇 방지 확인을 불러오지 못했습니다. 네트워크와 차단 설정을 확인해 주세요.'); } });
    return () => { current = false; if (widget !== undefined) api?.remove(widget); };
  }, [siteKey, resetKey, attempt]);
  return <div className="auth-captcha"><div ref={container} aria-label="봇 방지 확인" />{!siteKey && <p role="alert">봇 방지 설정을 확인할 수 없습니다.</p>}{error && <><p role="alert">{error}</p><button className="secondary-btn" type="button" onClick={() => { setError(''); setAttempt((value) => value + 1); }}>봇 방지 다시 시도</button></>}</div>;
};
