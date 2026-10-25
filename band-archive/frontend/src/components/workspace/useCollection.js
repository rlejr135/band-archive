import { useCallback, useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { useWorkspace } from './WorkspaceContext.jsx';
import { genericError, PAGE_SIZE, requestOptions } from './workspaceReadSupport.js';

/* eslint-disable react-hooks/set-state-in-effect */
export const useCollection = (kind) => {
  const { band, api, workspaceKey } = useWorkspace();
  const [state, setState] = useState({ loading: true, error: '', entries: [], pagination: null });
  const version = useRef(0);
  const resource = kind === 'songs' ? 'songs' : 'rehearsals';
  const load = useCallback(async ({ append = false, offset = 0 } = {}) => {
    const controller = new AbortController();
    const token = ++version.current;
    setState((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths[resource](band.id, { limit: PAGE_SIZE, offset }), requestOptions(controller.signal));
      if (token !== version.current) return;
      const next = Array.isArray(response?.[resource]) ? response[resource] : [];
      setState((previous) => ({ loading: false, error: '', entries: append ? [...previous.entries, ...next] : next, pagination: response?.pagination ?? null }));
    } catch {
      if (token === version.current) setState((previous) => ({ ...previous, loading: false, error: genericError }));
    }
  }, [api, band.id, resource]);
  useEffect(() => { version.current += 1; load(); }, [load, workspaceKey]);
  const refresh = useCallback(() => load(), [load]);
  const loadMore = useCallback(() => load({ append: true, offset: state.pagination?.next_offset ?? state.entries.length }), [load, state.entries.length, state.pagination?.next_offset]);
  return { ...state, refresh, loadMore };
};
