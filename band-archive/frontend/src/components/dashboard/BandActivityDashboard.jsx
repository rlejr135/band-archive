import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Calendar from 'react-calendar';
import { Link } from 'react-router-dom';
import 'react-calendar/dist/Calendar.css';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import {
  DASHBOARD_PAGE_SIZE,
  isoMonthFromDate,
  mergeDashboardImages,
  mergeDashboardPage,
  parseIsoDate,
  rehearsalsForDate,
  strictPositiveId,
} from '../workspace/bandDashboardState.js';
import './BandActivityDashboard.css';

const requestOptions = (signal) => ({ authenticated: false, optionalAuth: true, signal });
const DASHBOARD_ERROR = '대시보드를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';
const SECTION_ERROR = '이 항목을 불러오지 못했습니다. 다시 시도해 주세요.';

const seoulDate = () => {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const part = (type) => parts.find((value) => value.type === type)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
};

const dateKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const localDateFromKey = (value) => parseIsoDate(value) || new Date();
const displayDate = (value) => parseIsoDate(value)?.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' }) || '날짜 미정';
const monthDate = (month) => parseIsoDate(`${month}-01`) || new Date();
const imageKey = (workspaceKey, month, kind, id) => `${workspaceKey}:${month}:${kind}:${id}`;

const statusRows = [
  ['Practice', '연습 중'],
  ['Completed', '완료'],
  ['OnHold', '보류'],
  ['Other', '기타'],
];

const songHref = (band, song) => {
  const id = strictPositiveId(String(song?.id ?? ''));
  return id ? `/bands/${encodeURIComponent(band.slug)}/songs?song=${id}` : null;
};

const rehearsalHref = (band, rehearsal) => {
  const id = strictPositiveId(String(rehearsal?.id ?? ''));
  return id ? `/bands/${encodeURIComponent(band.slug)}/rehearsals?rehearsal=${id}` : null;
};

const RehearsalSummary = ({ band, rehearsal, compact = false }) => {
  const href = rehearsalHref(band, rehearsal);
  const hasValidRange = parseIsoDate(rehearsal.start_date) && parseIsoDate(rehearsal.end_date) && rehearsal.start_date <= rehearsal.end_date;
  const start = hasValidRange ? rehearsal.start_date : rehearsal.date;
  const end = hasValidRange ? rehearsal.end_date : null;
  const dateLabel = hasValidRange && start !== end ? `${displayDate(start)} ~ ${displayDate(end)}` : displayDate(start);
  return <article className={compact ? 'activity-rehearsal activity-rehearsal-compact' : 'activity-rehearsal'}>
    <div className="activity-rehearsal-heading">
      <strong>{href ? <Link to={href}>{rehearsal.title || '합주'}</Link> : rehearsal.title || '합주'}</strong>
      <span>{dateLabel}{rehearsal.time ? ` · ${rehearsal.time}` : ''}</span>
    </div>
    {rehearsal.location && <span className="activity-muted">{rehearsal.location}</span>}
    {Array.isArray(rehearsal.songs) && rehearsal.songs.length > 0 && <div className="activity-rehearsal-songs">
      {rehearsal.songs.map((song) => {
        const songLink = songHref(band, song);
        return songLink ? <Link key={song.id} to={songLink}>{song.title || '곡'}</Link> : <span key={song.id || song.title}>{song.title || '곡'}</span>;
      })}
      {rehearsal.has_more_songs && href && <Link className="activity-muted" to={href}>나머지 곡 보기</Link>}
    </div>}
  </article>;
};

const AnnouncementCard = ({ announcement, api, bandId, workspaceKey }) => {
  const [expanded, setExpanded] = useState(false);
  const [fullState, setFullState] = useState({ loading: false, error: '', content: '' });
  const requestEpoch = useRef(0);
  const controllerRef = useRef(null);
  useEffect(() => () => {
    requestEpoch.current += 1;
    controllerRef.current?.abort();
  }, [workspaceKey]);

  const loadFullAnnouncement = async () => {
    if (fullState.loading) return;
    const epoch = ++requestEpoch.current;
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setFullState({ loading: true, error: '', content: '' });
    try {
      const response = await api.requestJson(bandWorkspacePaths.announcement(bandId), requestOptions(controller.signal));
      if (controller.signal.aborted || epoch !== requestEpoch.current) return;
      setFullState({ loading: false, error: '', content: response?.announcement?.content || announcement.content || '' });
    } catch {
      if (!controller.signal.aborted && epoch === requestEpoch.current) setFullState({ loading: false, error: SECTION_ERROR, content: '' });
    }
  };

  const content = expanded ? fullState.content : announcement?.content;
  return <section className="activity-card activity-announcement" aria-labelledby="activity-announcement-title">
    <div className="activity-card-heading"><h2 id="activity-announcement-title">공지사항</h2></div>
    {announcement?.content ? <>
      <p className="activity-announcement-content">{content}</p>
      {announcement.content_truncated && !expanded && <button type="button" className="activity-text-button" onClick={() => { setExpanded(true); loadFullAnnouncement(); }}>전체 공지 보기</button>}
      {fullState.loading && <p className="activity-muted" aria-live="polite">전체 공지를 불러오는 중입니다…</p>}
      {fullState.error && <div className="activity-inline-error" role="alert"><span>{fullState.error}</span><button type="button" className="activity-text-button" onClick={loadFullAnnouncement}>다시 시도</button></div>}
    </> : <p className="activity-muted">현재 공개된 공지사항이 없습니다.</p>}
  </section>;
};

const DashboardImage = ({ image, kind, workspaceKey, month, failedUrls, onError }) => {
  const key = imageKey(workspaceKey, month, kind, image?.id ?? 'unknown');
  const url = image?.url;
  if (!url || failedUrls[key] === url) {
    return <div className={`activity-image-placeholder ${kind === 'featured' ? 'activity-image-placeholder-featured' : ''}`} role="img" aria-label={kind === 'featured' ? '대표 사진을 표시할 수 없습니다' : '사진을 표시할 수 없습니다'}>사진 없음</div>;
  }
  return <img src={url} alt={kind === 'featured' ? '대표 갤러리 사진' : image.alt_text || image.filename || '밴드 갤러리 사진'} onError={() => onError(kind, image)} />;
};

const BandActivityDashboard = ({ band, api, workspaceKey }) => {
  const [month, setMonth] = useState(() => seoulDate().slice(0, 7));
  const [selectedDate, setSelectedDate] = useState(() => seoulDate());
  const [state, setState] = useState({ month: '', loading: true, error: '', pageLoading: false, pageError: '', dashboard: null });
  const epoch = useRef(0);
  const controllers = useRef(new Set());
  const imageRefreshController = useRef(null);
  const imageRefreshEpoch = useRef(0);
  const refreshedImages = useRef(new Set());
  const [failedUrls, setFailedUrls] = useState({});

  const requestPage = useCallback(async ({ targetMonth = month, offset = 0, mode = 'replace' } = {}) => {
    const token = ++epoch.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    if (mode === 'append') setState((previous) => ({ ...previous, pageLoading: true, pageError: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths.dashboard(band.id, { month: targetMonth, limit: DASHBOARD_PAGE_SIZE, offset }), requestOptions(controller.signal));
      const dashboard = response?.dashboard;
      if (!dashboard || Number(dashboard.band_id) !== Number(band.id) || dashboard.month !== targetMonth) throw new Error('Dashboard response did not match the requested band and month');
      if (controller.signal.aborted || token !== epoch.current) return;
      setState((previous) => ({
        month: targetMonth,
        loading: false,
        error: '',
        pageLoading: false,
        pageError: '',
        dashboard: mode === 'append'
            ? mergeDashboardPage(previous.month === targetMonth ? previous.dashboard : null, dashboard, { append: true })
            : dashboard,
      }));
    } catch {
      if (controller.signal.aborted || token !== epoch.current) return;
      if (mode === 'append') setState((previous) => ({ ...previous, pageLoading: false, pageError: SECTION_ERROR }));
      else setState({ month: targetMonth, loading: false, error: DASHBOARD_ERROR, pageLoading: false, pageError: '', dashboard: null });
    } finally {
      controllers.current.delete(controller);
    }
  }, [api, band.id, month]);

  useEffect(() => {
    const activeControllers = controllers.current;
    let active = true;
    queueMicrotask(() => {
      if (active) requestPage({ targetMonth: month, offset: 0 });
    });
    return () => {
      active = false;
      epoch.current += 1;
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
      imageRefreshEpoch.current += 1;
      imageRefreshController.current?.abort();
      imageRefreshController.current = null;
    };
  }, [month, requestPage, workspaceKey]);

  const handleImageError = (kind, image) => {
    const identity = imageKey(workspaceKey, month, kind, image?.id ?? 'unknown');
    setFailedUrls((previous) => ({ ...previous, [identity]: image?.url || '' }));
    if (refreshedImages.current.has(identity)) return;
    refreshedImages.current.add(identity);
    const token = ++imageRefreshEpoch.current;
    const controller = new AbortController();
    imageRefreshController.current?.abort();
    imageRefreshController.current = controller;
    api.requestJson(bandWorkspacePaths.dashboard(band.id, { month, limit: DASHBOARD_PAGE_SIZE, offset: 0 }), requestOptions(controller.signal))
      .then((response) => {
        const incoming = response?.dashboard;
        if (!controller.signal.aborted && token === imageRefreshEpoch.current
          && Number(incoming?.band_id) === Number(band.id) && incoming?.month === month) {
          setState((previous) => previous.month === month
            ? { ...previous, dashboard: mergeDashboardImages(previous.dashboard, incoming) }
            : previous);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (imageRefreshController.current === controller) imageRefreshController.current = null;
      });
  };

  const dashboard = state.month === month ? state.dashboard : null;
  const monthItems = Array.isArray(dashboard?.month_rehearsals?.items) ? dashboard.month_rehearsals.items : [];
  const pagination = dashboard?.month_rehearsals?.pagination || {};
  const stats = dashboard?.stats || {};
  const statusCounts = stats.status_counts || {};
  const selectedItems = rehearsalsForDate(monthItems, selectedDate);
  const activeStartDate = useMemo(() => monthDate(month), [month]);
  const selectedDateObject = useMemo(() => localDateFromKey(selectedDate), [selectedDate]);

  const changeMonth = ({ activeStartDate }) => {
    const nextMonth = isoMonthFromDate(activeStartDate);
    if (!nextMonth || nextMonth === month) return;
    setState({ month: nextMonth, loading: true, error: '', pageLoading: false, pageError: '', dashboard: null });
    setMonth(nextMonth);
    setSelectedDate((previous) => previous.slice(0, 7) === nextMonth ? previous : `${nextMonth}-01`);
  };

  const selectDate = (date) => setSelectedDate(dateKey(date));
  const tileContent = ({ date, view }) => {
    if (view !== 'month') return null;
    const rows = rehearsalsForDate(monthItems, dateKey(date));
    return rows.length ? <span className="activity-calendar-dot" aria-label={`${rows.length}개 합주`} /> : null;
  };

  const practiceSongs = Array.isArray(dashboard?.practice_songs) ? dashboard.practice_songs.slice(0, 5) : [];
  const upcoming = Array.isArray(dashboard?.upcoming_rehearsals) ? dashboard.upcoming_rehearsals.slice(0, 3) : [];
  const recentGallery = Array.isArray(dashboard?.recent_gallery) ? dashboard.recent_gallery.slice(0, 4) : [];
  const loadedCount = monthItems.length;
  const totalMonthItems = Number.isFinite(Number(pagination.total)) ? Number(pagination.total) : loadedCount;

  if (state.loading || (!dashboard && state.month !== month)) {
    return <section className="workspace-page band-activity-dashboard" aria-labelledby="band-dashboard-title"><h1 id="band-dashboard-title">{band.name}</h1><p aria-live="polite">대시보드를 불러오는 중입니다…</p></section>;
  }
  if (state.error && !dashboard) {
    return <section className="workspace-page band-activity-dashboard" aria-labelledby="band-dashboard-title"><h1 id="band-dashboard-title">{band.name}</h1><div className="activity-error" role="alert"><p>{state.error}</p><button type="button" className="secondary-btn" onClick={() => { setState({ month, loading: true, error: '', pageLoading: false, pageError: '', dashboard: null }); requestPage({ targetMonth: month, offset: 0 }); }}>다시 시도</button></div></section>;
  }

  return <section className="workspace-page band-activity-dashboard" aria-labelledby="band-dashboard-title">
    <header className="activity-header"><div><h1 id="band-dashboard-title">{band.name}</h1><p className="workspace-description">{band.description || '밴드의 곡과 합주 기록을 한곳에서 확인하세요.'}</p></div><Link className="secondary-btn activity-all-songs" to={`/bands/${encodeURIComponent(band.slug)}/songs`}>전체 곡 보기</Link></header>

    <section className="activity-card activity-featured" aria-labelledby="activity-featured-title">
      <div className="activity-card-heading"><h2 id="activity-featured-title">대표 사진</h2><Link to={`/bands/${encodeURIComponent(band.slug)}/gallery`}>갤러리</Link></div>
      {dashboard?.featured_gallery ? <DashboardImage image={dashboard.featured_gallery} kind="featured" workspaceKey={workspaceKey} month={month} failedUrls={failedUrls} onError={handleImageError} /> : <div className="activity-image-placeholder activity-image-placeholder-featured" role="img" aria-label="등록된 대표 사진이 없습니다">대표 사진이 없습니다</div>}
    </section>

    <div className="activity-stats" aria-label="밴드 통계">
      <div className="activity-stat"><strong>{Number(stats.total_songs) || 0}</strong><span>전체 곡</span></div>
      <div className="activity-stat"><strong>{Number(stats.total_rehearsals) || 0}</strong><span>전체 합주</span></div>
      <div className="activity-stat activity-status-stat"><span className="activity-status-heading">곡 상태</span><div className="activity-status-list">{statusRows.map(([status, label]) => <span className={`activity-status activity-status-${status.toLowerCase()}`} key={status}><span>{label}</span><strong>{Number(statusCounts[status]) || 0}</strong></span>)}</div></div>
    </div>

    <AnnouncementCard announcement={dashboard?.announcement} api={api} bandId={band.id} workspaceKey={workspaceKey} />

    <div className="activity-main-grid">
      <section className="activity-card activity-practice" aria-labelledby="activity-practice-title">
        <div className="activity-card-heading"><h2 id="activity-practice-title">연습할 곡</h2><Link to={`/bands/${encodeURIComponent(band.slug)}/songs`}>전체 목록</Link></div>
        {practiceSongs.length ? <ol className="activity-practice-list">{practiceSongs.map((song) => {
          const href = songHref(band, song);
          return <li key={song.id}>{href ? <Link to={href}><strong>{song.title || '제목 없음'}</strong><span>{song.artist || '아티스트 정보 없음'}</span></Link> : <div><strong>{song.title || '제목 없음'}</strong><span>{song.artist || '아티스트 정보 없음'}</span></div>}</li>;
        })}</ol> : <p className="activity-muted">현재 연습 중인 곡이 없습니다.</p>}
      </section>

      <section className="activity-card activity-upcoming" aria-labelledby="activity-upcoming-title">
        <div className="activity-card-heading"><h2 id="activity-upcoming-title">다가오는 합주</h2><Link to={`/bands/${encodeURIComponent(band.slug)}/rehearsals`}>전체 일정</Link></div>
        {upcoming.length ? <div className="activity-rehearsal-list">{upcoming.map((rehearsal) => <RehearsalSummary key={rehearsal.id} band={band} rehearsal={rehearsal} compact />)}</div> : <p className="activity-muted">예정된 합주가 없습니다.</p>}
      </section>

      <section className="activity-card activity-calendar-card" aria-labelledby="activity-calendar-title">
        <div className="activity-card-heading"><h2 id="activity-calendar-title">월별 합주</h2><span className="activity-muted">{loadedCount} / {totalMonthItems}건 표시</span></div>
        <div className="activity-calendar-layout">
          <Calendar
            activeStartDate={activeStartDate}
            value={selectedDateObject}
            onChange={selectDate}
            onActiveStartDateChange={changeMonth}
            tileContent={tileContent}
            locale="ko-KR"
            calendarType="gregory"
            formatDay={(locale, date) => date.getDate()}
          />
          <div className="activity-selected-day" aria-live="polite">
            <h3>{displayDate(selectedDate)}</h3>
            {pagination.has_more && <p className="activity-incomplete-notice">이 달의 일정 일부만 표시 중입니다. 선택한 날짜에 더 많은 합주가 있는지 확인하려면 일정을 더 불러와 주세요.</p>}
            {selectedItems.length ? <div className="activity-rehearsal-list">{selectedItems.map((rehearsal) => <RehearsalSummary key={rehearsal.id} band={band} rehearsal={rehearsal} />)}</div> : !pagination.has_more && <p className="activity-muted">이 날짜에 등록된 합주가 없습니다.</p>}
          </div>
        </div>
        {state.pageError && <div className="activity-inline-error" role="alert"><span>{state.pageError}</span><button type="button" className="activity-text-button" onClick={() => requestPage({ targetMonth: month, offset: pagination.next_offset ?? loadedCount, mode: 'append' })}>다시 시도</button></div>}
        {pagination.has_more && <button type="button" className="secondary-btn activity-load-more" disabled={state.pageLoading} onClick={() => requestPage({ targetMonth: month, offset: pagination.next_offset ?? loadedCount, mode: 'append' })}>{state.pageLoading ? '합주를 불러오는 중…' : `이 달 합주 더 불러오기 (${loadedCount}/${totalMonthItems})`}</button>}
      </section>

      <section className="activity-card activity-gallery" aria-labelledby="activity-gallery-title">
        <div className="activity-card-heading"><h2 id="activity-gallery-title">최근 갤러리</h2><Link to={`/bands/${encodeURIComponent(band.slug)}/gallery`}>사진 더 보기</Link></div>
        {recentGallery.length ? <div className="activity-gallery-grid">{recentGallery.map((image) => <DashboardImage key={image.id} image={image} kind="recent" workspaceKey={workspaceKey} month={month} failedUrls={failedUrls} onError={handleImageError} />)}</div> : <p className="activity-muted">표시할 사진이 없습니다.</p>}
      </section>
    </div>
  </section>;
};

export default BandActivityDashboard;
