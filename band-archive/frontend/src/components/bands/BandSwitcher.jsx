import { useId } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useBands } from '../../context/BandContext.jsx';
import './BandSwitcher.css';

const BandSwitcher = ({ currentSlug, className = '', accountMode = false }) => {
  const generatedId = useId();
  const route = useParams();
  const navigate = useNavigate();
  const { activeBands = [], selectedBand, selectBand, loading = false, error = '', reload = () => {} } = useBands();
  const requestedSlug = currentSlug ?? route.bandSlug;
  const currentBand = requestedSlug
    ? activeBands.find((band) => band.slug === requestedSlug)
    : selectedBand;
  const value = accountMode ? '' : currentBand ? String(currentBand.id) : '';

  const changeBand = (event) => {
    const band = activeBands.find((item) => String(item.id) === event.target.value);
    if (!band?.slug) return;
    selectBand?.(band.id);
    navigate(`/bands/${encodeURIComponent(band.slug)}`);
  };

  const empty = !loading && !error && activeBands.length === 0;
  return <div className={`band-switcher${className ? ` ${className}` : ''}`} aria-busy={loading}>
    <label htmlFor={`band-switcher-${generatedId}`}>빠른 밴드 전환</label>
    {empty ? <Link className="band-switcher-empty" to="/bands/discover"><span>가입한 밴드 없음</span><strong>밴드 찾기 <span aria-hidden="true">↗</span></strong></Link> : <select id={`band-switcher-${generatedId}`} aria-label="빠른 밴드 전환" value={value} disabled={loading || activeBands.length === 0} onChange={changeBand}>
      <option value="" disabled>{loading ? '밴드 목록 불러오는 중' : error ? '밴드 목록 확인 필요' : requestedSlug && !currentBand ? '현재 공간' : '밴드 선택'}</option>
      {activeBands.map((band) => <option key={band.id} value={String(band.id)}>{band.name}</option>)}
    </select>}
    {error && <button type="button" className="band-switcher-retry" onClick={reload}>목록 다시 불러오기</button>}
  </div>;
};

export default BandSwitcher;
