import { createElement, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  GraduationCap,
  Layers,
  UserCheck,
  Users,
} from 'lucide-react';
import { EmptyState, ErrorNote } from '../../components/common/ui';
import { dashboardApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { IMAGES } from '../../config/images';

/**
 * The principal's dashboard: the school and its teachers, not its learners.
 * Following individual learners is the teachers' job; the principal sees how
 * far each teacher's classes are graded, who has gone quiet, and what in the
 * school's set-up still needs doing.
 */

function Stat({ label, value, icon, tone = '', foot }) {
  return (
    <div className="stat">
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        <span className={`stat-icon ${tone}`}>{createElement(icon, { size: 18 })}</span>
      </div>
      <div className="stat-value">{value}</div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  );
}

/** Green when nearly done, amber partway, red barely started. */
function progressTone(percent) {
  if (percent >= 75) return 'low';
  if (percent >= 40) return 'moderate';
  return 'critical';
}

export default function AdminDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [data, setData] = useState(null);
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
        const result = await dashboardApi.admin();
        if (!cancelled) setData(result);
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
        <div className="skeleton" style={{ height: 320, borderRadius: 'var(--radius-lg)' }} />
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

  const { counts, attention } = data;
  const termName = `${data.term_label} ${data.term}`;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const attentionCount =
    attention.quiet_teachers.length +
    attention.sections_without_adviser.length +
    attention.classes_not_started_total;

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
              {attentionCount > 0
                ? `${attentionCount} item${attentionCount === 1 ? '' : 's'} need${
                    attentionCount === 1 ? 's' : ''
                  } your attention. See the list below.`
                : 'Everything is on track.'}
            </p>
            <div className="hero-meta">
              {data.school_year && (
                <span>
                  <CalendarDays size={13} /> S.Y. {data.school_year}
                </span>
              )}
              <span>
                <ClipboardCheck size={13} /> Grading {termName}
              </span>
            </div>
          </div>
          <div className="hero-figure">
            <span className="v">
              {data.grading_progress == null ? '—' : `${data.grading_progress.toFixed(0)}%`}
            </span>
            <span className="l">{termName} graded</span>
          </div>
        </div>
      </section>

      {/* ── The school at a glance ───────────────────────────────────────── */}
      <section className="grid grid-4">
        <Stat label="Teachers" value={counts.teachers} icon={Users} foot="Approved and active" />
        <Stat
          label="Pending approvals"
          value={counts.pending_approvals}
          icon={UserCheck}
          tone={counts.pending_approvals > 0 ? 'warn' : ''}
          foot={
            counts.pending_approvals > 0 ? (
              <button type="button" className="link-button" onClick={() => navigate('/approvals')}>
                Review requests →
              </button>
            ) : (
              'Nothing waiting'
            )
          }
        />
        <Stat
          label="Sections"
          value={counts.sections}
          icon={Layers}
          foot={`${counts.subjects} subject${counts.subjects === 1 ? '' : 's'} offered`}
        />
        <Stat label="Learners enrolled" value={counts.learners} icon={GraduationCap} foot="This school year" />
      </section>

      {/* ── Teachers' grading progress + what needs attention ────────────── */}
      <section className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.65fr) minmax(0, 1fr)' }}>
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Grading progress by teacher</h2>
              <div className="card-subtitle">
                Furthest behind first
              </div>
            </div>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/teachers')}>
              All teachers
            </button>
          </div>
          <div className="card-body flush">
            {data.teachers.length === 0 ? (
              <EmptyState
                icon={Users}
                title="No teachers yet"
                description="Approved teachers appear here."
              />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Teacher</th>
                      <th className="right">Classes</th>
                      <th>{termName} graded</th>
                      <th>Last sign-in</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.teachers.map((teacher) => (
                      <tr key={teacher.id}>
                        <td className="cell-strong">{teacher.name}</td>
                        <td className="right">{teacher.classes}</td>
                        <td style={{ minWidth: 170 }}>
                          {teacher.progress == null ? (
                            <span className="faint">No classes yet</span>
                          ) : (
                            <div
                              className="progress-row"
                              title={`${teacher.graded} of ${teacher.learners} learners graded`}
                            >
                              <div className="progress">
                                <div
                                  className={`progress-fill is-${progressTone(teacher.progress)}`}
                                  style={{ width: `${teacher.progress}%` }}
                                />
                              </div>
                              <span className="progress-value">{teacher.progress.toFixed(0)}%</span>
                            </div>
                          )}
                        </td>
                        <td className={teacher.quiet ? '' : 'muted'}>
                          {teacher.last_login ? (
                            <span style={teacher.quiet ? { color: 'var(--warn-500)' } : undefined}>
                              {teacher.last_login}
                            </span>
                          ) : (
                            <span style={{ color: 'var(--warn-500)' }}>Never</span>
                          )}
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
              <h2>Needs attention</h2>
              <div className="card-subtitle">Teachers and set-up to follow up</div>
            </div>
          </div>
          <div className="card-body">
            {attentionCount === 0 ? (
              <EmptyState icon={CheckCircle2} title="Nothing to follow up" />
            ) : (
              <div style={{ display: 'grid', gap: '1rem' }}>
                {attention.quiet_teachers.length > 0 && (
                  <div>
                    <div className="row" style={{ gap: '.4rem', fontWeight: 600 }}>
                      <AlertTriangle size={14} style={{ color: 'var(--warn-500)' }} />
                      Inactive 2+ weeks
                    </div>
                    <div className="muted" style={{ marginTop: '.25rem' }}>
                      {attention.quiet_teachers.join(', ')}
                    </div>
                  </div>
                )}
                {attention.classes_not_started_total > 0 && (
                  <div>
                    <div className="row" style={{ gap: '.4rem', fontWeight: 600 }}>
                      <ClipboardCheck size={14} style={{ color: 'var(--warn-500)' }} />
                      No {termName} grades yet ({attention.classes_not_started_total})
                    </div>
                    <ul className="muted" style={{ margin: '.25rem 0 0', paddingLeft: '1.1rem' }}>
                      {attention.classes_not_started.map((item) => (
                        <li key={`${item.section}-${item.subject}`}>
                          {item.subject}, {item.section} · {item.teacher}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {attention.sections_without_adviser.length > 0 && (
                  <div>
                    <div className="row" style={{ gap: '.4rem', fontWeight: 600 }}>
                      <BookOpen size={14} style={{ color: 'var(--warn-500)' }} />
                      Sections without an adviser
                    </div>
                    <div className="muted" style={{ marginTop: '.25rem' }}>
                      {attention.sections_without_adviser.join(', ')}
                    </div>
                    <button
                      type="button"
                      className="link-button"
                      style={{ marginTop: '.35rem' }}
                      onClick={() => navigate('/class-management')}
                    >
                      Assign advisers →
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── Recent activity ──────────────────────────────────────────────── */}
      <section className="card">
        <div className="card-header">
          <div>
            <h2>Recent activity</h2>
            <div className="card-subtitle">What your staff did in the system</div>
          </div>
        </div>
        <div className="card-body flush">
          {data.recent_activity.length === 0 ? (
            <EmptyState icon={Activity} title="Nothing recorded yet" />
          ) : (
            <div className="feed">
              {data.recent_activity.map((item, index) => (
                <div className="feed-item" key={`${item.action}-${index}`}>
                  <span className="feed-dot">
                    <UserCheck size={15} />
                  </span>
                  <span className="feed-body">
                    <span className="feed-title">{item.label}</span>
                    <span className="feed-detail">{item.actor}</span>
                  </span>
                  <span className="feed-when">{item.when}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
