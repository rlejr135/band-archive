import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadBandGalleryImageFile } from '../../../services/mediaUploadManager.js';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { usePagedRead } from '../usePagedRead.js';
import { BandUploadInput } from '../BandUploadInput.jsx';
import { genericError, requestOptions } from '../workspaceReadSupport.js';
import { createWorkspaceRequestGate, takeOneStaleRefresh } from '../workspaceRouteState.js';
import '../BandWorkspace.css';

/* eslint-disable react-hooks/set-state-in-effect */
export const GalleryWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  const fetchGallery = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.gallery(band.id, page), requestOptions(signal));
    return { entries: response?.gallery, pagination: response?.pagination };
  }, [api, band.id]);
  const gallery = usePagedRead('gallery', fetchGallery);
  const [featured, setFeatured] = useState(null);
  const staleAttempts = useRef(new Set());
  const featuredGate = useRef(createWorkspaceRequestGate());
  const uploadGate = useRef(createWorkspaceRequestGate());
  const refreshFeatured = useCallback(() => {
    const controller = new AbortController(); let live = true;
    const token = featuredGate.current.begin(`${workspaceKey}:featured`);
    api.requestJson(bandWorkspacePaths.featuredGallery(band.id), requestOptions(controller.signal)).then((response) => {
      if (live && !controller.signal.aborted && featuredGate.current.accepts(`${workspaceKey}:featured`, token)) setFeatured(response?.gallery ?? null);
    }).catch(() => {
      // A missing or no-longer-visible featured image is an ordinary empty state.
      if (live && !controller.signal.aborted && featuredGate.current.accepts(`${workspaceKey}:featured`, token)) setFeatured(null);
    });
    return () => { live = false; controller.abort(); };
  }, [api, band.id, workspaceKey]);
  useEffect(() => { setFeatured(null); return refreshFeatured(); }, [refreshFeatured]);
  const refreshImage = (imageId, kind = 'gallery') => {
    if (!takeOneStaleRefresh(staleAttempts.current, kind, imageId)) return;
    if (kind === 'featured') refreshFeatured();
    else gallery.refresh();
  };
  const uploadGallery = useCallback(async (file, onProgress, { uploadPolicy, isCurrent: reviewIsCurrent = () => true } = {}) => {
    // Capture both the band id and account-scoped workspace identity before
    // initiating. A late B completion must never refresh A's gallery.
    const capturedBandId = band.id;
    const requestKey = `${workspaceKey}:gallery-upload`;
    const token = uploadGate.current.begin(requestKey);
    const isCurrent = () => reviewIsCurrent() && uploadGate.current.accepts(requestKey, token);
    const result = await uploadBandGalleryImageFile({
      file, bandId: capturedBandId, api, uploadPolicy, onProgress, isCurrent,
    });
    if (isCurrent()) {
      gallery.refresh();
      refreshFeatured();
    }
    return result?.gallery || result;
  }, [api, band.id, gallery, refreshFeatured, workspaceKey]);
  return <section className="workspace-page" aria-labelledby="workspace-gallery-title"><div className="workspace-page-heading"><div><h1 id="workspace-gallery-title">갤러리</h1><p>밴드에서 공개한 사진입니다.</p></div></div>
    {band.membership && String(band.membership.status || 'ACTIVE').toLowerCase() === 'active' && <BandUploadInput band={band} parentVisibility={band.default_visibility} onUpload={uploadGallery} accept="image/*,.png,.jpg,.jpeg,.gif,.webp" multiple={true} />}
    {featured?.url && <section className="workspace-featured"><h2>대표 사진</h2><img src={featured.url} alt="대표 갤러리 이미지" onError={() => refreshImage(featured.id, 'featured')} />{!gallery.entries.some((image) => Number(image.id) === Number(featured.id)) && <BandDeleteAction band={band} workspaceKey={`${workspaceKey}:gallery:${featured.id}`} canDelete={featured.can_delete} label="갤러리 사진" path={bandWorkspacePaths.galleryImage(band.id, featured.id)} asynchronous resourceType="gallery" onDeleted={() => { gallery.refresh(); refreshFeatured(); }} />}</section>}
    {gallery.loading && gallery.entries.length === 0 ? <p aria-live="polite">갤러리를 불러오는 중입니다…</p> : gallery.error && gallery.entries.length === 0 ? <ErrorState message={genericError} retry={gallery.refresh} /> : gallery.entries.length === 0 ? <div className="workspace-empty"><p>표시할 사진이 없습니다.</p></div> : <div className="workspace-gallery-grid">{gallery.entries.map((image) => <figure className="workspace-gallery-item" key={image.id}>{image.url ? <img src={image.url} alt="갤러리 이미지" onError={() => refreshImage(image.id)} /> : <div className="workspace-gallery-missing">표시할 사진이 없습니다.</div>}<BandDeleteAction band={band} workspaceKey={`${workspaceKey}:gallery:${image.id}`} canDelete={image.can_delete} label="갤러리 사진" path={bandWorkspacePaths.galleryImage(band.id, image.id)} asynchronous resourceType="gallery" onDeleted={() => { gallery.refresh(); refreshFeatured(); }} /></figure>)}</div>}
    {gallery.pagination?.has_more && <button className="secondary-btn" disabled={gallery.loading} onClick={gallery.loadMore}>{gallery.loading ? '사진을 불러오는 중…' : '사진 더 불러오기'}</button>}
    {gallery.error && gallery.entries.length > 0 && <ErrorState message={genericError} retry={gallery.refresh} />}
  </section>;
};
