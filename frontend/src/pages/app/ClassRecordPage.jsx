import { useEffect, useMemo, useRef, useState } from 'react';
import { useBlocker } from 'react-router-dom';
import { Check, FileUp, Save, Search, Settings2 } from 'lucide-react';
import {
  EmptyState,
  ErrorNote,
  PageHead,
  TableSkeleton,
} from '../../components/common/ui';
import { AiServiceNote } from '../../components/common/ai';
import ImportClassRecord from '../../components/common/ImportClassRecord';
import {
  aiApi,
  assignmentApi,
  sectionApi,
  subjectApi,
} from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import useClassRecordAutosave, { scoreText as asText } from '../../hooks/useClassRecordAutosave';
import {
  CORE,
  EXAM_LABELS,
  TEMPLATES,
  gradingPeriods,
  periodName,
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

export default function ClassRecordPage() {
  const toast = useToast();

  const [sections, setSections] = useState([]);
  const [subjects, setSubjects] = useState([]);

  const [sectionId, setSectionId] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [term, setTerm] = useState(1);
  // How many periods a year has and what one is called come from the
  // school's settings, so this sheet says "Quarter" or "Term" as the school
  // does.
  const { gradingPeriodCount, periodLabel } = useAuth();
  const periods = gradingPeriods(gradingPeriodCount);
  const [search, setSearch] = useState('');
  const [importing, setImporting] = useState(false);
  // Bumped after an import so the header is fetched again.
  const [setupVersion, setSetupVersion] = useState(0);

  const [changingContext, setChangingContext] = useState(false);
  const changingRef = useRef(false);

  const [aiStatus, setAiStatus] = useState(null);
  const [waking, setWaking] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // section id -> subject names this teacher grades there ("*" = all);
  // null for an administrator, who is not limited.
  const [myClasses, setMyClasses] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [sectionRows, subjectRows, mine] = await Promise.all([
          sectionApi.list(),
          subjectApi.list({ is_active: true }),
          assignmentApi.mine(),
        ]);
        // Only the classes this teacher grades: the sections they are
        // assigned to, and in each only their own subjects. The server
        // refuses any other save as well.
        const grade = mine?.grade || {};
        const own = mine?.unrestricted
          ? sectionRows || []
          : (sectionRows || []).filter((s) => grade[String(s.section_id)]);
        setMyClasses(mine?.unrestricted ? null : grade);
        setSections(own);
        setSubjects(subjectRows || []);
        if (own.length) setSectionId(String(own[0].section_id));
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

  const subjectOptions = useMemo(() => {
    if (!section) return [];
    const forLevel = subjects.filter((s) => s.grade_level_id === section.grade_level_id);
    if (!myClasses) return forLevel; // an administrator
    const taught = myClasses[String(section.section_id)] || [];
    return taught.includes('*') ? forLevel : forLevel.filter((s) => taught.includes(s.subject_name));
  }, [subjects, section, myClasses]);

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

  const record = useClassRecordAutosave({
    section_id: sectionId, subject_id: subjectId,
    subject_name: subject?.subject_name, grading_period: term,
  }, setupVersion);
  const { setup, setupDraft, setSetupDraft, setCell, saving, dirtyCount } = record;
  const loadingRoster = record.loading;
  const save = record.save;
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return record.allRows.filter((row) => !needle || (row.enrollment.student_name || '').toLowerCase().includes(needle));
  }, [record.allRows, search]);

  // A context can change only after its setup and every dirty learner have
  // been acknowledged. Failed or invalid entries stay on their original sheet.
  const changeContext = async (apply) => {
    if (changingRef.current) return;
    changingRef.current = true;
    setChangingContext(true);
    try {
      if (await record.flush()) apply();
      else toast.error('Stay on this record and correct or retry the unsaved entries first.');
    } finally {
      changingRef.current = false;
      setChangingContext(false);
    }
  };

  const navigation = useRef(null);
  navigation.current = { unsaved: record.pending || record.saving, flush: record.flush };
  const blocker = useBlocker(() => navigation.current.unsaved);
  const blockedKey = blocker.location?.key;
  const blockedRef = useRef(null);
  useEffect(() => {
    if (blocker.state !== 'blocked' || blockedRef.current === blockedKey) return undefined;
    blockedRef.current = blockedKey;
    let cancelled = false;
    changingRef.current = true;
    setChangingContext(true);
    navigation.current.flush().then((saved) => {
      if (cancelled) return;
      changingRef.current = false;
      setChangingContext(false);
      blockedRef.current = null;
      if (saved) blocker.proceed();
      else {
        blocker.reset();
        toast.error('Stay on this record and correct or retry the unsaved entries first.');
      }
    });
    return () => { cancelled = true; blockedRef.current = null; };
    // Each blocked destination gets one flush; rerendering save progress must
    // not replay navigation or start additional writers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blocker.state, blockedKey]);

  useEffect(() => {
    if (term > periods.length) changeContext(() => setTerm(1));
    // This follows changes to the school's period count, not score edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term, periods.length]);

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

  const saveSetup = () => changeContext(() => setSetupDraft(null));

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
          `Enter scores by ${periodLabel.toLowerCase()}. Grades compute as you type.`
        }
      />

      <ErrorNote message={error} />
      <ErrorNote message={record.error} />
      {!record.ready && !loadingRoster && sectionId && subjectId && <button type="button" className="btn btn-secondary" onClick={() => setSetupVersion((value) => value + 1)}>Retry loading record</button>}
      <AiServiceNote status={aiStatus} onRetry={wakeAi} busy={waking} />

      <div className="toolbar">
        <select
          className="select"
          value={sectionId}
          onChange={(e) => { const value = e.target.value; changeContext(() => setSectionId(value)); }}
          disabled={changingContext || importing || loadingRoster}
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
          onChange={(e) => { const value = e.target.value; changeContext(() => setSubjectId(value)); }}
          aria-label="Subject"
          disabled={!subjectOptions.length || changingContext || importing || loadingRoster}
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
          onChange={(e) => { const value = Number(e.target.value); changeContext(() => setTerm(value)); }}
          disabled={changingContext || importing || loadingRoster}
          aria-label={periodLabel}
        >
          {periods.map((t) => (
            <option key={t} value={t}>
              {periodName(t, periodLabel)}
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
              disabled={changingContext}
              aria-label="Class record type"
            >
              {Object.entries(TEMPLATES).map(([key, t]) => (
                <option key={key} value={key}>
                  {t.short} — {t.ww_weight}/{t.pt_weight}/{t.ex_weight}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-primary" onClick={saveSetup} disabled={changingContext}>
              <Check size={15} /> Done setup
            </button>
          </div>
        ) : (
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setSetupDraft({ ...setup })}
              disabled={!subjectId || !record.ready || changingContext}
            >
              <Settings2 size={15} /> Set up record
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => changeContext(() => setImporting(true))}
              disabled={!subjectId || !section || !record.ready || changingContext}
            >
              <FileUp size={15} /> Import from Excel
            </button>
          </>
        )}
      </div>

      <p className="cr-template-note">
        {editing ? (
          <>
            Enter assessment maxima in the Highest possible score row and learner marks below. Both save automatically after you pause. Done setup closes these settings.
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
      ) : !subject || !record.ready ? (
        <EmptyState
          title={!sections.length ? 'No assigned classes' : !subject ? 'No assigned subjects' : 'Class record unavailable'}
          description={!sections.length || !subject ? 'Ask your school administrator to assign your section and subject before entering scores.' : 'Retry loading this record before entering scores.'}
        />
      ) : !rows.length ? (
        <EmptyState
          title={record.allRows.length ? "No matching learners" : "No learners here yet"}
          description={record.allRows.length ? "Clear the search to see this class. Hidden learner changes still save." : "No learners enrolled in this section yet."}
        />
      ) : (
        <div className="card cr-card">
          <div className="cr-scroll" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) record.saveOnBlur(); }}>
            <table className="cr-table">
              <thead>
                <tr>
                  <th className="cr-sticky cr-name" rowSpan={2}>
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
                    {periodLabel}
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
                  <th className="cr-sticky cr-name" scope="row">Highest possible score</th>
                  <HighestCells
                    name="ww_highest"
                    list={head.ww_highest}
                    weight={head.ww_weight}
                    editing={editing}
                    disabled={changingContext || importing}
                    onChange={(i, v) => patchDraft('ww_highest', i, v)}
                  />
                  <HighestCells
                    name="pt_highest"
                    list={head.pt_highest}
                    weight={head.pt_weight}
                    editing={editing}
                    disabled={changingContext || importing}
                    onChange={(i, v) => patchDraft('pt_highest', i, v)}
                  />
                  {/* For the examinations: each mark's maximum, then the
                      share of the component each one carries. */}
                  {head.exam_highest.map((value, i) => (
                    <td key={`ehv${i}`} className="cr-cell">
                      {editing ? (
                        <input
                          className="cr-input"
                          disabled={changingContext || importing}
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
                          disabled={changingContext || importing}
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
                      disabled={changingContext || importing}
                      values={row.ww}
                      highest={head.ww_highest}
                      row={index}
                      colStart={0}
                      onChange={(i, v) => setCell(row.enrollment.enrollment_id, 'ww_scores', i, v)}
                      label={`${row.enrollment.student_name} written work`}
                    />
                    <Derived value={row.computed.ww_total} />
                    <Derived value={row.computed.ww_pct} />
                    <Derived value={row.computed.ww_ws} />

                    <ScoreCells
                      disabled={changingContext || importing}
                      values={row.pt}
                      highest={head.pt_highest}
                      row={index}
                      colStart={head.ww_highest.length}
                      onChange={(i, v) => setCell(row.enrollment.enrollment_id, 'pt_scores', i, v)}
                      label={`${row.enrollment.student_name} performance task`}
                    />
                    <Derived value={row.computed.pt_total} />
                    <Derived value={row.computed.pt_pct} />
                    <Derived value={row.computed.pt_ws} />

                    <ScoreCells
                      disabled={changingContext || importing}
                      values={row.exam}
                      highest={head.exam_highest}
                      row={index}
                      colStart={head.ww_highest.length + head.pt_highest.length}
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
                          ? 'Provisional: some scores are missing'
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

        </div>
      )}
      {section && subject && !loadingRoster && <div className="save-bar cr-save-status">
        <div className="cr-save-message" role="status" aria-live="polite">
          <strong>{saving ? 'Saving...' : record.error ? 'Not saved' : record.pending ? 'Waiting to save...' : record.savedAt ? 'Saved' : 'Ready to enter scores'}</strong>
          <span>{record.pending ? dirtyCount ? ` - ${dirtyCount} learner${dirtyCount === 1 ? '' : 's'} with pending scores${record.setupDirty ? ' and setup changes' : ''}.` : ' - Setup changes are waiting to save.' : record.savedAt ? ' - All entered changes are saved.' : ' - Your scores save automatically after a pause.'}</span>
          <span className="cr-foot-note"> - * provisional (scores missing)</span>
        </div>
        <button type="button" className="btn btn-primary" onClick={save} disabled={saving || changingContext || !record.pending || !record.ready}>
          <Save size={15} /> {saving ? 'Saving...' : record.error ? 'Retry save' : 'Save now'}
        </button>
      </div>}
      {importing && section && subject && (
        <ImportClassRecord
          section={section}
          subject={subject}
          periodLabel={periodLabel}
          onClose={() => setImporting(false)}
          onDone={(saved) => {
            setImporting(false);
            const added = (saved?.added || 0) + (saved?.enrolled || 0);
            toast.success(
              `Imported and saved ${saved?.terms?.length || 0} ${periodLabel.toLowerCase()}(s)` +
                (added ? `, ${added} learner${added === 1 ? '' : 's'} added.` : '.') + ' No second save is needed.',
            );
            setSetupVersion((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}

/**
 * The HIGHEST POSSIBLE SCORE row for a written-works or performance-task
 * group: each item's maximum, then the figures a learner with full marks
 * would reach — the sum, a flat 100.00, and the component's weight.
 */
function HighestCells({ name, list, weight, editing, onChange, disabled }) {
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
              disabled={disabled}
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

/* Spreadsheet-style movement between score cells, so a teacher can enter a
   whole column without the mouse: Enter or Down goes to the next learner in
   the same column, Up to the previous one. */
function moveTo(row, col) {
  const next = document.querySelector(`.cr-input[data-row="${row}"][data-col="${col}"]`);
  if (next) {
    next.focus();
    next.select();
  }
}

function ScoreCells({ values, highest = [], row, colStart, onChange, label, disabled }) {
  return values.map((value, i) => {
    const col = colStart + i;
    const max = Number(highest[i]);
    const score = Number(value);
    // A score above the item's highest possible is almost always a typo.
    const invalid = value.trim() !== '' && (!Number.isFinite(score) || score < 0 || score > 1000 || !Number.isFinite(max) || max <= 0 || score > max);
    const problem = !Number.isFinite(score) || score < 0 || score > 1000 ? 'Enter a number from 0 to 1000' : max <= 0 || !Number.isFinite(max) ? 'Enter the highest possible score for this assessment first' : `Above the highest possible score (${max})`;
    return (
      <td key={i} className={`cr-cell${invalid ? ' is-invalid' : ''}`}>
        <input
          className="cr-input"
          inputMode="decimal"
          value={value}
          data-row={row}
          data-col={col}
          onChange={(e) => onChange(i, e.target.value)}
          onFocus={(e) => e.target.select()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === 'ArrowDown') {
              e.preventDefault();
              moveTo(row + 1, col);
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              moveTo(row - 1, col);
            }
          }}
          aria-label={`${label} ${i + 1}`}
          disabled={disabled}
          aria-invalid={invalid || undefined}
          title={invalid ? problem : undefined}
        />
      </td>
    );
  });
}

function Derived({ value, strong = false }) {
  return <td className={`cr-derived${strong ? ' cr-strong' : ''}`}>{show(value)}</td>;
}
