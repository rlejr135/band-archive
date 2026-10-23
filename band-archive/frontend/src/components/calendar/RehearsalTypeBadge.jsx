import { normalizeRehearsalType, rehearsalTypeLabel } from '../../services/rehearsalTypes.js';
import './RehearsalTypeBadge.css';

const RehearsalTypeBadge = ({ type }) => {
  const normalized = normalizeRehearsalType(type);
  return <span className="rehearsal-type-badge" data-rehearsal-type={normalized}>{rehearsalTypeLabel(normalized)}</span>;
};

export default RehearsalTypeBadge;
