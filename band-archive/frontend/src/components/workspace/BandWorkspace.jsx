import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import HeaderActions from '../layout/HeaderActions.jsx';
import BandActivityDashboard from '../dashboard/BandActivityDashboard.jsx';
import AvailabilityCalendar from './AvailabilityCalendar.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';
import './BandPlacePicker.css';
import { MembersWorkspace } from './members/MembersWorkspace.jsx';
import { MemberDetailWorkspace } from './members/MemberDetailWorkspace.jsx';
import { AnnouncementEditor } from './announcements/AnnouncementEditor.jsx';
import { FeaturedMediaEditor } from './featured-media/FeaturedMediaEditor.jsx';
import { RepresentativeMediaWorkspace } from './featured-media/FeaturedMediaWorkspace.jsx';
import { SuggestionsWorkspace } from './suggestions/SuggestionsWorkspace.jsx';
import { GalleryWorkspace } from './gallery/GalleryWorkspace.jsx';
import { WorkspaceContext, useWorkspace } from './WorkspaceContext.jsx';
import { requestOptions } from './workspaceReadSupport.js';
import UploadCenter from '../common/UploadCenter.jsx';
import MediaPlayer from '../common/MediaPlayer.jsx';
import { createWorkspaceRequestGate, WorkspacePaintGate, workspaceIdentityKey } from './workspaceRouteState.js';
import './BandWorkspace.css';
import { SongWorkspace } from './songs/SongWorkspace.jsx';
import { RehearsalWorkspace } from './rehearsals/RehearsalWorkspace.jsx';

export { AnnouncementEditor };
export { FeaturedMediaEditor };
export { SuggestionsWorkspace };
export { GalleryWorkspace };
// Compatibility exports: remove after all consumers use the members entry points.
export { MembersWorkspace, MemberDetailWorkspace };
// Compatibility export retained for the frozen comparison fixture.
export { RepresentativeMediaWorkspace };
// Compatibility export: new consumers import the songs entry point directly.
export { SongWorkspace };
// Compatibility export: new consumers import the rehearsals entry point directly.
export { RehearsalWorkspace };

// This file intentionally starts request state from effects: each state reset
// prevents a previous band/selection from rendering while its replacement is
// being resolved. The guarded async callbacks then own the terminal state.

// React Router reuses a route element when only params change. Keying this
// boundary makes the old workspace (including open forms/details) unmount
// synchronously before the next slug or account can render.
export const BandWorkspaceRoute = () => {
  const { bandSlug } = useParams();
  const { user } = useAuth();
  const { membershipRevisions = {} } = useBands();
  return <BandWorkspace key={`${workspaceIdentityKey(bandSlug, user?.id)}:${membershipRevisions[bandSlug] || 0}`} />;
};

export const BandWorkspace = () => {
  const { bandSlug } = useParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState({ loading: true, error: '', band: null });
  const requestGate = useRef(createWorkspaceRequestGate());
  const currentBandRequest = useRef({ routeKey: '', version: null });
  const logoRefreshes = useRef(new Set());
  const logoRefreshController = useRef(null);
  const [failedLogoKey, setFailedLogoKey] = useState('');
  const routeKey = workspaceIdentityKey(bandSlug, user?.id);

  useEffect(() => {
    if (!state.band || state.band.slug !== bandSlug) return undefined;
    document.title = `${state.band.name} · Band Archive`;
    return () => { document.title = 'Band Archive'; };
  }, [bandSlug, pathname, state.band, state.band?.name, state.band?.slug]);

  useEffect(() => {
    const controller = new AbortController();
    const version = requestGate.current.begin(routeKey);
    currentBandRequest.current = { routeKey, version };
    logoRefreshes.current.clear();
    setFailedLogoKey('');
    setState({ loading: true, error: '', band: null });
    api.requestJson(bandWorkspacePaths.bandBySlug(bandSlug), requestOptions(controller.signal))
      .then((response) => {
        if (controller.signal.aborted || !requestGate.current.accepts(routeKey, version) || !response?.band) return;
        setState({ loading: false, error: '', band: response.band });
      })
      .catch(() => {
        if (!controller.signal.aborted && requestGate.current.accepts(routeKey, version)) {
          setState({ loading: false, error: '밴드를 찾을 수 없거나 볼 권한이 없습니다.', band: null });
        }
      });
    return () => { controller.abort(); logoRefreshController.current?.abort(); logoRefreshController.current = null; };
  }, [api, bandSlug, routeKey]);

  const refreshLogo = async () => {
    const logoImage = state.band?.logo;
    const imageId = logoImage?.gallery_image_id;
    const refreshKey = `${routeKey}:${imageId ?? 'missing'}`;
    const failedKey = `${refreshKey}:${logoImage?.url || ''}`;
    if (!logoImage || imageId == null || !logoImage.url) return;
    if (logoRefreshes.current.has(refreshKey)) { setFailedLogoKey(failedKey); return; }
    logoRefreshes.current.add(refreshKey);
    const controller = new AbortController();
    logoRefreshController.current?.abort();
    logoRefreshController.current = controller;
    const activeRequest = currentBandRequest.current;
    try {
      const response = await api.requestJson(bandWorkspacePaths.bandBySlug(bandSlug), requestOptions(controller.signal));
      const refreshedBand = response?.band;
      const stillCurrent = !controller.signal.aborted
        && currentBandRequest.current === activeRequest
        && requestGate.current.accepts(routeKey, activeRequest.version)
        && refreshedBand?.slug === bandSlug
        && String(refreshedBand?.id) === String(state.band.id);
      if (!stillCurrent) return;
      const refreshedLogo = refreshedBand.logo || null;
      setState((previous) => previous.band && String(previous.band.id) === String(refreshedBand.id)
        ? { ...previous, band: { ...previous.band, logo: refreshedLogo } }
        : previous);
      if (String(refreshedLogo?.gallery_image_id) === String(imageId) && refreshedLogo?.url === logoImage.url) setFailedLogoKey(failedKey);
    } catch {
      if (!controller.signal.aborted && currentBandRequest.current === activeRequest) setFailedLogoKey(failedKey);
    } finally {
      if (logoRefreshController.current === controller) logoRefreshController.current = null;
    }
  };

  if (state.loading) return <WorkspacePaintGate requestedSlug={bandSlug} />;
  if (state.error || !state.band) return <main className="band-workspace-loading" role="alert"><p>{state.error}</p><button className="secondary-btn" onClick={() => navigate('/bands')}>내 밴드로 이동</button></main>;
  if (state.band.slug !== bandSlug) return <WorkspacePaintGate requestedSlug={bandSlug} resolvedSlug={state.band.slug} />;
  const value = { band: state.band, api, workspaceKey: `${state.band.id}:${user?.id || 'visitor'}` };
  const logoUrl = safeExternalHttpUrl(state.band.logo?.url);
  const logoFailed = failedLogoKey === `${routeKey}:${state.band.logo?.gallery_image_id ?? 'missing'}:${state.band.logo?.url || ''}`;
  return <WorkspaceContext.Provider value={value}>
    <div className="band-workspace">
      <header className="workspace-header">
        <button className="workspace-brand" onClick={() => navigate(`/bands/${encodeURIComponent(state.band.slug)}`)}>
          {logoUrl && !logoFailed ? <img src={logoUrl} alt={`${state.band.name} 로고`} onError={refreshLogo} /> : <span className="workspace-brand-mark" aria-hidden="true">♫</span>}<span>{state.band.name}<small> Archive</small></span>
        </button>
        <div className="workspace-actions">
          <UploadCenter />
          <HeaderActions currentSlug={state.band.slug} />
        </div>
        <nav aria-label="밴드 공간">
          <NavLink end to={`/bands/${encodeURIComponent(state.band.slug)}`}>대시보드</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/songs`}>곡</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/representative-media`}>대표 미디어</NavLink>
          <NavLink className={({ isActive }) => isActive || pathname.endsWith('/availability') ? 'active' : ''} to={`/bands/${encodeURIComponent(state.band.slug)}/rehearsals`}>합주</NavLink>
          {state.band.permissions?.can_view_roster && <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/members`}>멤버</NavLink>}
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/gallery`}>갤러리</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/suggestions`}>추천곡</NavLink>
          {state.band.permissions?.can_manage_members && state.band.permissions?.can_manage_invites && <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/manage`}>관리</NavLink>}
        </nav>
      </header>
      <main className="workspace-main"><Outlet /></main>
    </div>
  </WorkspaceContext.Provider>;
};

export const BandDashboard = () => {
  const { band, api, workspaceKey } = useWorkspace();
  return <BandActivityDashboard key={workspaceKey} band={band} api={api} workspaceKey={workspaceKey} />;
};

export const AvailabilityWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  return <AvailabilityCalendar key={workspaceKey} band={band} api={api} workspaceKey={workspaceKey} />;
};
