import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandLogoManagementPath, bandLogoPayload, bandLogoUpdatePath } from '../../services/bandLogoApi.js';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';
import './BandLogoManagement.css';
import './BandsPage.css';

const PAGE_SIZE = 20;

const messageFor = (error) => {
  if (error?.status === 401) return '로그인 세션을 확인할 수 없습니다. 다시 로그인해 주세요.';
  if (error?.status === 403) return '이 밴드의 로고를 관리할 권한이 없습니다.';
  if (error?.status === 404) return '밴드를 찾을 수 없거나 로고를 관리할 권한이 없습니다.';
  return '밴드 로고 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';
};

const validLogo = (logo) => logo == null || (typeof logo === 'object'
  && (logo.gallery_image_id == null || (Number.isSafeInteger(logo.gallery_image_id) && logo.gallery_image_id > 0))
  && (logo.url == null || typeof logo.url === 'string'));

const parseManagementResponse = (data, slug) => {
  const band = data?.band;
  const pagination = data?.pagination;
  if (!band || !Number.isSafeInteger(band.id) || band.id <= 0 || band.slug !== slug || typeof band.name !== 'string' || !band.name.trim()) return null;
  if (data.permissions?.can_manage_logo !== true || !validLogo(data.logo) || !Array.isArray(data.gallery)) return null;
  const items = data.gallery.filter((item) => Number.isSafeInteger(item?.id) && item.id > 0
    && (item.url == null || typeof item.url === 'string'));
  const page = {
    limit: Number.isInteger(pagination?.limit) && pagination.limit > 0 ? pagination.limit : PAGE_SIZE,
    offset: Number.isInteger(pagination?.offset) && pagination.offset >= 0 ? pagination.offset : 0,
    total: Number.isInteger(pagination?.total) && pagination.total >= 0 ? pagination.total : items.length,
    has_more: pagination?.has_more === true,
  };
  return { band, logo: data.logo || null, items, pagination: page };
};

const photoKey = (identity, id, url) => `${identity}:${id ?? 'missing'}:${url || ''}`;

const Photo = ({ url, alt, fallbackLabel, onError }) => {
  const safeUrl = safeExternalHttpUrl(url);
  return safeUrl ? <img src={safeUrl} alt={alt} onError={onError} />
    : <div className="band-logo-placeholder" role="img" aria-label={fallbackLabel}>♫</div>;
};

export const BandLogoManagement = ({ identity: suppliedIdentity }) => {
  const { bandSlug = '' } = useParams();
  const { user, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const identity = suppliedIdentity || `${user?.id || 'anonymous'}:${bandSlug}`;
  const [state, setState] = useState({ identity, loading: true, band: null, logo: null, items: [], pagination: null, error: '' });
  const [busyId, setBusyId] = useState(null);
  const [failedPhotos, setFailedPhotos] = useState({});
  const pageSequence = useRef(0);
  const controllers = useRef(new Set());
  const imageRefreshes = useRef(new Set());
  const imageOffsets = useRef(new Map());
  const mutationEpoch = useRef(0);
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const mutationRef = useRef(null);

  const loadPage = useCallback(async (offset = 0, { append = false } = {}) => {
    const requestSequence = ++pageSequence.current;
    const requestMutationEpoch = mutationEpoch.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    setState((previous) => ({ ...previous, identity, loading: true, error: '' }));
    try {
      const data = await api.requestJson(bandLogoManagementPath(bandSlug, { limit: PAGE_SIZE, offset }), { signal: controller.signal });
      const parsed = parseManagementResponse(data, bandSlug);
      if (!parsed) throw new Error('Invalid band logo response');
      if (controller.signal.aborted || pageSequence.current !== requestSequence || identityRef.current !== identity) return;
      parsed.items.forEach((item) => imageOffsets.current.set(String(item.id), offset));
      setState((previous) => ({
        identity, loading: false, band: parsed.band,
        logo: requestMutationEpoch === mutationEpoch.current ? parsed.logo : previous.logo,
        items: append && previous.identity === identity ? mergeItems(previous.items, parsed.items) : parsed.items,
        pagination: parsed.pagination, error: '',
      }));
    } catch (error) {
      if (controller.signal.aborted || pageSequence.current !== requestSequence || identityRef.current !== identity) return;
      if (append) setState((previous) => previous.identity === identity ? { ...previous, loading: false, error: messageFor(error) } : previous);
      else setState({ identity, loading: false, band: null, logo: null, items: [], pagination: null, error: messageFor(error) });
    } finally { controllers.current.delete(controller); }
  }, [api, bandSlug, identity]);

  useEffect(() => {
    const activeControllers = controllers.current;
    imageRefreshes.current.clear();
    imageOffsets.current.clear();
    mutationEpoch.current += 1;
    setFailedPhotos({});
    setBusyId(null);
    Promise.resolve().then(() => loadPage(0));
    return () => {
      pageSequence.current += 1;
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
      mutationRef.current?.abort();
      mutationRef.current = null;
    };
  }, [identity, loadPage]);

  const changeLogo = async (galleryImageId) => {
    const current = state.identity === identity ? state : null;
    if (!current?.band || busyId != null || (galleryImageId != null && (!Number.isSafeInteger(galleryImageId) || galleryImageId <= 0))) return;
    const controller = new AbortController();
    mutationRef.current = controller;
    controllers.current.add(controller);
    const requestedIdentity = identity;
    mutationEpoch.current += 1;
    setBusyId(galleryImageId ?? 'clear');
    try {
      const response = await api.requestJson(bandLogoUpdatePath(current.band.id), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bandLogoPayload(galleryImageId)),
        signal: controller.signal,
      });
      if (controller.signal.aborted || requestedIdentity !== identity) return;
      if (!response || !Object.hasOwn(response, 'logo') || !validLogo(response.logo)) throw new Error('Invalid logo update response');
      if ((galleryImageId == null && response.logo != null) || (galleryImageId != null && response.logo?.gallery_image_id !== galleryImageId)) {
        throw new Error('Invalid logo update response');
      }
      setState((previous) => previous.identity === requestedIdentity ? { ...previous, logo: response.logo || null, error: '' } : previous);
      setFailedPhotos({});
    } catch (error) {
      if (!controller.signal.aborted) setState((previous) => previous.identity === requestedIdentity ? { ...previous, error: error?.status === 403 ? messageFor(error) : '밴드 로고를 변경하지 못했습니다. 다시 시도해 주세요.' } : previous);
    } finally {
      controllers.current.delete(controller);
      if (mutationRef.current === controller) mutationRef.current = null;
      if (!controller.signal.aborted && requestedIdentity === identity) setBusyId(null);
    }
  };

  const refreshPhoto = async (imageId, oldUrl, kind) => {
    const refreshKey = `${identity}:${imageId}`;
    const failKey = photoKey(identity, imageId, oldUrl);
    if (imageRefreshes.current.has(refreshKey)) {
      setFailedPhotos((previous) => ({ ...previous, [failKey]: true }));
      return;
    }
    imageRefreshes.current.add(refreshKey);
    if (kind === 'logo' && busyId != null) {
      setFailedPhotos((previous) => ({ ...previous, [failKey]: true }));
      return;
    }
    const offset = kind === 'candidate' ? imageOffsets.current.get(String(imageId)) ?? 0 : 0;
    const refreshMutationEpoch = mutationEpoch.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    try {
      const data = await api.requestJson(bandLogoManagementPath(bandSlug, { limit: PAGE_SIZE, offset }), { signal: controller.signal });
      const parsed = parseManagementResponse(data, bandSlug);
      if (!parsed || controller.signal.aborted || identityRef.current !== identity || refreshMutationEpoch !== mutationEpoch.current) return;
      const refreshedImage = kind === 'logo'
        ? (String(parsed.logo?.gallery_image_id) === String(imageId) ? parsed.logo : null)
        : parsed.items.find((item) => String(item.id) === String(imageId));
      setState((previous) => {
        if (previous.identity !== identity) return previous;
        if (kind === 'logo' && String(previous.logo?.gallery_image_id) !== String(imageId)) return previous;
        return {
          ...previous,
          logo: kind === 'logo' ? parsed.logo : previous.logo,
          items: kind === 'candidate' ? mergeItems(previous.items, parsed.items) : previous.items,
        };
      });
      if (kind === 'candidate') parsed.items.forEach((item) => imageOffsets.current.set(String(item.id), offset));
      if (!refreshedImage || refreshedImage.url === oldUrl) setFailedPhotos((previous) => ({ ...previous, [failKey]: true }));
    } catch (error) {
      if (!controller.signal.aborted && identityRef.current === identity && refreshMutationEpoch === mutationEpoch.current) {
        if ([401, 403, 404].includes(error?.status)) setState({ identity, loading: false, band: null, logo: null, items: [], pagination: null, error: messageFor(error) });
        else setFailedPhotos((previous) => ({ ...previous, [failKey]: true }));
      }
    } finally { controllers.current.delete(controller); }
  };

  if (state.identity !== identity || (state.loading && !state.band)) return <main className="band-logo-page" aria-live="polite"><p>로고 관리 정보를 불러오는 중입니다…</p></main>;
  if (state.error && !state.band) return <main className="band-logo-page" aria-labelledby="band-logo-title"><Link className="bands-return-link" to="/bands">← 내 밴드</Link><h1 id="band-logo-title">밴드 로고 관리</h1><p role="alert">{state.error}</p><button className="secondary-btn" type="button" onClick={() => loadPage(0)}>다시 시도</button></main>;

  const currentLogoId = state.logo?.gallery_image_id ?? null;
  const currentLogoUrl = safeExternalHttpUrl(state.logo?.url);
  const currentLogoFailed = Boolean(failedPhotos[photoKey(identity, currentLogoId, state.logo?.url)]);
  return <main className="band-logo-page" aria-labelledby="band-logo-title">
    <Link className="bands-return-link" to="/bands">← 내 밴드</Link>
    <header className="band-logo-header"><p className="band-logo-eyebrow">BRANDING</p><h1 id="band-logo-title">{state.band.name} 로고 관리</h1><p>밴드 갤러리에서 기존 사진을 로고로 선택할 수 있습니다.</p></header>
    <section className="band-logo-current" aria-labelledby="band-logo-current-title">
      <div className="band-logo-current-preview"><h2 id="band-logo-current-title">현재 로고</h2>
        {currentLogoId == null ? <div className="band-logo-placeholder" role="img" aria-label="설정된 로고 없음">♫</div>
          : currentLogoUrl && !currentLogoFailed ? <Photo url={state.logo.url} alt={`${state.band.name} 로고`} fallbackLabel={`${state.band.name} 로고를 표시할 수 없음`} onError={() => refreshPhoto(currentLogoId, state.logo.url, 'logo')} />
            : <div className="band-logo-placeholder" role="img" aria-label={`${state.band.name} 로고를 표시할 수 없음`}>♫</div>}
        <p>{currentLogoId == null ? '설정된 로고가 없습니다.' : `갤러리 사진 #${currentLogoId}`}</p>
      </div>
      {currentLogoId != null && <button type="button" className="secondary-btn" disabled={busyId != null} onClick={() => changeLogo(null)}>{busyId === 'clear' ? '해제 중…' : '로고 해제'}</button>}
    </section>
    {state.error && <p className="band-logo-error" role="alert">{state.error}</p>}
    <section className="band-logo-gallery" aria-labelledby="band-logo-gallery-title" aria-busy={busyId != null}>
      <div className="band-logo-section-heading"><div><h2 id="band-logo-gallery-title">갤러리에서 선택</h2><p>표시된 사진에서 로고를 선택하세요.</p></div><span>{state.pagination?.total ?? state.items.length}개</span></div>
      {state.items.length === 0 ? <p className="band-logo-empty">로고로 선택할 사진이 없습니다.</p> : <div className="band-logo-grid">{state.items.map((item) => {
        const selected = String(currentLogoId) === String(item.id);
        const failed = Boolean(failedPhotos[photoKey(identity, item.id, item.url)]);
        return <article className={`band-logo-item${selected ? ' is-selected' : ''}`} key={item.id}>
          {item.url && !failed ? <Photo url={item.url} alt={item.alt_text || `갤러리 사진 #${item.id}`} fallbackLabel={`갤러리 사진 #${item.id}을 표시할 수 없음`} onError={() => refreshPhoto(item.id, item.url, 'candidate')} />
            : <div className="band-logo-placeholder" role="img" aria-label={`갤러리 사진 #${item.id}을 표시할 수 없음`}>♫</div>}
          <div className="band-logo-item-actions"><span>{selected ? '현재 로고' : item.is_featured ? '대표 사진' : `사진 #${item.id}`}</span>
            <button type="button" className={selected ? 'secondary-btn' : 'primary-btn'} disabled={busyId != null || selected} aria-pressed={selected} onClick={() => changeLogo(item.id)}>{busyId === item.id ? '변경 중…' : selected ? '선택됨' : '로고로 선택'}</button>
          </div>
        </article>;
      })}</div>}
      {state.pagination?.has_more && <button type="button" className="secondary-btn band-logo-more" disabled={state.loading || busyId != null} onClick={() => loadPage(state.pagination.offset + state.pagination.limit, { append: true })}>사진 더 불러오기</button>}
    </section>
  </main>;
};

export const BandLogoManagementRoute = () => {
  const { bandSlug = '' } = useParams();
  const { user } = useAuth();
  const identity = `${user?.id || 'anonymous'}:${bandSlug}`;
  return <BandLogoManagement key={identity} identity={identity} />;
};

const mergeItems = (existing, incoming) => {
  const next = [...existing];
  const indexes = new Map(next.map((item, index) => [String(item.id), index]));
  for (const item of incoming) {
    if (indexes.has(String(item.id))) next[indexes.get(String(item.id))] = item;
    else { indexes.set(String(item.id), next.length); next.push(item); }
  }
  return next;
};

