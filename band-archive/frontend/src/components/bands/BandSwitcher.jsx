import { useId } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
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

  return <div className={`band-switcher${className ? ` ${className}` : ''}`}>
    <label htmlFor={`band-switcher-${generatedId}`}>빠른 밴드 전환</label>
    <select id={`band-switcher-${generatedId}`} aria-label="빠른 밴드 전환" value={value} disabled={loading || activeBands.length === 0} onChange={changeBand}>
      <option value="" disabled>{loading ? '밴드 목록 불러오는 중' : requestedSlug && !currentBand ? '현재 공간' : '밴드 선택'}</option>
      {activeBands.map((band) => <option key={band.id} value={String(band.id)}>{band.name}</option>)}
    </select>
    {error && <button type="button" className="band-switcher-retry" onClick={reload}>목록 다시 불러오기</button>}
  </div>;
};

export default BandSwitcher;
