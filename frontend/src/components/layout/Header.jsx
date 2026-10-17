import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Bell,
  ChevronDown,
  LogOut,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Sun,
  UserCircle,
} from 'lucide-react';
import { Avatar } from '../common/ui';
import { staffApi, studentApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';

const TITLES = {
  '/dashboard': 'Dashboard',
  '/students': 'Students',
  '/class-record': 'Class Record',
  '/early-warning': 'Early Warning',
  '/teachers': 'Teachers',
  '/approvals': 'Account Approvals',
  '/class-management': 'Class Management',
  '/account': 'Account Settings',
  '/ai-risk': 'AI Risk Model',
  '/schools': 'Schools',
  '/school-settings': 'School Settings',
};

function useOutsideClose(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const handler = (event) => {
      if (ref.current && !ref.current.contains(event.target)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);
  return ref;
}

export default function Header({ collapsed, onToggleCollapse, onOpenMobile, alerts = [], schoolYear }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const {
    user, signOut, unitLabel, unitLabelPlural, school, tracksUnits, passingMark, isAdmin, isSuperAdmin,
  } = useAuth();
  const { isDark, toggle } = useTheme();

  const [bellOpen, setBellOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState('');

  /* Search finds learners for a teacher and teachers for a principal — what
     each actually works with. Matches show as the user types; Enter opens
     the full list filtered. The directory is fetched once and filtered here,
     so typing never waits on the network. */
  const [directory, setDirectory] = useState(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [active, setActive] = useState(0);
  const searchRef = useOutsideClose(() => setSearchOpen(false));
  const searchTarget = isAdmin ? 'teachers' : 'students';
  const showSearch = pathname === '/dashboard' && !isSuperAdmin;

  // Refreshed on every focus so a learner added a minute ago is findable;
  // the previous list stays usable while the new one loads.
  const loadDirectory = async () => {
    try {
      const rows = isAdmin ? await staffApi.list() : await studentApi.list();
      setDirectory(rows || []);
    } catch {
      setDirectory((current) => current || []);
    }
  };

  const needle = query.trim().toLowerCase();
  const matches =
    needle && directory
      ? directory
          .filter((row) =>
            [row.full_name, row.login_id, row.email]
              .filter(Boolean)
              .some((text) => String(text).toLowerCase().includes(needle)),
          )
          .slice(0, 6)
      : [];

  const openMatch = (row) => {
    setSearchOpen(false);
    setQuery('');
    if (isAdmin) navigate(`/teachers?q=${encodeURIComponent(row.full_name)}`);
    else navigate(`/students/${row.student_id}`);
  };

  const bellRef = useOutsideClose(() => setBellOpen(false));
  const menuRef = useOutsideClose(() => setMenuOpen(false));

  // The lesson-progress page is titled with the school's own word for its
  // units, so it is resolved here rather than sitting in the static map above.
  const title =
    (pathname === '/pace' && `${unitLabel} Progress`) ||
    TITLES[pathname] ||
    (pathname.startsWith('/students/')
      ? 'Student Profile'
      : school?.name || 'Monitoring');

  const submitSearch = (event) => {
    event.preventDefault();
    // A highlighted suggestion opens directly; otherwise the full list,
    // filtered by what was typed.
    if (searchOpen && matches[active]) {
      openMatch(matches[active]);
      return;
    }
    if (!query.trim()) return;
    setSearchOpen(false);
    navigate(`/${searchTarget}?q=${encodeURIComponent(query.trim())}`);
    setQuery('');
  };

  return (
    <header className="header">
      <button
        type="button"
        className="btn-icon no-print only-mobile"
        onClick={onOpenMobile}
        aria-label="Open navigation"
      >
        <Menu size={19} />
      </button>

      <button
        type="button"
        className="btn-icon no-print hide-mobile"
        onClick={onToggleCollapse}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? <PanelLeftOpen size={19} /> : <PanelLeftClose size={19} />}
      </button>

      <div className="header-title">
        <span className="h">{title}</span>
      </div>

      {schoolYear && <span className="header-chip hide-mobile">S.Y. {schoolYear}</span>}

      {/* Only on the dashboard: every other page has its own search, and two
          search boxes on one screen is one too many. The system operator has
          no students or teachers to search. */}
      {!showSearch && <span className="header-spacer" />}
      {showSearch && (
        <form className="header-search" onSubmit={submitSearch} role="search" ref={searchRef}>
          <Search size={15} />
          <input
            className="input"
            type="search"
            placeholder={`Search ${searchTarget}…`}
            value={query}
            onFocus={() => {
              loadDirectory();
              setSearchOpen(true);
            }}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
              setSearchOpen(true);
            }}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                setActive((i) => Math.min(i + 1, Math.max(matches.length - 1, 0)));
              } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                setActive((i) => Math.max(i - 1, 0));
              } else if (event.key === 'Escape') {
                setSearchOpen(false);
              }
            }}
            aria-label={`Search ${searchTarget}`}
            aria-expanded={searchOpen && needle.length > 0}
            autoComplete="off"
          />
          {searchOpen && needle && (
            <div className="dropdown search-results" role="listbox">
              {directory === null ? (
                <div className="search-empty">Searching…</div>
              ) : matches.length === 0 ? (
                <div className="search-empty">No {searchTarget} match &ldquo;{query.trim()}&rdquo;</div>
              ) : (
                matches.map((row, index) => (
                  <button
                    key={row.student_id ?? row.id}
                    type="button"
                    role="option"
                    aria-selected={index === active}
                    className={`dropdown-item${index === active ? ' is-active' : ''}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => openMatch(row)}
                  >
                    <Avatar name={row.full_name} src={row.photo} size="sm" />
                    <span className="search-result-text">
                      <span className="search-result-name">{row.full_name}</span>
                      <span className="search-result-sub">
                        {isAdmin
                          ? row.email || row.role
                          : [row.section_name, row.grade_level_name].filter(Boolean).join(' · ') ||
                            'Not enrolled'}
                      </span>
                    </span>
                  </button>
                ))
              )}
              {matches.length > 0 && (
                <button type="submit" className="dropdown-item search-all">
                  See all results for &ldquo;{query.trim()}&rdquo;
                </button>
              )}
            </div>
          )}
        </form>
      )}

      <div className="header-actions">
        <button
          type="button"
          className="btn-icon"
          onClick={toggle}
          aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
        >
          {isDark ? <Sun size={18} /> : <Moon size={18} />}
        </button>

        <div className="bell" ref={bellRef}>
          <button
            type="button"
            className="btn-icon"
            onClick={() => setBellOpen((value) => !value)}
            aria-label={`Alerts (${alerts.length})`}
          >
            <Bell size={18} />
            {alerts.length > 0 && <span className="dot">{alerts.length > 9 ? '9+' : alerts.length}</span>}
          </button>

          {bellOpen && (
            <div className="dropdown">
              <div className="dropdown-head">
                <span>Students needing attention</span>
                <span>{alerts.length}</span>
              </div>
              {alerts.length === 0 ? (
                <div style={{ padding: '1.5rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                  Nothing urgent right now.
                </div>
              ) : (
                alerts.slice(0, 8).map((alert) => (
                  <div
                    key={alert.warning_id ?? `${alert.student_id}-${alert.subject}`}
                    className="notice"
                    role="button"
                    tabIndex={0}
                    onClick={() => {
                      setBellOpen(false);
                      navigate(`/students/${alert.student_id}`);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') navigate(`/students/${alert.student_id}`);
                    }}
                  >
                    <span className={`notice-icon ${alert.risk_level === 'critical' ? 'critical' : 'high'}`}>
                      <AlertTriangle size={16} />
                    </span>
                    <span className="notice-body">
                      <span className="notice-title">{alert.student_name}</span>
                      <span className="notice-text">
                        {/* Each kind of school in its own terms: booklets behind,
                            or the current grade against the passing mark. */}
                        {tracksUnits
                          ? `${alert.subject} — ${alert.paces_behind} ${
                              alert.paces_behind === 1 ? unitLabel : unitLabelPlural
                            } behind`
                          : alert.standing == null
                            ? alert.subject
                            : `${alert.subject} — ${Math.round(alert.standing)}${
                                alert.standing < passingMark
                                  ? ` (${Math.round(passingMark - alert.standing)} below ${passingMark})`
                                  : ''
                              }`}
                      </span>
                      <span className="notice-time">{alert.section || '—'}</span>
                    </span>
                  </div>
                ))
              )}
              <button
                type="button"
                className="dropdown-item"
                style={{ justifyContent: 'center', fontWeight: 600 }}
                onClick={() => {
                  setBellOpen(false);
                  navigate('/early-warning');
                }}
              >
                View all warnings
              </button>
            </div>
          )}
        </div>

        <div style={{ position: 'relative' }} ref={menuRef}>
          <button
            type="button"
            className="user-button"
            onClick={() => setMenuOpen((value) => !value)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            <Avatar
              name={`${user?.first_name || ''} ${user?.last_name || ''}`}
              src={user?.profile_pic}
              size="sm"
            />
            <span className="who">
              <span className="n">
                {user?.first_name} {user?.last_name}
              </span>
              <span className="r">{user?.role}</span>
            </span>
            <ChevronDown size={15} style={{ color: 'var(--text-faint)' }} />
          </button>

          {menuOpen && (
            <div className="dropdown narrow" role="menu">
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setMenuOpen(false);
                  navigate('/account');
                }}
              >
                <UserCircle size={16} /> My profile
              </button>
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setMenuOpen(false);
                  navigate('/account?tab=security');
                }}
              >
                <Settings size={16} /> Security
              </button>
              <div className="dropdown-sep" />
              <button
                type="button"
                className="dropdown-item danger"
                onClick={async () => {
                  setMenuOpen(false);
                  await signOut();
                  navigate('/login', { replace: true });
                }}
              >
                <LogOut size={16} /> Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
