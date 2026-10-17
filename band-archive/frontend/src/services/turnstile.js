let loading;
export const loadTurnstile = () => {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    let finished = false;
    const fail = (code) => { if (finished) return; finished = true; window.clearTimeout(timer); script.remove(); loading = null; reject(new Error(code)); };
    const timer = window.setTimeout(() => fail('captcha_timeout'), 15000);
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async = true;
    script.onload = () => { if (finished) return; if (!window.turnstile) { fail('captcha_unavailable'); return; } finished = true; window.clearTimeout(timer); resolve(window.turnstile); };
    script.onerror = () => fail('captcha_unavailable');
    document.head.append(script);
  });
  return loading;
};
