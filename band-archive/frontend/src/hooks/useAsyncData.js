import { useState, useEffect, useCallback } from 'react';

const useAsyncData = (fetchFn, deps = [], { immediate = true } = {}) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(immediate);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchFn();
      setData(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, deps);

  useEffect(() => {
    if (immediate) {
      reload();
    }
  }, [reload]);

  return { data, setData, loading, error, reload };
};

export default useAsyncData;
