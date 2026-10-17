import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { API_URL } from '../services/api.js';
import { validateServicePolicy } from '../services/servicePolicy.js';

// eslint-disable-next-line react-refresh/only-export-components
export const ServicePolicyContext = createContext({ loading: true, error: '', config: null });

export const ServicePolicyProvider = ({ children }) => {
  const [state, setState] = useState({ loading: true, error: '', config: null });
  const [attempt, setAttempt] = useState(0);
  const checkedAt = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { controller.abort(); setState({ loading: false, error: '서비스 안내 설정 요청 시간이 초과되었습니다.', config: null }); }, 10000);
    fetch(`${API_URL}/api/v2/policy`, { signal: controller.signal, cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('policy');
        const config = validateServicePolicy(await response.json());
        window.clearTimeout(timer);
        checkedAt.current = Date.now();
        if (!controller.signal.aborted) setState({ loading: false, error: '', config });
      }).catch(() => { window.clearTimeout(timer); if (!controller.signal.aborted) setState({ loading: false, error: '서비스 안내 설정을 불러오지 못했습니다.', config: null }); });
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [attempt]);
  useEffect(() => {
    const refresh = () => { if (document.visibilityState !== 'hidden' && Date.now() - checkedAt.current > 60000) { checkedAt.current = Date.now(); setState((previous) => ({ ...previous, loading: true })); setAttempt((value) => value + 1); } };
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => { window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); };
  }, []);
  return <ServicePolicyContext.Provider value={{ ...state, retry: () => { setState({ loading: true, error: '', config: null }); setAttempt((value) => value + 1); } }}>{children}</ServicePolicyContext.Provider>;
};

// eslint-disable-next-line react-refresh/only-export-components
export const useServicePolicy = () => useContext(ServicePolicyContext);
