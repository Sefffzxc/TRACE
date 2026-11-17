import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { schoolYearApi } from '../api/endpoints';
import { setSchoolYearScope } from '../lib/schoolYearScope';
import { useAuth } from './AuthContext';

const SchoolYearContext = createContext(null);

export function SchoolYearProvider({ children }) {
  const { isSuperAdmin } = useAuth();
  const [year, setYear] = useState(null);
  const [ready, setReady] = useState(isSuperAdmin);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const generation = useRef(0);
  const pendingRefresh = useRef(null);

  const accept = useCallback((next) => {
    generation.current += 1;
    setSchoolYearScope(next?.school_year_id);
    setYear(next || null);
    setReady(true);
    setError('');
  }, []);

  const refresh = useCallback(async () => {
    if (isSuperAdmin) return null;
    if (pendingRefresh.current) return pendingRefresh.current;
    const attempt = ++generation.current;
    const task = (async () => {
      try {
        const next = await schoolYearApi.current();
        if (mounted.current && attempt === generation.current) accept(next);
        return next;
      } catch (err) {
        if (mounted.current && attempt === generation.current) setError(err.message);
        return null;
      }
    })();
    pendingRefresh.current = task;
    task.finally(() => { if (pendingRefresh.current === task) pendingRefresh.current = null; });
    return task;
  }, [accept, isSuperAdmin]);

  useEffect(() => {
    mounted.current = true;
    if (isSuperAdmin) accept(null);
    else refresh();
    const onVisible = () => { if (document.visibilityState === 'visible') refresh(); };
    const onChanged = () => refresh();
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onChanged);
    window.addEventListener('trace:school-year-conflict', onChanged);
    const timer = window.setInterval(refresh, 60000);
    return () => {
      mounted.current = false;
      generation.current += 1;
      pendingRefresh.current = null;
      setSchoolYearScope(null);
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onChanged);
      window.removeEventListener('trace:school-year-conflict', onChanged);
    };
  }, [accept, refresh, isSuperAdmin]);

  const value = useMemo(() => ({ year, yearId: year?.school_year_id ?? null, ready, error, refresh, accept }), [year, ready, error, refresh, accept]);
  return <SchoolYearContext.Provider value={value}>{children}</SchoolYearContext.Provider>;
}

export function useSchoolYear() {
  const value = useContext(SchoolYearContext);
  if (!value) throw new Error('useSchoolYear must be used inside SchoolYearProvider.');
  return value;
}
