import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Save, Search, Settings2, X } from 'lucide-react';
import {
  EmptyState,
  ErrorNote,
  PageHead,
  TableSkeleton,
} from '../../components/common/ui';
import { AiServiceNote } from '../../components/common/ai';
import {
  aiApi,
  classRecordApi,
  enrollmentApi,
  paceApi,
  sectionApi,
  subjectApi,
} from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';
import {
  CORE,
  EXAM_LABELS,
  TEMPLATES,
  TERMS,
  TERM_LABELS,
  computeRow,
  show,
} from '../../lib/grading';

/**
 * The MATATAG three-term E-Class Record.
 *
 * Laid out as the workbook is, because teachers already keep that sheet and
 * a screen that rearranges it makes them translate between two layouts every
 * time they encode: three component groups across the top, the item columns
 * beneath them, a HIGHEST POSSIBLE SCORE row, then one row per learner
 * ending in the initial grade and term grade.
 *
 * Two records, not one. Science, Mathematics, English, Filipino and Araling
 * Panlipunan weight the components 20/50/30; Music and Arts and Physical
 * Education and Health weight them 20/60/20. Which one a subject uses is
 * part of that record's own setup, so a school runs both at once — which
 * every school does.
 *
 * The examinations group is the part that is easy to get wrong. It is not a
 * total: the two summative tests and the term examination each carry their
 * own share of the component — 30, 30 and 40 — so each is converted on its
 * own scale and the three weighted figures are added to make the
 * component's percentage.
 *
 * Everything derived is computed here as the teacher types and again on the
 * server when the record is saved. The server's answer is the one stored and
 * the one the risk model reads; this copy is for the typing.
 *
 * A blank cell means "not yet", never zero. That distinction is why 65 rows
 * of the school's own export read as failing learners when they were empty
 * columns, and it is kept from this grid through to the stored grade: a
 * record missing a whole component shows no grade rather than a wrong one.
 */

const DEFAULT_SETUP = {
  template: CORE,
  ww_highest: [null, null, null, null, null],
  pt_highest: [null, null, null],
  exam_highest: [null, null, null],
  ww_weight: TEMPLATES[CORE].ww_weight,
  pt_weight: TEMPLATES[CORE].pt_weight,
  ex_weight: TEMPLATES[CORE].ex_weight,
  exam_subweights: [30, 30, 40],
};

/** '' is a cleared cell; anything else becomes a number or stays blank. */
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
  const [term, setTerm] = useState(1);
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

  // The sheet's own header. Falls back to the core template so a fresh
  // record is usable before anyone has set it up.
  useEffect(() => {
    if (!sectionId || !subjectId) return undefined;
    let cancelled = false;
    classRecordApi
      .getSetup({ section_id: sectionId, subject_id: subjectId, grading_period: term })
      .then((data) => {
        if (cancelled || !data) return;
        setSetup({
          template: data.template || CORE,
          ww_highest: data.ww_highest?.length ? data.ww_highest : DEFAULT_SETUP.ww_highest,
          pt_highest: data.pt_highest?.length ? data.pt_highest : DEFAULT_SETUP.pt_highest,
          exam_highest: data.exam_highest?.length
            ? data.exam_highest
            : DEFAULT_SETUP.exam_highest,
          ww_weight: data.ww_weight,
          pt_weight: data.pt_weight,
          ex_weight: data.ex_weight,
          exam_subweights: data.exam_subweights?.length
            ? data.exam_subweights
            : DEFAULT_SETUP.exam_subweights,
        });
      })
      .catch(() => setSetup(DEFAULT_SETUP));
    return () => {
      cancelled = true;
    };
  }, [sectionId, subjectId, term]);

  /** One row per learner: stored scores, overlaid with anything unsaved. */
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return enrollments
      .filter((e) => (needle ? (e.student_name || '').toLowerCase().includes(needle) : true))
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
        const exam = cells('exam_scores', setup.exam_highest.length);

        const computed = computeRow(
          {
            ww_scores: ww.map(toScore),
            pt_scores: pt.map(toScore),
            exam_scores: exam.map(toScore),
          },
          setup,
        );

        return {
          enrollment,
          stored,
          ww,
          pt,
          exam,
          computed,
          dirty: Object.keys(draft).length > 0,
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
        // already holds goes back unchanged rather than being reset to zero.
        current_pace: row.stored?.current_pace ?? 0,
        expected_pace: row.stored?.expected_pace ?? 0,
        total_paces: row.stored?.total_paces ?? 0,
        attendance: row.stored?.attendance ?? 100,
        average_score: row.stored?.average_score ?? 0,
        ww_scores: row.ww.map(toScore),
        pt_scores: row.pt.map(toScore),
        exam_scores: row.exam.map(toScore),
      }));

      const result = await paceApi.bulkSave(entries, {
        section_id: Number(sectionId),
        subject_id: Number(subjectId),
        grading_period: term,
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

  /** Switching record type resets the weights to that type's own. */
  const chooseTemplate = (value) => {
    const chosen = TEMPLATES[value] || TEMPLATES[CORE];
    setSetupDraft({
      ...setupDraft,
      template: value,
      ww_weight: chosen.ww_weight,
      pt_weight: chosen.pt_weight,
      ex_weight: chosen.ex_weight,
    });
  };

  const saveSetup = async () => {
    const payload = {
      template: setupDraft.template,
      ww_highest: setupDraft.ww_highest.map(toScore),
      pt_highest: setupDraft.pt_highest.map(toScore),
      exam_highest: setupDraft.exam_highest.map(toScore),
      ww_weight: Number(setupDraft.ww_weight),
      pt_weight: Number(setupDraft.pt_weight),
      ex_weight: Number(setupDraft.ex_weight),
      exam_subweights: setupDraft.exam_subweights.map(Number),
    };
    const total = payload.ww_weight + payload.pt_weight + payload.ex_weight;
    if (Math.abs(total - 100) > 0.01) {
      toast.error(`The three component weights must total 100 — they total ${total}.`);
      return;
    }
    const shares = payload.exam_subweights.reduce((a, b) => a + b, 0);
    if (Math.abs(shares - 100) > 0.01) {
      toast.error(`The examination shares must total 100 — they total ${shares}.`);
      return;
    }
    try {
      const saved = await classRecordApi.saveSetup(
        { section_id: Number(sectionId), subject_id: Number(subjectId), grading_period: term },
        payload,
      );
      setSetup({ ...setup, ...saved });
      setSetupDraft(null);
      toast.success('Setup saved. Every grade in this record was recalculated.');
      await loadRoster();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const editing = setupDraft !== null;
  const head = editing ? setupDraft : setup;
  const profile = TEMPLATES[head.template] || TEMPLATES[CORE];

  const patchDraft = (key, index, value) => {
    const next = [...setupDraft[key]];
    next[index] = value;
    setSetupDraft({ ...setupDraft, [key]: next });
  };

  if (loading) return <TableSkeleton rows={8} />;

  const examCount = head.exam_highest.length;

  return (
    <div className="page">
      <PageHead
        title="Class record"
        subtitle={
          'The MATATAG three-term E-Class Record. Once the first term is graded, the ' +
          'risk model reads its grade, written works, performance tasks and ' +
          'examinations and predicts the final grade and the chance of finishing ' +
          'below 75. If the AI service is down, a school rule answers instead.'
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
          value={term}
          onChange={(e) => setTerm(Number(e.target.value))}
          aria-label="Term"
        >
          {TERMS.map((t) => (
            <option key={t} value={t}>
              {TERM_LABELS[t]}
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
            <select
              className="select"
              value={head.template}
              onChange={(e) => chooseTemplate(e.target.value)}
              aria-label="Class record type"
            >
              {Object.entries(TEMPLATES).map(([key, t]) => (
                <option key={key} value={key}>
                  {t.short} — {t.ww_weight}/{t.pt_weight}/{t.ex_weight}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-primary" onClick={saveSetup}>
              <Check size={15} /> Save setup
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setSetupDraft(null)}
            >
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

      <p className="cr-template-note">
        {editing ? (
          <>
            Enter the highest possible score for each assessment, choose the record
            type, and save. Every grade in this record is recalculated, because a
            percentage means nothing without the total it came out of.
          </>
        ) : (
          <>
            <b>{profile.short}</b> — {profile.label}. Written works{' '}
            {show(head.ww_weight, 0)}%, performance tasks {show(head.pt_weight, 0)}%,
            examinations {show(head.ex_weight, 0)}%.
          </>
        )}
      </p>

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
                  <th className="cr-group" colSpan={head.ww_highest.length + 3}>
                    Written / Oral Works (WWs)
                    <span className="cr-weight">{show(head.ww_weight, 0)}%</span>
                  </th>
                  <th className="cr-group" colSpan={head.pt_highest.length + 3}>
                    Product / Performance Tasks (PTs)
                    <span className="cr-weight">{show(head.pt_weight, 0)}%</span>
                  </th>
                  <th className="cr-group" colSpan={examCount * 2 + 2}>
                    Examinations (EXs)
                    <span className="cr-weight">{show(head.ex_weight, 0)}%</span>
                  </th>
                  <th className="cr-out" rowSpan={3}>
                    Initial
                    <br />
                    Grade
                  </th>
                  <th className="cr-out" rowSpan={3}>
                    Term
                    <br />
                    Grade
                  </th>
                </tr>

                <tr>
                  {head.ww_highest.map((_, i) => (
                    <th key={`wh${i}`} className="cr-item">
                      {i + 1}
                    </th>
                  ))}
                  <th className="cr-derived">Total</th>
                  <th className="cr-derived">PS</th>
                  <th className="cr-derived">WS</th>

                  {head.pt_highest.map((_, i) => (
                    <th key={`ph${i}`} className="cr-item">
                      {i + 1}
                    </th>
                  ))}
                  <th className="cr-derived">Total</th>
                  <th className="cr-derived">PS</th>
                  <th className="cr-derived">WS</th>

                  {/* Each examination is converted on its own scale, so this
                      group prints the raw marks and then a weighted figure
                      for each one, rather than a single total. */}
                  {head.exam_highest.map((_, i) => (
                    <th key={`eh${i}`} className="cr-item">
                      {EXAM_LABELS[i] || `EX${i + 1}`}
                    </th>
                  ))}
                  {head.exam_highest.map((_, i) => (
                    <th key={`ew${i}`} className="cr-derived">
                      WS {EXAM_LABELS[i] || i + 1}
                    </th>
                  ))}
                  <th className="cr-derived">PS</th>
                  <th className="cr-derived">WS</th>
                </tr>

                <tr className="cr-hps">
                  <HighestCells
                    name="ww_highest"
                    list={head.ww_highest}
                    weight={head.ww_weight}
                    editing={editing}
                    onChange={(i, v) => patchDraft('ww_highest', i, v)}
                  />
                  <HighestCells
                    name="pt_highest"
                    list={head.pt_highest}
                    weight={head.pt_weight}
                    editing={editing}
                    onChange={(i, v) => patchDraft('pt_highest', i, v)}
                  />
                  {/* For the examinations: each mark's maximum, then the
                      share of the component each one carries. */}
                  {head.exam_highest.map((value, i) => (
                    <td key={`ehv${i}`} className="cr-cell">
                      {editing ? (
                        <input
                          className="cr-input"
                          inputMode="decimal"
                          value={asText(value)}
                          onChange={(e) => patchDraft('exam_highest', i, e.target.value)}
                          aria-label={`highest possible score, ${EXAM_LABELS[i] || i + 1}`}
                        />
                      ) : (
                        show(value, 0)
                      )}
                    </td>
                  ))}
                  {head.exam_subweights.map((value, i) => (
                    <td key={`esw${i}`} className="cr-derived cr-hps-label">
                      {editing ? (
                        <input
                          className="cr-input"
                          inputMode="decimal"
                          value={asText(value)}
                          onChange={(e) => patchDraft('exam_subweights', i, e.target.value)}
                          aria-label={`share of the examinations component, ${
                            EXAM_LABELS[i] || i + 1
                          }`}
                        />
                      ) : (
                        show(value, 0)
                      )}
                    </td>
                  ))}
                  <td className="cr-derived cr-hps-label">100.00</td>
                  <td className="cr-derived cr-hps-label">{show(head.ex_weight, 0)}%</td>
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
                      values={row.exam}
                      onChange={(i, v) =>
                        setCell(row.enrollment.enrollment_id, 'exam_scores', i, v)
                      }
                      label={`${row.enrollment.student_name} examination`}
                    />
                    {row.computed.exam_parts.map((part, i) => (
                      <Derived key={`p${i}`} value={part} />
                    ))}
                    <Derived value={row.computed.exam_pct} />
                    <Derived value={row.computed.exam_ws} />

                    <Derived value={row.computed.initial_grade} strong />
                    <td
                      className="cr-derived cr-strong"
                      title={
                        row.computed.transmuted_grade != null && !row.computed.complete
                          ? 'Provisional — some assessments are still unmarked'
                          : undefined
                      }
                    >
                      {show(row.computed.transmuted_grade, 0)}
                      {row.computed.transmuted_grade != null && !row.computed.complete ? ' *' : ''}
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
              <span className="cr-foot-note">
                {' '}
                · a grade marked * is provisional, computed from a record with
                assessments still unmarked
              </span>
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
 * The HIGHEST POSSIBLE SCORE row for a written-works or performance-task
 * group: each item's maximum, then the figures a learner with full marks
 * would reach — the sum, a flat 100.00, and the component's weight.
 */
function HighestCells({ name, list, weight, editing, onChange }) {
  const entered = list.map(Number).filter((n) => Number.isFinite(n) && n > 0);
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
