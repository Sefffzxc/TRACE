import { NavLink } from 'react-router-dom';
import {
  AlertTriangle,
  BookOpen,
  Brain,
  CalendarDays,
  GraduationCap,
  LayoutDashboard,
  UserCheck,
  Users,
} from 'lucide-react';
import { Avatar } from '../common/ui';
import { SCHOOL } from '../../config/images';
import { useAuth } from '../../context/AuthContext';

export default function Sidebar({ collapsed, open, onNavigate, pendingCount, criticalCount }) {
  const { user, isAdmin } = useAuth();

  const items = [
    { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, group: 'Overview' },
    { to: '/students', label: 'Students', icon: Users, group: 'Monitoring' },
    { to: '/pace', label: 'PACE Progress', icon: GraduationCap, group: 'Monitoring' },
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
        ]
      : [{ to: '/schedule', label: 'My Timetable', icon: CalendarDays, group: 'Administration' }]),
  ];

  let lastGroup = null;

  return (
    <aside
      className={`sidebar ${collapsed ? 'is-collapsed' : ''} ${open ? 'is-open' : ''}`.trim()}
      aria-label="Main navigation"
    >
      <div className="sidebar-head">
        <span className="sidebar-seal">
          <GraduationCap size={21} />
        </span>
        <span className="sidebar-title">
          <span className="name">{SCHOOL.shortName}</span>
          <span className="sub">Monitoring</span>
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
            <div className="r">{user?.role}</div>
          </span>
        </div>
      </div>
    </aside>
  );
}
