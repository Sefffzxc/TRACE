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
import {
  PeriodGradeChart,
  RiskDonut,
  SectionGradeChart,
  SectionPaceChart,
  SubjectChart,
  SubjectGradeChart,
  TrendChart,
} from '../../components/charts/Charts';
import { chartColors } from '../../components/charts/chartTheme';
import { EmptyState, ErrorNote, RiskBadge } from '../../components/common/ui';
import { dashboardApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { IMAGES } from '../../config/images';
import AdminDashboard from './AdminDashboard';

/* A figure card. With `to` it is a button that opens the page behind the
   number, already filtered to what the number counts. */
function Stat({ label, value, unit, icon: Icon, tone = '', foot, to }) {
  const navigate = useNavigate();
  const Tag = to ? 'button' : 'div';
  return (
    <Tag
      className={`stat${to ? ' is-link' : ''}`}
      {...(to ? { type: 'button', onClick: () => navigate(to) } : {})}
    >
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
    </Tag>
  );
}

/** Teachers follow their learners; principals follow the school and its
    teachers, so each role gets its own dashboard. */
export default function DashboardPage() {
  const { isAdmin } = useAuth();
  return isAdmin ? <AdminDashboard /> : <TeacherDashboard />;
}

function TeacherDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();

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
  const levelCount = (key) => stats.risk_distribution.find((row) => row.key === key)?.value ?? 0;
  /* A school that tracks units (PACE booklets) is read on completion; every
     other school is read on its class record. The answer comes with the
     figures, so the page never draws one kind of school with the other's
     charts while the school lookup is still in flight. */
  const units = stats.tracks_units;
  const passing = stats.passing_mark;
  const unitsPlural = stats.unit_label_plural;
  const periodLabel = stats.grading_period_label.toLowerCase();
  const belowPassing = stats.standing_by_subject.reduce((sum, row) => sum + row.below, 0);
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
                ? `${stats.critical} student${stats.critical === 1 ? '' : 's'} need attention today.`
                : 'No critical students today.'}
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
            {units ? (
              <>
                <span className="v">{stats.average_pace.toFixed(0)}%</span>
                <span className="l">Average completion</span>
              </>
            ) : (
              <>
                <span className="v">{stats.average_standing ? stats.average_standing.toFixed(0) : '—'}</span>
                <span className="l">Average grade</span>
              </>
            )}
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
          to="/students?risk=low"
        />
        <Stat
          label="At risk"
          value={stats.at_risk}
          icon={AlertTriangle}
          tone="warn"
          foot={`${levelCount('high')} at risk · ${levelCount('moderate')} watch`}
          to="/early-warning?level=at-risk"
        />
        <Stat
          label="Critical"
          value={stats.critical}
          icon={AlertOctagon}
          tone="crit"
          foot="Needs intervention now"
          to="/early-warning?level=critical"
        />
        {units ? (
          <Stat
            label="Average completion"
            value={stats.average_pace.toFixed(0)}
            unit="%"
            icon={Activity}
            foot={`Of the year's ${unitsPlural.toLowerCase()}`}
            to="/pace"
          />
        ) : (
          <Stat
            label="Below passing"
            value={belowPassing}
            icon={Activity}
            tone={belowPassing > 0 ? 'warn' : ''}
            foot={`Subject grades under ${passing} in your sections`}
            to="/early-warning"
          />
        )}
      </section>

      {/* ── Charts ───────────────────────────────────────────────────────── */}
      <section className="grid grid-2">
        <div className="card chart-card">
          <div className="card-header">
            <div>
              <h2>{units ? 'Completion by section' : 'Average grade by section'}</h2>
              <div className="card-subtitle">
                {units
                  ? `Share of ${unitsPlural.toLowerCase()} finished`
                  : `Passing mark: ${passing}`}
              </div>
            </div>
          </div>
          <div className="card-body">
            {units ? (
              <SectionPaceChart data={stats.pace_by_section} />
            ) : (
              <SectionGradeChart data={stats.standing_by_section} passingMark={passing} />
            )}
          </div>
        </div>

        <div className="card chart-card">
          <div className="card-header">
            <div>
              <h2>Risk distribution</h2>
              <div className="card-subtitle">By weakest subject</div>
            </div>
          </div>
          <div className="card-body">
            <RiskDonut
              data={stats.risk_distribution}
              total={totalRisk}
              emptyMessage={units ? `No ${unitsPlural} records yet.` : 'No grades recorded yet.'}
            />
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
              <h2>{units ? 'Actual pace vs. expected' : `Grades by ${periodLabel}`}</h2>
              <div className="card-subtitle">
                {units
                  ? 'Against the plan'
                  : `Average grade per ${periodLabel}`}
              </div>
            </div>
          </div>
          <div className="card-body">
            {units ? (
              <TrendChart data={stats.trend} />
            ) : (
              <PeriodGradeChart data={stats.standing_by_period} />
            )}
          </div>
        </div>

        <div className="card chart-card">
          <div className="card-header">
            <div>
              <h2>{units ? 'Completion by subject' : 'Average grade by subject'}</h2>
              <div className="card-subtitle">Strongest and weakest subjects</div>
            </div>
          </div>
          <div className="card-body">
            {units ? (
              <SubjectChart data={stats.pace_by_subject} />
            ) : (
              <SubjectGradeChart data={stats.standing_by_subject} passingMark={passing} />
            )}
          </div>
        </div>
      </section>

      {/* ── Concerns + activity ──────────────────────────────────────────── */}
      <section className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Needs attention first</h2>
              <div className="card-subtitle">Highest risk first</div>
            </div>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/early-warning')}>
              All warnings <ArrowUpRight size={14} />
            </button>
          </div>
          <div className="card-body flush">
            {stats.top_concerns.length === 0 ? (
              <EmptyState
                icon={CheckCircle2}
                title={units ? 'Everyone is keeping pace' : 'No one is flagged'}
                description="No student is flagged."
              />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Student</th>
                      <th>Section</th>
                      <th>Subject</th>
                      {units ? (
                        <>
                          <th className="right">Behind</th>
                          <th>Completion</th>
                        </>
                      ) : (
                        <th>Grade</th>
                      )}
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
                        {units ? (
                          <>
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
                          </>
                        ) : (
                          <td>
                            <span className="cell-strong">
                              {row.standing == null ? '—' : row.standing.toFixed(0)}
                            </span>
                            {row.basis === 'coursework' && <span className="muted"> so far</span>}
                            {row.basis === 'year' && <span className="muted"> year avg.</span>}
                          </td>
                        )}
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
