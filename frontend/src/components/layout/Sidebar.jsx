import { NavLink } from 'react-router-dom';
import {
  AlertTriangle,
  BookOpen,
  Brain,
  ClipboardList,
  Building2,
  CalendarDays,
  GraduationCap,
  LayoutDashboard,
  Settings,
  UserCheck,
  Users,
} from 'lucide-react';
import { Avatar } from '../common/ui';
import { SYSTEM } from '../../config/images';
import TraceMark from '../common/TraceMark';
import { useAuth } from '../../context/AuthContext';

export default function Sidebar({ collapsed, open, onNavigate, pendingCount, criticalCount }) {
  const { user, isAdmin, isSuperAdmin, school, tracksUnits, unitLabel } = useAuth();

  /**
   * The nav is built from the role rather than filtered after the fact, because
   * the three roles are looking at three different things.
   *
   * The system operator belongs to no school, so every monitoring screen would
   * have nothing to show them and the API refuses them anyway — they get the
   * schools console and their own account, and nothing else. Principals and
   * teachers both work inside one school; the difference between them is who
   * may change how the school is set up.
   */
  let items;

  if (isSuperAdmin) {
    items = [
      { to: '/schools', label: 'Schools', icon: Building2, group: 'Operations' },
      { to: '/account', label: 'Account', icon: UserCheck, group: 'Operations' },
    ];
  } else {
    items = [
      { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, group: 'Overview' },
      { to: '/students', label: 'Students', icon: Users, group: 'Monitoring' },
      /* Every school gets this one. Attendance and assessment marks are what
         the risk model actually reads, and they exist whether or not a school
         tracks lesson units — so this is never hidden. */
      { to: '/class-record', label: 'Class Record', icon: ClipboardList, group: 'Monitoring' },
      /* Lesson-unit progress is the part that varies, so it is the part that
         appears conditionally, named with the school's own word for it —
         "PACE Progress" at a School of Tomorrow school, "Module Progress"
         elsewhere, nothing at all at a school that tracks no units. */
      ...(tracksUnits
        ? [
            {
              to: '/pace',
              label: `${unitLabel} Progress`,
              icon: GraduationCap,
              group: 'Monitoring',
            },
          ]
        : []),
      {
        to: '/early-warning',
        label: 'Early Warning',
        icon: AlertTriangle,
        group: 'Monitoring',
        count: criticalCount,
      },
      { to: '/ai-risk', label: 'AI Risk Model', icon: Brain, group: 'Monitoring' },
      ...(isAdmin
        ? [
            { to: '/teachers', label: 'Teachers', icon: UserCheck, group: 'Administration' },
            {
              to: '/approvals',
              label: 'Approvals',
              icon: UserCheck,
              group: 'Administration',
              count: pendingCount,
            },
            {
              to: '/class-management',
              label: 'Class Management',
              icon: BookOpen,
              group: 'Administration',
            },
            { to: '/schedule', label: 'Timetable', icon: CalendarDays, group: 'Administration' },
            { to: '/school-settings', label: 'School Settings', icon: Settings, group: 'Administration' },
          ]
        : [{ to: '/schedule', label: 'My Timetable', icon: CalendarDays, group: 'Administration' }]),
    ];
  }

  let lastGroup = null;

  return (
    <aside
      className={`sidebar ${collapsed ? 'is-collapsed' : ''} ${open ? 'is-open' : ''}`.trim()}
      aria-label="Main navigation"
    >
      <div className="sidebar-head">
        <span className="sidebar-seal">
          <TraceMark size={24} />
        </span>
        <span className="sidebar-title">
          <span className="name">{SYSTEM.name}</span>
          {/* One deployment, many schools: under the product name sits the
              school the user actually belongs to. The operator belongs to none. */}
          <span className="sub">
            {isSuperAdmin ? 'Operations' : school?.short_name || school?.name || 'Monitoring'}
          </span>
        </span>
      </div>

      <nav className="sidebar-nav">
        {items.map((item) => {
          const showGroup = item.group !== lastGroup;
          lastGroup = item.group;
          const Icon = item.icon;
          return (
            <div key={item.to}>
              {showGroup && <div className="sidebar-group">{item.group}</div>}
              <NavLink
                to={item.to}
                data-label={item.label}
                className={({ isActive }) => `nav-item ${isActive ? 'is-active' : ''}`}
                onClick={onNavigate}
              >
                <Icon size={18} />
                <span className="nav-label">{item.label}</span>
                {item.count > 0 && <span className="nav-count">{item.count}</span>}
              </NavLink>
            </div>
          );
        })}
      </nav>

      <div className="sidebar-foot">
        <div className="sidebar-user">
          <Avatar
            name={`${user?.first_name || ''} ${user?.last_name || ''}`}
            src={user?.profile_pic}
            size="sm"
          />
          <span className="who">
            <div className="n">
              {user?.first_name} {user?.last_name}
            </div>
            <div className="r">{user?.role?.replace(/_/g, ' ')}</div>
          </span>
        </div>
      </div>
    </aside>
  );
}
