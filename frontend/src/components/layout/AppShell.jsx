import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Header from './Header';
import Sidebar from './Sidebar';
import { staffApi, warningApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { SchoolYearProvider, useSchoolYear } from '../../context/SchoolYearContext';
import { ErrorNote, TableSkeleton } from '../common/ui';

const COLLAPSE_KEY = 'trace.sidebar-collapsed';

/**
 * The signed-in frame. It also owns the two counters the chrome needs —
 * pending approvals and critical warnings — so every page does not have to
 * fetch them itself.
 */
export default function AppShell() {
  return <SchoolYearProvider><SchoolYearShell /></SchoolYearProvider>;
}

function SchoolYearShell() {
  const { isAdmin, isSuperAdmin } = useAuth();
  const { year, yearId, ready, error: yearError, refresh: refreshYear } = useSchoolYear();
  const { pathname } = useLocation();

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === '1',
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);
  const activeYear = useRef(yearId);
  activeYear.current = yearId;

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
  }, [collapsed]);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const loadChrome = useCallback(async () => {
    const capturedYear = yearId;
    // The system operator belongs to no school, so there are no warnings and
    // no school year to fetch — and the API would rightly refuse both. Asking
    // anyway would mean two failing requests every two minutes, which makes a
    // deliberate boundary look like a broken page in the network tab.
    if (isSuperAdmin) {
      setAlerts([]);
      setPendingCount(0);
      return;
    }
    try {
      // Student warnings are the teachers' to act on; the principal's screens
      // follow the school and its teachers, so their bell carries none.
      const warnings = isAdmin ? [] : await warningApi.list({ risk_level: 'critical,high', mine: true });
      if (activeYear.current === capturedYear) setAlerts(warnings || []);
    } catch {
      /* The chrome degrades quietly — the pages surface their own errors. */
    }
    if (isAdmin) {
      try {
        const pending = await staffApi.pending();
        setPendingCount(pending?.length || 0);
      } catch {
        /* ignore */
      }
    }
  }, [isAdmin, isSuperAdmin, yearId]);

  useEffect(() => {
    setAlerts([]);
    if (!ready) return undefined;
    loadChrome();
    const timer = setInterval(loadChrome, 120000);
    return () => clearInterval(timer);
  }, [loadChrome, ready]);

  // Critical STUDENTS, as on the dashboard and Early Warning tiles, not
  // critical subject flags: one learner failing two subjects counts once.
  const criticalCount = useMemo(
    () => new Set(alerts.filter((a) => a.risk_level === 'critical').map((a) => a.student_id)).size,
    [alerts],
  );

  return (
    <div className="shell">
      <Sidebar
        collapsed={collapsed}
        open={mobileOpen}
        onNavigate={() => setMobileOpen(false)}
        pendingCount={pendingCount}
        criticalCount={criticalCount}
      />

      {mobileOpen && (
        <div className="sidebar-scrim no-print" onClick={() => setMobileOpen(false)} aria-hidden="true" />
      )}

      <div className="main">
        <Header
          key={yearId ?? 'no-current-year'}
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((value) => !value)}
          onOpenMobile={() => setMobileOpen(true)}
          alerts={alerts}
          schoolYear={year?.year || null}
        />
        <div className="content">
          {!ready ? (
            <>
              <ErrorNote message={yearError} />
              {yearError ? <button type="button" className="btn btn-secondary" onClick={refreshYear}>Retry loading school year</button> : <TableSkeleton rows={5} />}
            </>
          ) : (
            // Recreate page-local selections, import previews and autosave
            // writers together; no old-year state can remain on screen.
            <div key={yearId ?? 'no-current-year'}>
              <Outlet context={{ refreshChrome: loadChrome }} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
