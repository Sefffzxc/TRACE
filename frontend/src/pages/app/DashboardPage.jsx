import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  GraduationCap,
  TrendingUp,
  UserCheck,
  Users,
} from 'lucide-react';
import { RiskDonut, SectionPaceChart, SubjectChart, TrendChart } from '../../components/charts/Charts';
import { chartColors } from '../../components/charts/chartTheme';
import { EmptyState, ErrorNote, RiskBadge } from '../../components/common/ui';
import { dashboardApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { IMAGES } from '../../config/images';

function Stat({ label, value, unit, icon: Icon, tone = '', foot }) {
  return (
    <div className="stat">
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        <span className={`stat-icon ${tone}`}>
          <Icon size={18} />
        </span>
      </div>
      <div className="stat-value">
        {value}
        {unit && <span className="unit">{unit}</span>}
      </div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { user, isAdmin } = useAuth();

  const [stats, setStats] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState(null);

  useEffect(() => {
    const image = new Image();
    image.src = IMAGES.dashboardBanner;
    image.onload = () => setBanner(IMAGES.dashboardBanner);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await dashboardApi.load();
        if (!cancelled) setStats(data);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="page">
        <div className="skeleton" style={{ height: 132, borderRadius: 'var(--radius-lg)' }} />
        <div className="grid grid-4">
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="skeleton" style={{ height: 118, borderRadius: 'var(--radius-lg)' }} />
          ))}
        </div>
        <div className="grid grid-2">
          <div className="skeleton" style={{ height: 320, borderRadius: 'var(--radius-lg)' }} />
          <div className="skeleton" style={{ height: 320, borderRadius: 'var(--radius-lg)' }} />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page">
        <ErrorNote message={error} />
      </div>
    );
  }

  const colors = chartColors();
  const tone = {
    low: colors.low,
    moderate: colors.moderate,
    high: colors.high,
    critical: colors.critical,
  };
  const totalRisk = stats.risk_distribution.reduce((sum, row) => sum + row.value, 0);
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  return (
    <div className="page">
      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <section className="hero">
        {banner && <div className="hero-photo" style={{ backgroundImage: `url(${banner})` }} aria-hidden="true" />}
        <div className="hero-inner">
          <div>
            <h1>
              {greeting}, {user?.first_name}.
            </h1>
            <p className="hero-sub">
              {stats.critical > 0
                ? `${stats.critical} student${stats.critical === 1 ? '' : 's'} need attention today. Start with the concerns list below.`
                : 'No students are in the critical band right now — a good day to check on the watch list.'}
            </p>
            <div className="hero-meta">
              {stats.school_year && (
                <span>
                  <CalendarDays size={13} /> S.Y. {stats.school_year}
                </span>
              )}
              <span>
                <Users size={13} /> {stats.total_students} students monitored
              </span>
              <span>
                <BookOpen size={13} /> {stats.total_subjects} subjects
              </span>
            </div>
          </div>
          <div className="hero-figure">
            <span className="v">{stats.average_pace.toFixed(0)}%</span>
            <span className="l">Average PACE completion</span>
          </div>
        </div>
      </section>

      {/* ── Key figures ──────────────────────────────────────────────────── */}
      <section className="grid grid-4">
        <Stat
          label="On track"
          value={stats.on_track}
          icon={CheckCircle2}
          tone="ok"
          foot={
            totalRisk > 0 ? `${Math.round((stats.on_track / totalRisk) * 100)}% of all students` : '—'
          }
        />
        <Stat
          label="At risk"
          value={stats.at_risk}
          icon={AlertTriangle}
          tone="warn"
          foot="Behind the expected PACE"
        />
        <Stat
          label="Critical"
          value={stats.critical}
          icon={AlertOctagon}
          tone="crit"
          foot="Needs intervention now"
        />
        {isAdmin ? (
          <Stat
            label="Pending approvals"
            value={stats.pending_approvals}
            icon={UserCheck}
            foot={
              stats.pending_approvals > 0 ? (
                <button
                  type="button"
                  className="link-button"
                  style={{
                    background: 'none',
                    border: 0,
                    padding: 0,
                    color: 'var(--brand-600)',
                    cursor: 'pointer',
                    font: 'inherit',
                  }}
                  onClick={() => navigate('/approvals')}
                >
                  Review requests →
                </button>
              ) : (
                'Nothing waiting'
              )
            }
          />
        ) : (
          <Stat
            label="Average attendance"
            value={stats.average_attendance.toFixed(0)}
            unit="%"
            icon={Activity}
            foot="Across your sections"
          />
        )}
      </section>

      {/* ── Charts ───────────────────────────────────────────────────────── */}
      <section className="grid grid-2">
        <div className="card chart-card">
          <div className="card-header">
            <div>
              <h2>Completion by section</h2>
              <div className="card-subtitle">Average share of the year&rsquo;s PACE booklets finished</div>
            </div>
          </div>
          <div className="card-body">
            <SectionPaceChart data={stats.pace_by_section} />
          </div>
        </div>

        <div className="card chart-card">
          <div className="card-header">
            <div>
              <h2>Risk distribution</h2>
              <div className="card-subtitle">Each student counted at their weakest subject</div>
            </div>
          </div>
          <div className="card-body">
            <RiskDonut data={stats.risk_distribution} total={totalRisk} />
            <div className="risk-key">
              {stats.risk_distribution.map((row) => (
                <div className="risk-key-row" key={row.key}>
                  <i style={{ background: tone[row.key] }} />
                  <span className="n">{row.name}</span>
                  <span className="v">{row.value}</span>
                  <span className="p">
                    {totalRisk ? `${Math.round((row.value / totalRisk) * 100)}%` : '0%'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-2">
        <div className="card chart-card">
          <div className="card-header">
            <div>
              <h2>Actual pace vs. expected</h2>
              <div className="card-subtitle">Where each subject sits against the curriculum plan</div>
            </div>
          </div>
          <div className="card-body">
            <TrendChart data={stats.trend} />
          </div>
        </div>

        <div className="card chart-card">
          <div className="card-header">
            <div>
              <h2>Completion by subject</h2>
              <div className="card-subtitle">Which subjects the school is carrying, and which are lagging</div>
            </div>
          </div>
          <div className="card-body">
            <SubjectChart data={stats.pace_by_subject} />
          </div>
        </div>
      </section>

      {/* ── Concerns + activity ──────────────────────────────────────────── */}
      <section className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Needs attention first</h2>
              <div className="card-subtitle">Highest risk scores across every monitored subject</div>
            </div>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/early-warning')}>
              All warnings <ArrowUpRight size={14} />
            </button>
          </div>
          <div className="card-body flush">
            {stats.top_concerns.length === 0 ? (
              <EmptyState
                icon={CheckCircle2}
                title="Everyone is keeping pace"
                description="No student is currently flagged. New warnings appear here the moment PACE progress is encoded."
              />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Student</th>
                      <th>Section</th>
                      <th>Subject</th>
                      <th className="right">Behind</th>
                      <th>Completion</th>
                      <th>Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.top_concerns.map((row) => (
                      <tr
                        key={row.warning_id}
                        className="clickable"
                        onClick={() => navigate(`/students/${row.student_id}`)}
                      >
                        <td className="cell-strong">{row.student_name}</td>
                        <td className="muted">{row.section}</td>
                        <td>{row.subject}</td>
                        <td className="right cell-strong">{row.paces_behind}</td>
                        <td style={{ minWidth: 130 }}>
                          <div className="progress-row">
                            <div className="progress">
                              <div
                                className={`progress-fill is-${row.risk_level}`}
                                style={{ width: `${row.pace_percent}%` }}
                              />
                            </div>
                            <span className="progress-value">{row.pace_percent.toFixed(0)}%</span>
                          </div>
                        </td>
                        <td>
                          <RiskBadge level={row.risk_level} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div>
              <h2>Recent activity</h2>
              <div className="card-subtitle">What changed in the system</div>
            </div>
          </div>
          <div className="card-body flush">
            {stats.recent_activity.length === 0 ? (
              <EmptyState icon={Activity} title="Nothing recorded yet" />
            ) : (
              <div className="feed">
                {stats.recent_activity.map((item, index) => (
                  <div className="feed-item" key={`${item.action}-${index}`}>
                    <span className="feed-dot">
                      {item.action.includes('pace') ? (
                        <TrendingUp size={15} />
                      ) : item.action.includes('student') ? (
                        <GraduationCap size={15} />
                      ) : (
                        <UserCheck size={15} />
                      )}
                    </span>
                    <span className="feed-body">
                      <span className="feed-title">{item.label}</span>
                      <span className="feed-detail">{item.detail || item.actor}</span>
                    </span>
                    <span className="feed-when">{item.when}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
