import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { GraduationCap, RotateCcw, Save, Search } from 'lucide-react';
import {
  Avatar,
  EmptyState,
  ErrorNote,
  PageHead,
  RiskBadge,
  TableSkeleton,
} from '../../components/common/ui';
import { enrollmentApi, paceApi, sectionApi, subjectApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

/**
 * The teacher's main workflow: pick a section and a subject, type in how far
 * each student has got, save the whole roster in one submit.
 *
 * Everything derived — percentage, units behind, risk band — is recomputed on
 * the server so the numbers on screen always match the early-warning list.
 * Only schools that track units see this page; their grades and the AI
 * forecast live on the class record instead.
 *
 * Every word the teacher reads here is the school's own word for a unit —
 * "PACE" at a School of Tomorrow school, "Module" elsewhere — so the same
 * screen serves both without either one reading as somebody else's system. The
 * variables and API fields keep their original `pace` names; only the labels
 * change.
 */
export default function RosterPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { unitLabel, unitLabelPlural } = useAuth();

  const [sections, setSections] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [enrollments, setEnrollments] = useState([]);
  const [paces, setPaces] = useState([]);

  const [sectionId, setSectionId] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [search, setSearch] = useState('');

  const [drafts, setDrafts] = useState({});
  const [loading, setLoading] = useState(true);
  const [loadingRoster, setLoadingRoster] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const [sectionRows, subjectRows] = await Promise.all([
          sectionApi.list(),
          subjectApi.list({ is_active: true }),
        ]);
        setSections(sectionRows || []);
        setSubjects(subjectRows || []);
        if (sectionRows?.length) setSectionId(String(sectionRows[0].section_id));
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const section = useMemo(
    () => sections.find((item) => String(item.section_id) === sectionId),
    [sections, sectionId],
  );

  // Only subjects that belong to this section's grade level make sense here.
  const subjectOptions = useMemo(
    () =>
      section ? subjects.filter((item) => item.grade_level_id === section.grade_level_id) : [],
    [subjects, section],
  );

  useEffect(() => {
    if (!subjectOptions.length) {
      setSubjectId('');
      return;
    }
    const stillValid = subjectOptions.some((item) => String(item.subject_id) === subjectId);
    if (!stillValid) setSubjectId(String(subjectOptions[0].subject_id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectOptions]);

  const loadRoster = useCallback(async () => {
    if (!sectionId) return;
    setLoadingRoster(true);
    setDrafts({});
    try {
      const [enrollmentRows, paceRows] = await Promise.all([
        enrollmentApi.list({ section_id: sectionId, is_active: true }),
        paceApi.list({ section_id: sectionId }),
      ]);
      setEnrollments(enrollmentRows || []);
      setPaces(paceRows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingRoster(false);
    }
  }, [sectionId]);

  useEffect(() => {
    loadRoster();
  }, [loadRoster]);

  const subject = useMemo(
    () => subjectOptions.find((item) => String(item.subject_id) === subjectId),
    [subjectOptions, subjectId],
  );

  /**
   * How many units a student should have finished by today, assuming an
   * even spread across the 36 teaching weeks of the school year.
   */
  const defaultExpected = useMemo(() => {
    if (!subject) return 0;
    const start = new Date(new Date().getFullYear(), 5, 1); // early June
    const weeks = Math.max(1, Math.min(36, Math.round((Date.now() - start) / 6048e5)));
    return Math.max(1, Math.round((subject.total_paces * weeks) / 36));
  }, [subject]);

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return enrollments
      .filter((enrollment) =>
        term ? (enrollment.student_name || '').toLowerCase().includes(term) : true,
      )
      .map((enrollment) => {
        const existing = paces.find(
          (pace) =>
            pace.enrollment_id === enrollment.enrollment_id &&
            pace.subject === subject?.subject_name,
        );
        const draft = drafts[enrollment.enrollment_id] || {};

        return {
          enrollment,
          existing,
          current:
            draft.current ?? (existing ? String(existing.current_pace) : ''),
          expected:
            draft.expected ??
            String(existing ? existing.expected_pace : defaultExpected),
          dirty: Object.keys(draft).length > 0,
        };
      })
      .sort((a, b) => (a.enrollment.student_name || '').localeCompare(b.enrollment.student_name || ''));
  }, [enrollments, paces, drafts, subject, search, defaultExpected]);

  const dirtyCount = rows.filter((row) => row.dirty).length;

  const setDraft = (enrollmentId, field, value) =>
    setDrafts((current) => ({
      ...current,
      [enrollmentId]: { ...current[enrollmentId], [field]: value },
    }));

  const save = async () => {
    if (!subject) return;
    const dirty = rows.filter((row) => row.dirty);

    // The current unit is the whole point of the entry, so a row without one
    // is not saved.
    const missingPace = dirty.filter((row) => row.current === '');

    const entries = dirty
      .filter((row) => row.current !== '')
      .map((row) => ({
        student_id: row.enrollment.student_id,
        enrollment_id: row.enrollment.enrollment_id,
        subject: subject.subject_name,
        current_pace: Number(row.current) || 0,
        expected_pace: Number(row.expected) || 0,
        total_paces: subject.total_paces,
        attendance: row.existing?.attendance ?? 100,
        average_score: row.existing?.average_score ?? 0,
      }));

    if (!entries.length) {
      toast.info('Nothing to save yet.');
      return;
    }

    setSaving(true);
    try {
      const result = await paceApi.bulkSave(entries);
      toast.success(
        `Saved ${result.saved} record${result.saved === 1 ? '' : 's'}${
          result.skipped?.length ? `, ${result.skipped.length} skipped` : ''
        }.`,
      );
      result.skipped?.forEach((message) => toast.warning(message));


      missingPace.forEach((row) =>
        toast.warning(
          `${row.enrollment.student_name}: enter a current ${unitLabel} number.`,
        ),
      );

      setDrafts({});
      loadRoster();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="page">
        <div className="skeleton" style={{ height: 60, borderRadius: 'var(--radius-lg)' }} />
        <div className="skeleton" style={{ height: 420, borderRadius: 'var(--radius-lg)' }} />
      </div>
    );
  }

  return (
    <div className="page">
      <PageHead
        title={`${unitLabel} progress`}
        subtitle={`Each student's current ${unitLabel}.`}
        actions={
          dirtyCount > 0 && (
            <button type="button" className="btn btn-secondary" onClick={() => setDrafts({})}>
              <RotateCcw size={16} /> Discard changes
            </button>
          )
        }
      />

      <ErrorNote message={error} />

      <div className="toolbar">
        <select
          className="select"
          value={sectionId}
          onChange={(event) => setSectionId(event.target.value)}
          aria-label="Section"
        >
          {sections.map((item) => (
            <option key={item.section_id} value={item.section_id}>
              {item.name}
            </option>
          ))}
        </select>

        <select
          className="select"
          value={subjectId}
          onChange={(event) => setSubjectId(event.target.value)}
          aria-label="Subject"
          disabled={!subjectOptions.length}
        >
          {subjectOptions.length === 0 && <option>No subjects for this grade level</option>}
          {subjectOptions.map((item) => (
            <option key={item.subject_id} value={item.subject_id}>
              {item.subject_name}
              {` (${item.total_paces} ${unitLabelPlural})`}
            </option>
          ))}
        </select>

        <div className="search">
          <Search size={15} />
          <input
            className="input"
            type="search"
            placeholder="Find a student…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <span className="spacer" />
        <span className="result-count">
          {rows.length} student{rows.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="card pace-grid">
        <div className="card-header">
          <div>
            <h2>{section?.name || 'Section'}</h2>
            <div className="card-subtitle">
              {!subject
                ? 'Choose a subject to begin'
                : `${subject.subject_name} · ${defaultExpected} of ${subject.total_paces} expected by today`}
            </div>
          </div>
        </div>

        <div className="card-body flush">
          {loadingRoster ? (
            <TableSkeleton rows={8} columns={6} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={GraduationCap}
              title="No students in this section"
              description="Enrol students in this section first."
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Student</th>
                    <th style={{ width: 110 }}>Current {unitLabel}</th>
                    <th style={{ width: 110 }}>Expected</th>
                    <th>Completion</th>
                    <th>Standing</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const total = subject?.total_paces || 1;
                    const current = Number(row.current) || 0;
                    const expected = Number(row.expected) || 0;
                    const percent = Math.min(100, (current / total) * 100);
                    const behind = Math.max(0, expected - current);
                    const level =
                      behind >= 4 ? 'critical' : behind >= 3 ? 'high' : behind >= 1 ? 'moderate' : 'low';

                    return (
                      <tr key={row.enrollment.enrollment_id}>
                        <td>
                          <div className="cell-name">
                            <Avatar name={row.enrollment.student_name} size="sm" />
                            <button
                              type="button"
                              className="cell-strong"
                              style={{
                                background: 'none',
                                border: 0,
                                padding: 0,
                                cursor: 'pointer',
                                font: 'inherit',
                                fontWeight: 600,
                                color: 'var(--text-strong)',
                              }}
                              onClick={() => navigate(`/students/${row.enrollment.student_id}`)}
                            >
                              {row.enrollment.student_name}
                            </button>
                          </div>
                        </td>
                            <td>
                              <input
                                className={`pace-input ${row.dirty ? 'dirty' : ''}`}
                                type="number"
                                min="0"
                                max={total}
                                value={row.current}
                                placeholder="—"
                                onChange={(event) =>
                                  setDraft(row.enrollment.enrollment_id, 'current', event.target.value)
                                }
                              />
                            </td>
                            <td>
                              <input
                                className={`pace-input ${row.dirty ? 'dirty' : ''}`}
                                type="number"
                                min="0"
                                max={total}
                                value={row.expected}
                                onChange={(event) =>
                                  setDraft(row.enrollment.enrollment_id, 'expected', event.target.value)
                                }
                              />
                            </td>
                            <td style={{ minWidth: 140 }}>
                              <div className="progress-row">
                                <div className="progress">
                                  <div
                                    className={`progress-fill is-${level}`}
                                    style={{ width: `${percent}%` }}
                                  />
                                </div>
                                <span className="progress-value">{percent.toFixed(0)}%</span>
                              </div>
                            </td>
                            <td>
                              {row.current === '' ? (
                                <span className="badge badge-neutral badge-plain">Not encoded</span>
                              ) : (
                                <RiskBadge level={level} />
                              )}
                            </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {rows.length > 0 && (
          <div className="save-bar">
            <span className="dirty-count">
              {dirtyCount > 0 ? (
                <>
                  <b>{dirtyCount}</b> unsaved change{dirtyCount === 1 ? '' : 's'}
                </>
              ) : (
                'All changes saved'
              )}
            </span>
            <span className="spacer" />
            <button
              type="button"
              className="btn btn-primary"
              onClick={save}
              disabled={saving || dirtyCount === 0}
            >
              {saving ? (
                <>
                  <span className="spinner" /> Saving…
                </>
              ) : (
                <>
                  <Save size={16} /> Save progress
                </>
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
