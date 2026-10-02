import { useCallback, useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Header from './Header';
import Sidebar from './Sidebar';
import { schoolYearApi, staffApi, warningApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';

const COLLAPSE_KEY = 'lbca.sidebar-collapsed';

/**
 * The signed-in frame. It also owns the two counters the chrome needs —
 * pending approvals and critical warnings — so every page does not have to
 * fetch them itself.
 */
export default function AppShell() {
  const { isAdmin, isSuperAdmin, tracksUnits, unitLabel } = useAuth();
  const { pathname } = useLocation();

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === '1',
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [schoolYear, setSchoolYear] = useState(null);

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
  }, [collapsed]);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const loadChrome = useCallback(async () => {
    // The system operator belongs to no school, so there are no warnings and
    // no school year to fetch — and the API would rightly refuse both. Asking
    // anyway would mean two failing requests every two minutes, which makes a
    // deliberate boundary look like a broken page in the network tab.
    if (isSuperAdmin) {
      setAlerts([]);
      setSchoolYear(null);
      setPendingCount(0);
      return;
    }
    try {
      const [warnings, year] = await Promise.all([
        warningApi.list({ risk_level: 'critical,high' }),
        schoolYearApi.current(),
      ]);
      setAlerts(warnings || []);
      setSchoolYear(year?.year || null);
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
  }, [isAdmin, isSuperAdmin]);

  useEffect(() => {
    loadChrome();
    const timer = setInterval(loadChrome, 120000);
    return () => clearInterval(timer);
  }, [loadChrome]);

  const criticalCount = useMemo(
    () => alerts.filter((alert) => alert.risk_level === 'critical').length,
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
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((value) => !value)}
          onOpenMobile={() => setMobileOpen(true)}
          alerts={alerts}
          schoolYear={schoolYear}
        />
        <div className="content">
          <Outlet context={{ refreshChrome: loadChrome }} />
        </div>
      </div>
    </div>
  );
}
