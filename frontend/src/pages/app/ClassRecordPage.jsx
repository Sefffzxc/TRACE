import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Save, Search, Settings2, X } from 'lucide-react';
import {
  EmptyState,
  ErrorNote,
  PageHead,
  TableSkeleton,
} from '../../components/common/ui';
import { AiRiskBadge, AiServiceNote } from '../../components/common/ai';
import {
  aiApi,
  classRecordApi,
  enrollmentApi,
  paceApi,
  sectionApi,
  subjectApi,
} from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';
import { computeRow, show } from '../../lib/grading';

/**
 * The DepEd E-Class Record.
 *
 * Laid out as the printed form is, because teachers already keep this sheet
 * and a screen that rearranges it makes them translate between two layouts
 * every time they encode: three component groups across the top, the item
 * columns beneath them, a HIGHEST POSSIBLE SCORE row, then one row per
 * learner ending in the initial, transmuted and letter grades.
 *
 * The totals, percentages and grades are computed here as the teacher types
 * so the sheet behaves like the spreadsheet it replaces. They are computed
 * again on the server when the record is saved, and the server's answer is
 * the one stored and the one the risk model reads — this copy is for the
 * typing, not for the record.
 *
 * A blank cell means "not yet", never zero. That distinction is the whole
 * reason 65 rows of the school's own export read as failing learners when
 * they were really empty columns, so it is preserved from this grid through
 * to the stored grade.
 */

const PERIODS = [1, 2, 3, 4];

/** The default DepEd split, used until a teacher sets the record's own. */
const DEFAULT_SETUP = {
  ww_highest: [null, null, null, null, null],
  pt_highest: [null, null, null],
  sa_highest: [null, null],
  te_highest: null,
  ww_weight: 20,
  pt_weight: 50,
  te_weight: 30,
};

/** '' and '-' are a cleared cell; anything else becomes a number or stays blank. */
const toScore = (raw) => {
  if (raw === '' || raw === null || raw === undefined) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
};

const asText = (value) => (value === null || value === undefined ? '' : String(value));

export default function ClassRecordPage() {
  const toast = useToast();

  const [sections, setSections] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [enrollments, setEnrollments] = useState([]);
  const [paces, setPaces] = useState([]);

  const [sectionId, setSectionId] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [period, setPeriod] = useState(1);
  const [search, setSearch] = useState('');

  const [setup, setSetup] = useState(DEFAULT_SETUP);
  const [setupDraft, setSetupDraft] = useState(null); // non-null while editing
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

  // Wake the model while the teacher is still choosing a section, so the
  // first save is scored by the model rather than by the fallback rule.
  useEffect(() => {
    aiApi.status(true).then(setAiStatus).catch(() => setAiStatus(null));
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
    () => sections.find((s) => String(s.section_id) === sectionId),
    [sections, sectionId],
  );

  const subjectOptions = useMemo(
    () => (section ? subjects.filter((s) => s.grade_level_id === section.grade_level_id) : []),
    [subjects, section],
  );

  useEffect(() => {
    if (!subjectOptions.length) {
      setSubjectId('');
      return;
    }
    if (!subjectOptions.some((s) => String(s.subject_id) === subjectId)) {
      setSubjectId(String(subjectOptions[0].subject_id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectOptions]);

  const subject = useMemo(
    () => subjectOptions.find((s) => String(s.subject_id) === subjectId),
    [subjectOptions, subjectId],
  );

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

  // The sheet's own header. Falls back to the DepEd default split so a fresh
  // record is usable before anyone has set it up.
  useEffect(() => {
    if (!sectionId || !subjectId) return;
    let cancelled = false;
    classRecordApi
      .getSetup({ section_id: sectionId, subject_id: subjectId, grading_period: period })
      .then((data) => {
        if (cancelled || !data) return;
        setSetup({
          ww_highest: data.ww_highest?.length ? data.ww_highest : DEFAULT_SETUP.ww_highest,
          pt_highest: data.pt_highest?.length ? data.pt_highest : DEFAULT_SETUP.pt_highest,
          sa_highest: data.sa_highest?.length ? data.sa_highest : DEFAULT_SETUP.sa_highest,
          te_highest: data.te_highest ?? null,
          ww_weight: data.ww_weight,
          pt_weight: data.pt_weight,
          te_weight: data.te_weight,
        });
      })
      .catch(() => setSetup(DEFAULT_SETUP));
    return () => {
      cancelled = true;
    };
  }, [sectionId, subjectId, period]);

  /** One row per learner: stored scores, overlaid with anything unsaved. */
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return enrollments
      .filter((e) => (term ? (e.student_name || '').toLowerCase().includes(term) : true))
      .map((enrollment) => {
        const stored = paces.find(
          (p) =>
            p.enrollment_id === enrollment.enrollment_id && p.subject === subject?.subject_name,
        );
        const draft = drafts[enrollment.enrollment_id] || {};

        const cells = (key, length) => {
          const base = stored?.[key] || [];
          return Array.from({ length }, (_, i) =>
            draft[key]?.[i] !== undefined ? draft[key][i] : asText(base[i]),
          );
        };

        const ww = cells('ww_scores', setup.ww_highest.length);
        const pt = cells('pt_scores', setup.pt_highest.length);
        const sa = cells('sa_scores', setup.sa_highest.length);
        const te = draft.te_score !== undefined ? draft.te_score : asText(stored?.te_score);

        const computed = computeRow(
          {
            ww_scores: ww.map(toScore),
            pt_scores: pt.map(toScore),
            sa_scores: sa.map(toScore),
            te_score: toScore(te),
          },
          setup,
        );

        return {
          enrollment,
          stored,
          ww,
          pt,
          sa,
          te,
          computed,
          dirty: Object.keys(draft).length > 0,
          ai: stored?.ai_risk_level
            ? {
                risk_score: stored.ai_risk_score,
                risk_level: stored.ai_risk_level,
                source: stored.ai_source,
                used_threshold: stored.ai_used_threshold,
                model_version: stored.ai_model_version,
                used_baseline_defaults: stored.ai_used_baseline_defaults,
                primary_factor: stored.ai_primary_factor,
                imputed_fields: [],
              }
            : null,
        };
      })
      .sort((a, b) =>
        (a.enrollment.student_name || '').localeCompare(b.enrollment.student_name || ''),
      );
  }, [enrollments, paces, drafts, subject, search, setup]);

  const dirtyCount = rows.filter((r) => r.dirty).length;

  const setCell = (enrollmentId, key, index, value) =>
    setDrafts((current) => {
      const row = current[enrollmentId] || {};
      const list = [...(row[key] || [])];
      list[index] = value;
      return { ...current, [enrollmentId]: { ...row, [key]: list } };
    });

  const setExam = (enrollmentId, value) =>
    setDrafts((current) => ({
      ...current,
      [enrollmentId]: { ...current[enrollmentId], te_score: value },
    }));

  const save = async () => {
    if (!subject) return;
    const changed = rows.filter((r) => r.dirty);
    if (!changed.length) {
      toast.info('Nothing to save yet — type a score first.');
      return;
    }

    setSaving(true);
    try {
      const entries = changed.map((row) => ({
        student_id: row.enrollment.student_id,
        enrollment_id: row.enrollment.enrollment_id,
        subject: subject.subject_name,
        // Unit progress is not kept on this screen, so whatever the record
        // already holds is sent back unchanged rather than reset to zero.
        current_pace: row.stored?.current_pace ?? 0,
        expected_pace: row.stored?.expected_pace ?? 0,
        total_paces: row.stored?.total_paces ?? subject.total_paces ?? 12,
        attendance: row.stored?.attendance ?? 100,
        average_score: row.stored?.average_score ?? 0,
        ww_scores: row.ww.map(toScore),
        pt_scores: row.pt.map(toScore),
        sa_scores: row.sa.map(toScore),
        te_score: toScore(row.te),
      }));

      const result = await paceApi.bulkSave(entries, {
        section_id: Number(sectionId),
        subject_id: Number(subjectId),
        grading_period: period,
      });

      toast.success(`Saved ${result.saved} record${result.saved === 1 ? '' : 's'}.`);
      if (result.ai?.fallback) {
        toast.info(
          `${result.ai.fallback} of ${result.saved} were scored by the rule, not the model.`,
        );
      }
      (result.skipped || []).forEach((reason) => toast.error(reason));
      setDrafts({});
      await loadRoster();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const saveSetup = async () => {
    const payload = {
      ww_highest: setupDraft.ww_highest.map(toScore),
      pt_highest: setupDraft.pt_highest.map(toScore),
      sa_highest: setupDraft.sa_highest.map(toScore),
      te_highest: toScore(setupDraft.te_highest),
      ww_weight: Number(setupDraft.ww_weight),
      pt_weight: Number(setupDraft.pt_weight),
      te_weight: Number(setupDraft.te_weight),
    };
    const total = payload.ww_weight + payload.pt_weight + payload.te_weight;
    if (Math.abs(total - 100) > 0.01) {
      toast.error(`The three weights must total 100 percent — they total ${total}.`);
      return;
    }
    try {
      const saved = await classRecordApi.saveSetup(
        { section_id: Number(sectionId), subject_id: Number(subjectId), grading_period: period },
        payload,
      );
      setSetup({ ...setup, ...saved });
      setSetupDraft(null);
      toast.success('Class record setup saved. Every grade in this record was recalculated.');
      await loadRoster();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const editing = setupDraft !== null;
  const head = editing ? setupDraft : setup;

  const groups = [
    { key: 'ww', label: 'Written / Oral Works', weight: head.ww_weight, count: head.ww_highest.length },
    { key: 'pt', label: 'Product / Performance Tasks', weight: head.pt_weight, count: head.pt_highest.length },
    {
      key: 'te',
      label: 'Summative Tests & Term Examination',
      weight: head.te_weight,
      count: head.sa_highest.length + 1,
    },
  ];

  if (loading) return <TableSkeleton rows={8} />;

  return (
    <div className="page">
      <PageHead
        title="Class record"
        subtitle={
          'The DepEd E-Class Record. Written works and performance tasks are what ' +
          'the risk model reads; the term examination is not, because it is the ' +
          'result the warning is meant to come before.'
        }
      />

      <ErrorNote message={error} />
      <AiServiceNote status={aiStatus} onRetry={wakeAi} busy={waking} />

      <div className="toolbar">
        <select
          className="select"
          value={sectionId}
          onChange={(e) => setSectionId(e.target.value)}
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
          onChange={(e) => setSubjectId(e.target.value)}
          aria-label="Subject"
          disabled={!subjectOptions.length}
        >
          {subjectOptions.length === 0 && <option>No subjects for this grade level</option>}
          {subjectOptions.map((item) => (
            <option key={item.subject_id} value={item.subject_id}>
              {item.subject_name}
            </option>
          ))}
        </select>

        <select
          className="select"
          value={period}
          onChange={(e) => setPeriod(Number(e.target.value))}
          aria-label="Grading period"
        >
          {PERIODS.map((p) => (
            <option key={p} value={p}>
              Quarter {p}
            </option>
          ))}
        </select>

        <div className="search">
          <Search size={15} />
          <input
            className="input"
            type="search"
            placeholder="Find a learner…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {editing ? (
          <div className="cr-setup-actions">
            <button type="button" className="btn btn-primary" onClick={saveSetup}>
              <Check size={15} /> Save setup
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setSetupDraft(null)}>
              <X size={15} /> Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setSetupDraft({ ...setup })}
            disabled={!subjectId}
          >
            <Settings2 size={15} /> Set up record
          </button>
        )}
      </div>

      {editing && (
        <p className="cr-setup-hint">
          Enter the highest possible score for each assessment and the weight of each
          component. Saving recalculates every grade in this record, because a
          percentage means nothing without the total it was taken out of.
        </p>
      )}

      {loadingRoster ? (
        <TableSkeleton rows={8} />
      ) : !rows.length ? (
        <EmptyState
          title="No learners here yet"
          message="Enrol learners in this section and they will appear on the class record."
        />
      ) : (
        <div className="card cr-card">
          <div className="cr-scroll">
            <table className="cr-table">
              <thead>
                <tr>
                  <th className="cr-sticky cr-name" rowSpan={3}>
                    Learners&rsquo; names
                  </th>
                  {groups.map((g) => (
                    <th key={g.key} colSpan={g.count + 3} className="cr-group">
                      {g.label}
                      <span className="cr-weight">
                        {editing ? (
                          <input
                            className="cr-input cr-weight-input"
                            value={head[`${g.key}_weight`]}
                            onChange={(e) =>
                              setSetupDraft({ ...setupDraft, [`${g.key}_weight`]: e.target.value })
                            }
                            aria-label={`${g.label} weight, percent`}
                          />
                        ) : (
                          `${show(g.weight, 0)}%`
                        )}
                      </span>
                    </th>
                  ))}
                  <th className="cr-out" rowSpan={3}>
                    Initial
                    <br />
                    Grade
                  </th>
                  <th className="cr-out" rowSpan={3}>
                    Transmuted
                    <br />
                    Grade
                  </th>
                  <th className="cr-out" rowSpan={3}>
                    Letter
                    <br />
                    Grade
                  </th>
                  <th className="cr-out cr-risk-head" rowSpan={3}>
                    Risk
                  </th>
                </tr>

                <tr>
                  {head.ww_highest.map((_, i) => (
                    <th key={`wwh${i}`} className="cr-item">
                      {i + 1}
                    </th>
                  ))}
                  <th className="cr-derived">Total</th>
                  <th className="cr-derived">PS</th>
                  <th className="cr-derived">WS</th>

                  {head.pt_highest.map((_, i) => (
                    <th key={`pth${i}`} className="cr-item">
                      {i + 1}
                    </th>
                  ))}
                  <th className="cr-derived">Total</th>
                  <th className="cr-derived">PS</th>
                  <th className="cr-derived">WS</th>

                  {head.sa_highest.map((_, i) => (
                    <th key={`sah${i}`} className="cr-item">
                      SA{i + 1}
                    </th>
                  ))}
                  <th className="cr-item">TE</th>
                  <th className="cr-derived">Total</th>
                  <th className="cr-derived">PS</th>
                  <th className="cr-derived">WS</th>
                </tr>

                <tr className="cr-hps">
                  {[
                    ['ww_highest', head.ww_highest, head.ww_weight],
                    ['pt_highest', head.pt_highest, head.pt_weight],
                  ].map(([key, list, weight]) => (
                    <HighestCells
                      key={key}
                      name={key}
                      list={list}
                      weight={weight}
                      editing={editing}
                      onChange={(i, v) => {
                        const next = [...setupDraft[key]];
                        next[i] = v;
                        setSetupDraft({ ...setupDraft, [key]: next });
                      }}
                    />
                  ))}
                  <HighestCells
                    name="sa_highest"
                    list={head.sa_highest}
                    weight={head.te_weight}
                    editing={editing}
                    trailing={{
                      value: head.te_highest,
                      onChange: (v) => setSetupDraft({ ...setupDraft, te_highest: v }),
                    }}
                    onChange={(i, v) => {
                      const next = [...setupDraft.sa_highest];
                      next[i] = v;
                      setSetupDraft({ ...setupDraft, sa_highest: next });
                    }}
                  />
                </tr>
              </thead>

              <tbody>
                {rows.map((row, index) => (
                  <tr key={row.enrollment.enrollment_id} className={row.dirty ? 'cr-dirty' : ''}>
                    <th className="cr-sticky cr-name" scope="row">
                      <span className="cr-no">{index + 1}</span>
                      {row.enrollment.student_name}
                    </th>

                    <ScoreCells
                      values={row.ww}
                      onChange={(i, v) => setCell(row.enrollment.enrollment_id, 'ww_scores', i, v)}
                      label={`${row.enrollment.student_name} written work`}
                    />
                    <Derived value={row.computed.ww_total} />
                    <Derived value={row.computed.ww_pct} />
                    <Derived value={row.computed.ww_ws} />

                    <ScoreCells
                      values={row.pt}
                      onChange={(i, v) => setCell(row.enrollment.enrollment_id, 'pt_scores', i, v)}
                      label={`${row.enrollment.student_name} performance task`}
                    />
                    <Derived value={row.computed.pt_total} />
                    <Derived value={row.computed.pt_pct} />
                    <Derived value={row.computed.pt_ws} />

                    <ScoreCells
                      values={row.sa}
                      onChange={(i, v) => setCell(row.enrollment.enrollment_id, 'sa_scores', i, v)}
                      label={`${row.enrollment.student_name} summative test`}
                    />
                    <td className="cr-cell">
                      <input
                        className="cr-input"
                        inputMode="decimal"
                        value={row.te}
                        onChange={(e) => setExam(row.enrollment.enrollment_id, e.target.value)}
                        aria-label={`${row.enrollment.student_name} term examination`}
                      />
                    </td>
                    <Derived value={row.computed.te_total} />
                    <Derived value={row.computed.te_pct} />
                    <Derived value={row.computed.te_ws} />

                    <Derived value={row.computed.initial_grade} strong />
                    <td className="cr-derived cr-strong">
                      {show(row.computed.transmuted_grade, 0)}
                    </td>
                    <td className="cr-derived">
                      {row.computed.letter_grade ? (
                        <span
                          className={`cr-letter cr-letter-${row.computed.letter_grade.toLowerCase()}`}
                          title={row.computed.descriptor}
                        >
                          {row.computed.letter_grade}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="cr-derived">
                      <AiRiskBadge ai={row.ai} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="save-bar">
            <span>
              {dirtyCount ? (
                <>
                  <b>{dirtyCount}</b> learner{dirtyCount === 1 ? '' : 's'} with unsaved changes
                </>
              ) : (
                'All changes saved'
              )}
            </span>
            <button
              type="button"
              className="btn btn-primary"
              onClick={save}
              disabled={saving || !dirtyCount}
            >
              <Save size={15} /> {saving ? 'Saving…' : 'Save class record'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * The HIGHEST POSSIBLE SCORE row for one component group.
 *
 * Its Total / PS / WS cells mirror the printed form: the sum of the highest
 * possible scores, a flat 100.00, and the component's weight — the figures a
 * learner who scored full marks on everything would reach.
 */
function HighestCells({ name, list, weight, editing, onChange, trailing }) {
  const entered = [...list, ...(trailing ? [trailing.value] : [])]
    .map((v) => Number(v))
    .filter((n) => Number.isFinite(n) && n > 0);
  const total = entered.length ? entered.reduce((a, b) => a + b, 0) : null;
  return (
    <>
      {list.map((value, i) => (
        <td key={`${name}${i}`} className="cr-cell">
          {editing ? (
            <input
              className="cr-input"
              inputMode="decimal"
              value={asText(value)}
              onChange={(e) => onChange(i, e.target.value)}
              aria-label={`highest possible score, item ${i + 1}`}
            />
          ) : (
            show(value, 0)
          )}
        </td>
      ))}
      {trailing && (
        <td className="cr-cell">
          {editing ? (
            <input
              className="cr-input"
              inputMode="decimal"
              value={asText(trailing.value)}
              onChange={(e) => trailing.onChange(e.target.value)}
              aria-label="highest possible score, term examination"
            />
          ) : (
            show(trailing.value, 0)
          )}
        </td>
      )}
      <td className="cr-derived cr-hps-label">{show(total, 0)}</td>
      <td className="cr-derived cr-hps-label">100.00</td>
      <td className="cr-derived cr-hps-label">{show(weight, 0)}%</td>
    </>
  );
}

function ScoreCells({ values, onChange, label }) {
  return values.map((value, i) => (
    <td key={i} className="cr-cell">
      <input
        className="cr-input"
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(i, e.target.value)}
        aria-label={`${label} ${i + 1}`}
      />
    </td>
  ));
}

function Derived({ value, strong = false }) {
  return <td className={`cr-derived${strong ? ' cr-strong' : ''}`}>{show(value)}</td>;
}
