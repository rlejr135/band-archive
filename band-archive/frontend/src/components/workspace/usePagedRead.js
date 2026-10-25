import { useCallback, useEffect, useRef, useState } from 'react';
import { createWorkspaceRequestGate } from './workspaceRouteState.js';
import { useWorkspace } from './WorkspaceContext.jsx';
import { genericError, PAGE_SIZE } from './workspaceReadSupport.js';

const dedupeEntries = (entries) => {
  const seen = new Set();
  return entries.filter((entry) => {
    if (seen.has(entry.id)) return false;
    seen.add(entry.id);
    return true;
  });
};

// The route shell keys every workspace by slug and account. This hook adds an
// abort plus a generation fence for pagination, so a late page cannot paint
// after either a resource selection or the workspace itself has changed.
export const usePagedRead = (resourceKey, fetchPage) => {
  const { workspaceKey } = useWorkspace();
  const [state, setState] = useState({ loading: true, error: '', notFound: false, entries: [], pagination: null });
  const gate = useRef(createWorkspaceRequestGate());
  const controllers = useRef(new Set());
  const readKey = `${workspaceKey}:${resourceKey}`;
  const load = useCallback(async ({ offset = 0, append = false } = {}) => {
    const controller = new AbortController();
    controllers.current.add(controller);
    const token = gate.current.begin(`${readKey}:${offset}`);
    setState((previous) => ({ ...previous, loading: true, error: '', notFound: false }));
    try {
      const response = await fetchPage({ limit: PAGE_SIZE, offset }, controller.signal);
      if (controller.signal.aborted || !gate.current.accepts(`${readKey}:${offset}`, token)) return;
      const next = Array.isArray(response?.entries) ? response.entries : [];
      setState((previous) => ({ loading: false, error: '', notFound: false, entries: dedupeEntries(append ? [...previous.entries, ...next] : next), pagination: response?.pagination ?? null }));
    } catch (error) {
      if (!controller.signal.aborted && gate.current.accepts(`${readKey}:${offset}`, token)) {
        setState((previous) => ({ ...previous, loading: false, error: genericError, notFound: error?.status === 404 }));
      }
    } finally { controllers.current.delete(controller); }
  }, [fetchPage, readKey]);
  useEffect(() => {
    const activeControllers = controllers.current;
    setState({ loading: true, error: '', notFound: false, entries: [], pagination: null });
    load();
    return () => { activeControllers.forEach((controller) => controller.abort()); activeControllers.clear(); };
  }, [load, readKey]);
  return {
    ...state,
    refresh: useCallback(() => load(), [load]),
    loadMore: useCallback(() => load({ offset: state.pagination?.next_offset ?? state.entries.length, append: true }), [load, state.entries.length, state.pagination?.next_offset]),
  };
};
