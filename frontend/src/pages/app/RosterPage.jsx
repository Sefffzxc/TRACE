import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Brain, GraduationCap, RotateCcw, Save, Search } from 'lucide-react';
import {
  Avatar,
  EmptyState,
  ErrorNote,
  PageHead,
  RiskBadge,
  TableSkeleton,
} from '../../components/common/ui';
import { AiRiskBadge, AiServiceNote } from '../../components/common/ai';
import { aiApi, enrollmentApi, paceApi, sectionApi, subjectApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

/**
 * The columns of the DepEd class record the risk model reads.
 *
 * They live on the same screen as unit progress but behind a separate view, so
 * the everyday encoding task stays as narrow as it was. Every one is optional:
 * a blank cell is saved as "not encoded", the model substitutes its training
 * median, and the prediction is marked as resting on partial data. Blank never
 * means zero.
 */
const ASSESSMENT_COLUMNS = [
  { key: 'absences', label: 'Absences', max: 200, hint: 'Days absent this quarter' },
  { key: 'ww_percent', label: 'WW %', max: 100, hint: 'Written Work percentage score' },
  { key: 'ww_total_raw', label: 'WW raw', max: 1000, hint: 'Written Work raw total' },
  { key: 'pt_percent', label: 'PT %', max: 100, hint: 'Performance Tasks percentage score' },
  { key: 'pt_total_raw', label: 'PT raw', max: 1000, hint: 'Performance Tasks raw total' },
  { key: 'missed_ww_items', label: 'Missed WW', max: 100, hint: 'Written Work items not submitted' },
  { key: 'missed_pt_items', label: 'Missed PT', max: 100, hint: 'Performance Tasks not submitted' },
  { key: 'extracurricular_count', label: 'Activities', max: 50, hint: 'Extracurricular activities joined' },
];

/**
 * Reshape the flat `ai_*` fields on a progress row into the object AiRiskBadge
 * expects. Returns null when the record has never been scored, so an unscored
 * row reads as "not scored" rather than as a zero-risk one.
 */
function aiOf(pace) {
  if (!pace || !pace.ai_risk_level) return null;
  return {
    risk_score: pace.ai_risk_score,
    risk_level: pace.ai_risk_level,
    source: pace.ai_source,
    used_threshold: pace.ai_used_threshold,
    model_version: pace.ai_model_version,
    used_baseline_defaults: pace.ai_used_baseline_defaults,
    primary_factor: pace.ai_primary_factor,
    predicted_final: pace.ai_predicted_final,
    range_low: pace.ai_range_low,
    range_high: pace.ai_range_high,
    imputed_fields: [],
  };
}

/**
 * The teacher's main workflow: pick a section and a subject, type in how far
 * each student has got, save the whole roster in one submit.
 *
 * Everything derived — percentage, units behind, risk band — is recomputed on
 * the server so the numbers on screen always match the early-warning list.
 * Saving also sends the record to the risk classifier and logs what it said;
 * if that service is asleep or down the save still succeeds and the row is
 * scored by the progress rules instead, marked as such.
 *
 * Every word the teacher reads here is the school's own word for a unit —
 * "PACE" at a School of Tomorrow school, "Module" elsewhere — so the same
 * screen serves both without either one reading as somebody else's system. The
 * variables and API fields keep their original `pace` names; only the labels
 * change.
 */
export default function RosterPage({ mode = 'units' }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { unitLabel, unitLabelPlural } = useAuth();

  // Which grid this is showing is decided by the route, not by a control on
  // the page. That matters: the class record has to exist at a school that
  // tracks no units at all, and while it was a tab inside the unit-progress
  // screen it inherited that screen's visibility — so a DepEd school had
  // nowhere to enter attendance or assessment marks, and every prediction it
  // made ran on training medians instead of its own students' data.
  const isRecord = mode === 'record';
  const view = isRecord ? 'assessment' : 'pace';

  const [sections, setSections] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [enrollments, setEnrollments] = useState([]);
  const [paces, setPaces] = useState([]);

  const [sectionId, setSectionId] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [search, setSearch] = useState('');

  const [drafts, setDrafts] = useState({});
  const [aiStatus, setAiStatus] = useState(null);
  const [waking, setWaking] = useState(false);
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

  // Warm the model service up while the teacher is still choosing a section, so
  // the first save is scored by the model rather than falling back.
  useEffect(() => {
    aiApi
      .status(true)
      .then(setAiStatus)
      .catch(() => setAiStatus(null));
  }, []);

  const wakeAi = async () => {
    setWaking(true);
    try {
      setAiStatus(await aiApi.status(true));
    } catch {
      /* the note already says it is unreachable */
    } finally {
      setWaking(false);
    }
  };

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

        // Assessment cells: the draft wins, then whatever is stored, then
        // blank. Stored nulls stay blank rather than becoming 0.
        const assessment = {};
        ASSESSMENT_COLUMNS.forEach(({ key }) => {
          const stored = existing?.[key];
          assessment[key] =
            draft[key] ?? (stored === null || stored === undefined ? '' : String(stored));
        });

        return {
          enrollment,
          existing,
          current:
            draft.current ?? (existing ? String(existing.current_pace) : ''),
          expected:
            draft.expected ??
            String(existing ? existing.expected_pace : defaultExpected),
          attendance:
            draft.attendance ?? String(existing ? existing.attendance : '100'),
          assessment,
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

    // A unit number is required only on the unit-progress screen, where it is
    // the whole point of the entry. On the class record it must NOT be: a
    // school that tracks no units will never have one, and demanding it would
    // silently discard every attendance and assessment mark the teacher typed.
    const missingPace = isRecord ? [] : dirty.filter((row) => row.current === '');

    const entries = dirty
      .filter((row) => isRecord || row.current !== '')
      .map((row) => {
        const entry = {
          student_id: row.enrollment.student_id,
          enrollment_id: row.enrollment.enrollment_id,
          subject: subject.subject_name,
          current_pace: Number(row.current) || 0,
          expected_pace: Number(row.expected) || 0,
          total_paces: subject.total_paces,
          attendance: Number(row.attendance) || 0,
          average_score: row.existing?.average_score ?? 0,
        };
        // A blank cell is omitted entirely, so the server keeps whatever is
        // already stored and the model is told the value is unknown. Sending 0
        // would be inventing a fact about the student.
        ASSESSMENT_COLUMNS.forEach(({ key }) => {
          const raw = row.assessment[key];
          if (raw !== '' && raw !== null && raw !== undefined && !Number.isNaN(Number(raw))) {
            entry[key] = Number(raw);
          }
        });
        return entry;
      });

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

      // Say plainly which engine scored the save.
      const ai = result.ai;
      if (ai?.source === 'fallback') {
        toast.warning(
          'Saved. The AI service was asleep, so the school rule was used.',
        );
      } else if (ai?.source === 'mixed') {
        toast.warning(
          `${ai.scored - ai.fallback} by the AI, ${ai.fallback} by the school rule.`,
        );
      } else if (ai?.source === 'model') {
        toast.info('AI forecast updated.');
      }

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
        title={isRecord ? 'Class record' : `${unitLabel} progress`}
        subtitle={
          isRecord
            ? 'Assessment marks per student.'
            : `Each student's current ${unitLabel}.`
        }
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
              {isRecord ? '' : ` (${item.total_paces} ${unitLabelPlural})`}
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

      {view === 'assessment' && <AiServiceNote status={aiStatus} onRetry={wakeAi} busy={waking} />}

      <div className="card pace-grid">
        <div className="card-header">
          <div>
            <h2>{section?.name || 'Section'}</h2>
            <div className="card-subtitle">
              {!subject
                ? 'Choose a subject to begin'
                : isRecord
                  ? subject.subject_name
                  : `${subject.subject_name} · ${defaultExpected} of ${subject.total_paces} expected by today`}
            </div>
            {view === 'assessment' && (
              <div className="card-subtitle" style={{ marginTop: '.35rem' }}>
                <Brain size={13} style={{ verticalAlign: '-2px', marginRight: '.3rem' }} />
                Leave unmarked cells blank, not zero.
              </div>
            )}
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
                    {view === 'pace' ? (
                      <>
                        <th style={{ width: 110 }}>Current {unitLabel}</th>
                        <th style={{ width: 110 }}>Expected</th>
                        <th style={{ width: 110 }}>Attendance</th>
                        <th>Completion</th>
                        <th>Standing</th>
                      </>
                    ) : (
                      <>
                        {ASSESSMENT_COLUMNS.map((column) => (
                          <th key={column.key} style={{ width: 96 }} title={column.hint}>
                            {column.label}
                          </th>
                        ))}
                        <th>AI forecast</th>
                      </>
                    )}
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
                        {view === 'pace' ? (
                          <>
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
                            <td>
                              <input
                                className={`pace-input ${row.dirty ? 'dirty' : ''}`}
                                type="number"
                                min="0"
                                max="100"
                                value={row.attendance}
                                onChange={(event) =>
                                  setDraft(row.enrollment.enrollment_id, 'attendance', event.target.value)
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
                          </>
                        ) : (
                          <>
                            {ASSESSMENT_COLUMNS.map((column) => (
                              <td key={column.key}>
                                <input
                                  className={`pace-input ${row.dirty ? 'dirty' : ''}`}
                                  type="number"
                                  min="0"
                                  max={column.max}
                                  step="any"
                                  placeholder="—"
                                  aria-label={`${column.hint} for ${row.enrollment.student_name}`}
                                  value={row.assessment[column.key]}
                                  onChange={(event) =>
                                    setDraft(
                                      row.enrollment.enrollment_id,
                                      column.key,
                                      event.target.value,
                                    )
                                  }
                                />
                              </td>
                            ))}
                            <td>
                              <AiRiskBadge prediction={aiOf(row.existing)} compact />
                            </td>
                          </>
                        )}
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
