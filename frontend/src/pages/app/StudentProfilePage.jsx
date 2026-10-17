import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CalendarDays,
  GraduationCap,
  Lightbulb,
  Mail,
  MapPin,
  Phone,
  Printer,
  TrendingDown,
  TrendingUp,
  Users,
} from 'lucide-react';
import { Avatar, EmptyState, ErrorNote, ProgressBar, RiskBadge } from '../../components/common/ui';
import { AiReason, AiRiskBadge } from '../../components/common/ai';
import { gradingPeriods, periodName } from '../../lib/grading';
import { BASIS_LABEL } from '../../lib/standing';
import { gradeLevelApi, sectionApi, studentApi } from '../../api/endpoints';
import StudentFormModal from '../../components/common/StudentFormModal';
import { useAuth } from '../../context/AuthContext';
import { IMAGES } from '../../config/images';

function TrendIcon({ trend }) {
  if (trend === 'improving') return <TrendingUp size={14} style={{ color: 'var(--ok-500)' }} />;
  if (trend === 'declining') return <TrendingDown size={14} style={{ color: 'var(--crit-500)' }} />;
  return null;
}

export default function StudentProfilePage() {
  const { studentId } = useParams();
  const navigate = useNavigate();
  const { tracksUnits, unitLabel, unitLabelPlural, gradingPeriodCount, periodLabel } = useAuth();

  /* A school that does not track lesson units has no unit progress to show, so
     the tab is not there at all — the student still has subjects and grades,
     which live on the overview and in the enrolment history. The tab list is
     built per school rather than filtered on render, so "Overview" stays the
     landing tab either way. */
  const tabs = [
    { key: 'overview', label: 'Overview' },
    /* Each kind of school keeps its own record: booklet progress at a school
       that tracks units, grades by period everywhere else. */
    tracksUnits
      ? { key: 'pace', label: `${unitLabel} progress` }
      : { key: 'grades', label: 'Grades' },
    { key: 'history', label: 'Enrolment history' },
  ];

  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('overview');
  const [banner, setBanner] = useState(null);
  // The edit form, with the lists it needs; null while closed.
  const [editing, setEditing] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const image = new Image();
    image.src = IMAGES.studentProfileBanner;
    image.onload = () => setBanner(IMAGES.studentProfileBanner);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const result = await studentApi.profile(studentId);
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
  }, [studentId, reloadKey]);

  const openEdit = async () => {
    try {
      const [gradeLevels, sections] = await Promise.all([gradeLevelApi.list(), sectionApi.list()]);
      setEditing({ gradeLevels: gradeLevels || [], sections: sections || [] });
    } catch (err) {
      setError(err.message);
    }
  };

  if (loading) {
    return (
      <div className="page">
        <div className="skeleton" style={{ height: 140, borderRadius: 'var(--radius-lg)' }} />
        <div className="skeleton" style={{ height: 320, borderRadius: 'var(--radius-lg)' }} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="page">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/students')}>
          <ArrowLeft size={15} /> Back to students
        </button>
        <ErrorNote message={error} />
      </div>
    );
  }

  const { student, subjects, enrollment_history: history } = data;

  /* The Grades tab: one card per subject, with each period's grade. The
     record list holds one row per subject per period; the latest graded row
     gives the current standing, and the AI forecast is the same on every row
     of a subject. */
  const periods = gradingPeriods(gradingPeriodCount);
  const bySubject = new Map();
  subjects.forEach((row) => {
    const entry = bySubject.get(row.subject) || { subject: row.subject, grades: {}, rows: [] };
    entry.rows.push(row);
    if (row.term_grade != null) entry.grades[row.grading_period || 1] = row.term_grade;
    bySubject.set(row.subject, entry);
  });
  const gradeSubjects = [...bySubject.values()].map((entry) => {
    const ordered = [...entry.rows].sort((a, b) => (b.grading_period || 1) - (a.grading_period || 1));
    const current = ordered.find((row) => row.standing != null) || ordered[0];
    return {
      ...entry,
      standing: current.standing,
      basis: current.basis,
      risk_level: current.risk_level,
      intervention: current.intervention,
      ai: entry.rows.find((row) => row.ai)?.ai || null,
    };
  });
  const guardianName = [student.guardian_first_name, student.guardian_last_name]
    .filter(Boolean)
    .join(' ');

  return (
    <div className="page">
      <div className="row no-print">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate('/students')}>
          <ArrowLeft size={15} /> Back to students
        </button>
        <span className="spacer" />
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => window.print()}>
          <Printer size={15} /> Print profile
        </button>
      </div>

      {student.profile_missing?.length > 0 && (
        <div className="alert alert-warning">
          <AlertTriangle size={17} />
          <span style={{ flex: 1 }}>
            Profile incomplete: no {student.profile_missing.join(', ')} yet.
          </span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={openEdit}>
            Fill in details
          </button>
        </div>
      )}

      {/* ── Banner ───────────────────────────────────────────────────────── */}
      <section className="profile-banner">
        {banner && <div className="banner-photo" style={{ backgroundImage: `url(${banner})` }} aria-hidden="true" />}
        <div className="banner-inner">
          <Avatar name={student.full_name} src={student.photo} />
          <div style={{ flex: 1, minWidth: 220 }}>
            <h1>{student.full_name}</h1>
            <div className="banner-meta">
              <span>
                <GraduationCap size={13} /> {student.login_id || '—'}
              </span>
              <span>
                <Users size={13} /> {student.section_name || 'Not enrolled'}
              </span>
              <span>
                <BookOpen size={13} /> {student.grade_level_name || '—'}
              </span>
              {student.adviser_name && (
                <span>
                  <CalendarDays size={13} /> Adviser: {student.adviser_name}
                </span>
              )}
            </div>
          </div>
          <div
            style={{
              display: 'flex',
              gap: '1.75rem',
              flexWrap: 'wrap',
              paddingLeft: '1rem',
            }}
          >
            {/* A school that tracks units reads a learner on completion; every
                other school reads them on grades, where completion is always 0. */}
            {tracksUnits ? (
              <>
                <div>
                  <div style={{ fontSize: 'var(--text-3xl)', fontWeight: 700, lineHeight: 1 }}>
                    {student.pace_percent.toFixed(0)}%
                  </div>
                  <div style={{ fontSize: 'var(--text-xs)', opacity: 0.75, marginTop: 4 }}>
                    Average completion
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 'var(--text-3xl)', fontWeight: 700, lineHeight: 1 }}>
                    {student.paces_behind}
                  </div>
                  <div style={{ fontSize: 'var(--text-xs)', opacity: 0.75, marginTop: 4 }}>
                    {unitLabelPlural} behind (worst subject)
                  </div>
                </div>
              </>
            ) : (
              <>
                <div>
                  <div style={{ fontSize: 'var(--text-3xl)', fontWeight: 700, lineHeight: 1 }}>
                    {student.average_standing == null ? '—' : student.average_standing.toFixed(0)}
                  </div>
                  <div style={{ fontSize: 'var(--text-xs)', opacity: 0.75, marginTop: 4 }}>
                    Average grade
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 'var(--text-3xl)', fontWeight: 700, lineHeight: 1 }}>
                    {student.subjects_flagged ?? 0}
                    <span style={{ fontSize: 'var(--text-lg)', opacity: 0.75 }}>
                      {' '}
                      of {student.subjects_tracked ?? 0}
                    </span>
                  </div>
                  <div style={{ fontSize: 'var(--text-xs)', opacity: 0.75, marginTop: 4 }}>
                    Subjects flagged
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </section>

      <div className="tabs no-print">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`tab ${tab === item.key ? 'is-active' : ''}`}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {/* ── Overview ─────────────────────────────────────────────────────── */}
      {tab === 'overview' && (
        <section className="grid grid-2">
          <div className="card">
            <div className="card-header">
              <h2>Student details</h2>
              <RiskBadge level={student.risk_level} />
            </div>
            <div className="card-body">
              <div className="detail-list">
                <div className="detail-row">
                  <span className="k">Student ID</span>
                  <span className="v mono">{student.login_id || '—'}</span>
                </div>
                <div className="detail-row">
                  <span className="k">Full name</span>
                  <span className="v">{student.full_name}</span>
                </div>
                <div className="detail-row">
                  <span className="k">Date of birth</span>
                  <span className="v">{student.birth_date || '—'}</span>
                </div>
                <div className="detail-row">
                  <span className="k">Gender</span>
                  <span className="v">{student.gender}</span>
                </div>
                <div className="detail-row">
                  <span className="k">
                    <MapPin size={12} /> Address
                  </span>
                  <span className="v">{student.address || '—'}</span>
                </div>
                <div className="detail-row">
                  <span className="k">Subjects tracked</span>
                  <span className="v">{student.subjects_tracked}</span>
                </div>
                <div className="detail-row">
                  <span className="k">Average grade</span>
                  <span className="v">
                    {student.average_standing === null ||
                    student.average_standing === undefined
                      ? '—'
                      : student.average_standing.toFixed(1)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Parent or guardian</h2>
            </div>
            <div className="card-body">
              <div className="detail-list">
                <div className="detail-row">
                  <span className="k">Name</span>
                  <span className="v">{guardianName || '—'}</span>
                </div>
                <div className="detail-row">
                  <span className="k">Relationship</span>
                  <span className="v">{student.guardian_relationship}</span>
                </div>
                <div className="detail-row">
                  <span className="k">
                    <Phone size={12} /> Contact
                  </span>
                  <span className="v">{student.guardian_contact || '—'}</span>
                </div>
                <div className="detail-row">
                  <span className="k">
                    <Mail size={12} /> Email
                  </span>
                  <span className="v">{student.guardian_email || '—'}</span>
                </div>
              </div>

              {student.risk_level !== 'low' && (
                <div className="intervention" style={{ marginTop: '1rem' }}>
                  <Lightbulb size={15} />
                  <span>
                    Flagged <strong>{student.risk_level}</strong>. Contact the guardian early.
                  </span>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── Unit progress ────────────────────────────────────────────────── */}
      {tab === 'pace' && tracksUnits && (
        <section>
          {subjects.length === 0 ? (
            <div className="card">
              <EmptyState
                icon={BookOpen}
                title={`No ${unitLabel} records yet`}
                description={`No ${unitLabel} progress recorded yet.`}
              />
            </div>
          ) : (
            <>
              <div className="grid grid-3">
              {subjects.map((subject) => (
                <article className="subject-card" key={subject.pace_id}>
                  <div className="sc-head">
                    <div>
                      <div className="sc-name">{subject.subject}</div>
                      <div className="sc-sub">
                        {unitLabel} {subject.current_pace} of {subject.total_paces}
                        {subject.paces_behind > 0 && ` · ${subject.paces_behind} behind`}
                      </div>
                    </div>
                    <RiskBadge level={subject.risk_level} />
                  </div>

                  <ProgressBar value={subject.pace_percent} level={subject.risk_level} />

                  <div className="sc-figures">
                    <div>
                      <span className="k">Expected</span>
                      <span className="v">{subject.expected_pace}</span>
                    </div>
                    <div>
                      <span className="k">Completion</span>
                      <span className="v">{subject.pace_percent.toFixed(0)}%</span>
                    </div>
                  </div>

                  {subject.risk_level !== 'low' && (
                    <div className="intervention">
                      <Lightbulb size={14} />
                      <span>{subject.intervention}</span>
                    </div>
                  )}

                </article>
              ))}
              </div>
            </>
          )}
        </section>
      )}

      {/* ── Grades by period ─────────────────────────────────────────────── */}
      {tab === 'grades' && !tracksUnits && (
        <section>
          {gradeSubjects.length === 0 ? (
            <div className="card">
              <EmptyState icon={BookOpen} title="No grades recorded yet" />
            </div>
          ) : (
            <div className="grid grid-3">
              {gradeSubjects.map((subject) => (
                <article className="subject-card" key={subject.subject}>
                  <div className="sc-head">
                    <div>
                      <div className="sc-name">{subject.subject}</div>
                      <div className="sc-sub">
                        {subject.standing == null
                          ? 'No grade yet'
                          : `Now ${subject.standing.toFixed(0)} · ${
                              BASIS_LABEL[subject.basis] || 'grade'
                            }`}
                      </div>
                    </div>
                    <RiskBadge level={subject.risk_level} />
                  </div>

                  <div className="sc-figures">
                    {periods.map((period) => (
                      <div key={period}>
                        <span className="k">{periodName(period, periodLabel)}</span>
                        <span className="v">
                          {subject.grades[period] == null ? '—' : subject.grades[period]}
                        </span>
                      </div>
                    ))}
                  </div>

                  {subject.risk_level !== 'low' && subject.intervention && (
                    <div className="intervention">
                      <Lightbulb size={14} />
                      <span>{subject.intervention}</span>
                    </div>
                  )}

                  <div className="sc-ai">
                    <span className="k">AI forecast</span>
                    <AiRiskBadge prediction={subject.ai} />
                  </div>
                  <AiReason prediction={subject.ai} />
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ── History ──────────────────────────────────────────────────────── */}
      {tab === 'history' && (
        <section className="card">
          <div className="card-header">
            <h2>Enrolment history</h2>
          </div>
          <div className="card-body flush">
            {history.length === 0 ? (
              <EmptyState icon={CalendarDays} title="No enrolment records" />
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>School year</th>
                      <th>Grade level</th>
                      <th>Section</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((row) => (
                      <tr key={row.enrollment_id}>
                        <td className="cell-strong">{row.school_year || '—'}</td>
                        <td>{row.grade_level_name || '—'}</td>
                        <td>{row.section_name || '—'}</td>
                        <td>
                          <span className={`badge ${row.is_active ? 'badge-low' : 'badge-neutral'}`}>
                            {row.is_active ? 'Active' : 'Closed'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}
      {editing && (
        <StudentFormModal
          student={student}
          gradeLevels={editing.gradeLevels}
          sections={editing.sections}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            setReloadKey((k) => k + 1);
          }}
        />
      )}
    </div>
  );
}
