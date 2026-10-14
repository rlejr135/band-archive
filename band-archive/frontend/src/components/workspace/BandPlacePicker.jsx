import React, { useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import './BandPlacePicker.css';

const DEBOUNCE_MS = 350;
const RATE_LIMIT_COOLDOWN_MS = 30_000;
const normalizedQuery = (value) => String(value || '').trim().slice(0, 100);
const characterCount = (value) => [...value].length;

const BandPlacePickerInstance = ({ bandId, workspaceKey, api, onSelect }) => {
  const [query, setQuery] = useState('');
  const [state, setState] = useState({ query: '', status: 'idle', results: [], error: '' });
  const epoch = useRef(0);
  const activeController = useRef(null);
  const forbidden = useRef(false);
  const cooldownUntil = useRef(0);

  useEffect(() => {
    const requestEpoch = ++epoch.current;
    const searchQuery = normalizedQuery(query);
    let active = true;
    let controller;
    if (characterCount(searchQuery) < 2 || forbidden.current) return () => { active = false; epoch.current += 1; };

    const timer = setTimeout(async () => {
      if (!active || requestEpoch !== epoch.current) return;
      if (Date.now() < cooldownUntil.current) {
        setState({ query: searchQuery, status: 'rate-limited', results: [], error: '검색 요청이 많습니다. 잠시 기다린 뒤 검색어를 수정해 주세요.' });
        return;
      }
      controller = new AbortController();
      activeController.current = controller;
      setState({ query: searchQuery, status: 'loading', results: [], error: '' });
      try {
        const response = await api.requestJson(bandWorkspacePaths.searchPlaces(bandId, searchQuery), { signal: controller.signal });
        if (!active || controller.signal.aborted || requestEpoch !== epoch.current) return;
        const results = Array.isArray(response) ? response.filter((item) => item && typeof item === 'object' && (item.title || item.roadAddress || item.address)) : [];
        setState({ query: searchQuery, status: results.length ? 'results' : 'empty', results, error: '' });
      } catch (error) {
        if (!active || controller.signal.aborted || requestEpoch !== epoch.current) return;
        if ([401, 403, 404].includes(error?.status)) {
          forbidden.current = true;
          setState({ query: searchQuery, status: 'denied', results: [], error: '밴드 멤버만 장소를 검색할 수 있습니다. 장소를 직접 입력해 주세요.' });
        } else if (error?.status === 429) {
          cooldownUntil.current = Date.now() + RATE_LIMIT_COOLDOWN_MS;
          setState({ query: searchQuery, status: 'rate-limited', results: [], error: '검색 요청이 많습니다. 잠시 기다린 뒤 검색어를 수정해 주세요.' });
        } else {
          setState({ query: searchQuery, status: 'error', results: [], error: '장소 검색을 사용할 수 없습니다. 장소를 직접 입력해 주세요.' });
        }
      } finally {
        if (activeController.current === controller) activeController.current = null;
      }
    }, DEBOUNCE_MS);

    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
      if (activeController.current === controller) activeController.current = null;
      epoch.current += 1;
    };
  }, [api, bandId, query, workspaceKey]);

  const choose = (place) => {
    const title = String(place.title || '').trim();
    const address = String(place.roadAddress || place.address || '').trim();
    const location = title && address ? `${title} (${address})` : title || address;
    if (!location) return;
    epoch.current += 1;
    activeController.current?.abort();
    activeController.current = null;
    setState({ query: '', status: 'idle', results: [], error: '' });
    setQuery('');
    onSelect(location.slice(0, 200));
  };

  const currentState = state.query === normalizedQuery(query) ? state : null;
  const queryLength = characterCount(normalizedQuery(query));
  return <div className="band-place-picker">
    <label htmlFor="band-place-search">밴드 장소 검색</label>
    <input id="band-place-search" type="search" value={query} maxLength={100} autoComplete="off" placeholder="장소명 또는 주소 (2글자 이상)" onKeyDown={(event) => { if (event.key === 'Enter') event.preventDefault(); }} onChange={(event) => {
      epoch.current += 1;
      activeController.current?.abort();
      activeController.current = null;
      const nextQuery = event.target.value.slice(0, 100);
      const nextStateQuery = normalizedQuery(nextQuery);
      setState((previous) => previous.status === 'denied'
        ? { query: nextStateQuery, status: 'denied', results: [], error: '밴드 멤버만 장소를 검색할 수 있습니다. 장소를 직접 입력해 주세요.' }
        : { query: nextStateQuery, status: 'idle', results: [], error: '' });
      setQuery(nextQuery);
    }} />
    {!queryLength && <p className="workspace-muted">검색은 로그인한 밴드 멤버에게만 제공됩니다. 장소는 직접 입력해도 됩니다.</p>}
    {queryLength === 1 && <p className="workspace-muted">두 글자 이상 입력해 주세요. 장소는 직접 입력할 수 있습니다.</p>}
    {currentState?.status === 'loading' && <p className="workspace-muted" aria-live="polite">장소를 검색하고 있습니다…</p>}
    {currentState?.status === 'empty' && <p className="workspace-muted" role="status">검색 결과가 없습니다. 장소를 직접 입력해 주세요.</p>}
    {currentState?.error && <p className="workspace-error" role="status">{currentState.error}</p>}
    {currentState?.status === 'results' && <ul className="band-place-results" aria-label="장소 검색 결과">{currentState.results.map((place, index) => <li key={`${place.title || ''}:${place.roadAddress || place.address || ''}:${index}`}><button type="button" onClick={() => choose(place)}><strong>{String(place.title || '')}</strong><span>{String(place.roadAddress || place.address || '')}</span></button></li>)}</ul>}
  </div>;
};

const BandPlacePicker = (props) => <BandPlacePickerInstance key={`${props.bandId}:${props.workspaceKey}`} {...props} />;

export default BandPlacePicker;
