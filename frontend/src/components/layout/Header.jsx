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
  const { user, signOut, unitLabel, school } = useAuth();
  const { isDark, toggle } = useTheme();

  const [bellOpen, setBellOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState('');

  const bellRef = useOutsideClose(() => setBellOpen(false));
  const menuRef = useOutsideClose(() => setMenuOpen(false));

  // The lesson-progress page is titled with the school's own word for its
  // units, so it is resolved here rather than sitting in the static map above.
  const title =
    (pathname === '/pace' && `${unitLabel} Progress`) ||
    TITLES[pathname] ||
    (pathname.startsWith('/students/')
      ? 'Student Profile'
      : school?.short_name || school?.name || 'Monitoring');

  const submitSearch = (event) => {
    event.preventDefault();
    if (!query.trim()) return;
    navigate(`/students?q=${encodeURIComponent(query.trim())}`);
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

      <form className="header-search" onSubmit={submitSearch} role="search">
        <Search size={15} />
        <input
          className="input"
          type="search"
          placeholder="Search students…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search students"
        />
      </form>

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
                        {alert.subject} — {alert.paces_behind} PACE
                        {alert.paces_behind === 1 ? '' : 's'} behind
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
