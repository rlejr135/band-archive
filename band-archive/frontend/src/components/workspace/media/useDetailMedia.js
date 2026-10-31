import { useCallback, useEffect, useRef, useState } from 'react';
import { requestOptions } from '../workspaceReadSupport.js';
import { createWorkspaceRequestGate, mergeMediaPage, takeOneStaleRefresh } from '../workspaceRouteState.js';

// The caller must key its owning detail by workspace, band and selected resource.
// That boundary also resets playback/comment state and this hook's retry budget.
export const useDetailMedia = ({ api, workspaceKey, resourceId, mediaPath }) => {
  const [mediaState, setMediaState] = useState({
    loading: true,
    error: '',
    pages: [],
    pagination: null
  });
  const requestGate = useRef(createWorkspaceRequestGate());
  const controllers = useRef(new Set());
  const staleAttempts = useRef(new Set());
  const loadMedia = useCallback(async (offset = 0, replace = false) => {
    const controller = new AbortController();
    controllers.current.add(controller);
    const token = requestGate.current.begin(`${workspaceKey}:${resourceId}:${offset}`);
    setMediaState(previous => ({
      ...previous,
      loading: true,
      error: ''
    }));
    try {
      const response = await api.requestJson(mediaPath(offset), requestOptions(controller.signal));
      if (controller.signal.aborted || !requestGate.current.accepts(`${workspaceKey}:${resourceId}:${offset}`, token)) return;
      const page = {
        offset,
        media: Array.isArray(response?.media) ? response.media : []
      };
      setMediaState(previous => ({
        loading: false,
        error: '',
        pages: replace ? [page] : mergeMediaPage(previous.pages, page),
        pagination: response?.pagination ?? previous.pagination
      }));
    } catch {
      if (!controller.signal.aborted && requestGate.current.accepts(`${workspaceKey}:${resourceId}:${offset}`, token)) setMediaState(previous => ({
        ...previous,
        loading: false,
        error: '미디어를 불러오지 못했습니다.'
      }));
    } finally {
      controllers.current.delete(controller);
    }
  }, [api, resourceId, mediaPath, workspaceKey]);
  useEffect(() => {
    const activeControllers = controllers.current;
    loadMedia(0);
    return () => {
      activeControllers.forEach(controller => controller.abort());
      activeControllers.clear();
    };
  }, [loadMedia]);
  const refreshAuthoritativeMedia = useCallback(() => loadMedia(0, true), [loadMedia]);
  const updateMedia = useCallback(updated => {
    if (!updated?.id) return;
    setMediaState(previous => ({
      ...previous,
      pages: previous.pages.map(page => ({
        ...page,
        media: page.media.map(media => media.id === updated.id ? {
          ...media,
          ...updated
        } : media)
      }))
    }));
  }, []);
  const refreshStale = useCallback((offset, mediaId) => {
    if (takeOneStaleRefresh(staleAttempts.current, offset, mediaId)) loadMedia(offset);
  }, [loadMedia]);
  return { mediaState, loadMedia, refreshAuthoritativeMedia, updateMedia, refreshStale };
};
