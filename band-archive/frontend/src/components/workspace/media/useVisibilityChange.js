import { useEffect, useRef, useState } from 'react';
import { useServicePolicy } from '../../../context/ServicePolicyContext.jsx';
import { reviewPublicVisibility } from '../../../services/uploadPolicy.js';

export function useVisibilityChange(api, path, resource, id) {
  const flight = useRef(null);
  const { config } = useServicePolicy();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => () => {
    flight.current?.abort();
    flight.current = null;
  }, [path]);

  const change = async (body, onSaved, confirmationMessage) => {
    if (flight.current) return;
    setError('');
    if (confirmationMessage) {
      const decision = reviewPublicVisibility(config, { previous: 'MEMBERS', next: 'PUBLIC', confirmationMessage });
      if (!decision.proceed) {
        if (decision.error) setError(decision.error);
        return;
      }
      if (!decision.confirmation && !window.confirm(confirmationMessage)) return;
      body = {
        ...body,
        rights_confirmed: true,
        ...(decision.confirmation ? { visibility_confirm: decision.confirmation } : {}),
      };
    }
    const controller = new AbortController();
    flight.current = controller;
    setBusy(true);
    setError('');
    try {
      const response = await api.requestJson(path, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      const updated = response?.[resource];
      if (updated?.id !== id) throw new Error('Invalid visibility response');
      onSaved(updated);
    } catch {
      if (!controller.signal.aborted) setError('공개 범위를 저장하지 못했습니다. 다시 시도해 주세요.');
    } finally {
      if (flight.current === controller) {
        flight.current = null;
        setBusy(false);
      }
    }
  };

  return { change, busy, error };
}
