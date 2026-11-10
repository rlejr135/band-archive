/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useRef, useState } from 'react';
import { strictPositiveId } from './bandDashboardState.js';
import { useWorkspace } from './WorkspaceContext.jsx';
import { requestOptions } from './workspaceReadSupport.js';

const notFound = '요청한 항목을 찾을 수 없습니다.';
const empty = { id: null, loading: false, error: '', item: null };

// Keep query navigation separate from each screen's local selection and form state.
export const useQueryDetail = ({ identity, detailValue, pathForId, extract }) => {
  const { api, band } = useWorkspace();
  const detailId = detailValue == null ? null : strictPositiveId(detailValue);
  const path = detailId ? pathForId(detailId) : null;
  const requestKey = JSON.stringify([identity, band.id, detailValue, path]);
  const [result, setResult] = useState({ key: null, ...empty });
  const generation = useRef(0);

  useEffect(() => {
    const token = ++generation.current;
    if (detailValue == null || !detailId) return undefined;
    const controller = new AbortController();
    setResult({ key: requestKey, id: detailId, loading: true, error: '', item: null });
    const current = () => !controller.signal.aborted && token === generation.current;
    api.requestJson(path, requestOptions(controller.signal)).then((response) => {
      if (!current()) return;
      const item = extract(response);
      const sameBand = item?.band_id == null || Number(item.band_id) === Number(band.id);
      const valid = item && Number(item.id) === detailId && sameBand;
      setResult({ key: requestKey, id: detailId, loading: false, error: valid ? '' : notFound, item: valid ? item : null });
    }).catch(() => {
      if (current()) setResult({ key: requestKey, id: detailId, loading: false, error: notFound, item: null });
    });
    return () => controller.abort();
  }, [api, band.id, detailId, detailValue, extract, path, requestKey]);

  if (detailValue == null) return empty;
  if (!detailId) return { ...empty, error: notFound };
  // Never expose the previous account/target's item while its replacement effect starts.
  if (result.key !== requestKey) return { ...empty, id: detailId, loading: true };
  return result;
};
