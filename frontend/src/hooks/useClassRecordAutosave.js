import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { classRecordApi, enrollmentApi, paceApi } from '../api/endpoints';
import { CORE, TEMPLATES, computeRow } from '../lib/grading';

export const DEFAULT_RECORD_SETUP = {
  template: CORE, ww_highest: [null, null, null, null, null],
  pt_highest: [null, null, null], exam_highest: [null, null, null],
  ww_weight: TEMPLATES[CORE].ww_weight, pt_weight: TEMPLATES[CORE].pt_weight,
  ex_weight: TEMPLATES[CORE].ex_weight, exam_subweights: [30, 30, 40],
};
const GROUPS = [['ww_scores', 'ww_highest'], ['pt_scores', 'pt_highest'], ['exam_scores', 'exam_highest']];
const blank = (value) => value == null || String(value).trim() === '';
const number = (value) => blank(value) ? null : Number(value);
export const scoreText = (value) => value == null ? '' : String(value);

function normalizeSetup(value) {
  return {
    template: value.template || CORE,
    ...Object.fromEntries(['ww_highest', 'pt_highest', 'exam_highest', 'exam_subweights'].map((key) => [key, (value[key]?.length ? value[key] : DEFAULT_RECORD_SETUP[key]).map(number)])),
    ...Object.fromEntries(['ww_weight', 'pt_weight', 'ex_weight'].map((key) => [key, Number(value[key] ?? DEFAULT_RECORD_SETUP[key])])),
  };
}
function setupChanged(state) {
  return state.setupDraft !== null && Boolean(setupProblem(state.setupDraft) || JSON.stringify(normalizeSetup(state.setupDraft)) !== JSON.stringify(normalizeSetup(state.setup)));
}
function hasChanges(state) { return Boolean(Object.keys(state.drafts).length || setupChanged(state)); }
function setupProblem(head) {
  for (const [, key] of GROUPS) {
    if (head[key].some((value) => !blank(value) && (!Number.isFinite(Number(value)) || Number(value) <= 0))) return 'Highest possible scores must be positive numbers or blank.';
  }
  const weightKeys = ['ww_weight', 'pt_weight', 'ex_weight'];
  if (weightKeys.some((key) => blank(head[key])) || head.exam_subweights.some(blank)) return 'Enter all component weights and examination shares.';
  const weights = weightKeys.map((key) => Number(head[key]));
  if (weights.some((value) => !Number.isFinite(value) || value < 0 || value > 100) || Math.abs(weights.reduce((a, b) => a + b, 0) - 100) > 0.01) return 'The three component weights must total 100%.';
  const shares = head.exam_subweights.map(Number);
  if (shares.some((value) => !Number.isFinite(value) || value < 0 || value > 100) || Math.abs(shares.reduce((a, b) => a + b, 0) - 100) > 0.01) return 'Examination shares must total 100%.';
  return '';
}
function recordRows(state) {
  const head = state.setupDraft || state.setup;
  return state.enrollments.map((enrollment) => {
    const stored = state.paces.find((pace) => pace.enrollment_id === enrollment.enrollment_id && pace.subject === state.context.subject_name && (pace.grading_period ?? 1) === state.context.grading_period);
    const draft = state.drafts[enrollment.enrollment_id] || {};
    const cells = (key, highest) => Array.from({ length: head[highest].length }, (_, index) => draft[key]?.[index] !== undefined ? draft[key][index] : scoreText(stored?.[key]?.[index]));
    const ww = cells('ww_scores', 'ww_highest'), pt = cells('pt_scores', 'pt_highest'), exam = cells('exam_scores', 'exam_highest');
    const computed = computeRow({ ww_scores: ww.map(number), pt_scores: pt.map(number), exam_scores: exam.map(number) }, normalizeSetup(head));
    if (rowProblem({ ww, pt, exam }, head) || setupProblem(head)) {
      computed.initial_grade = null;
      computed.transmuted_grade = null;
    }
    return { enrollment, stored, ww, pt, exam, dirty: Object.keys(draft).length > 0, computed };
  }).sort((a, b) => (a.enrollment.student_name || '').localeCompare(b.enrollment.student_name || ''));
}
function rowProblem(row, head) {
  for (const [index, values] of [row.ww, row.pt, row.exam].entries()) {
    for (const [item, value] of values.entries()) {
      if (blank(value)) continue;
      const score = Number(value), max = Number(head[GROUPS[index][1]][item]);
      if (!Number.isFinite(score) || score < 0 || score > 1000) return 'Scores must be numbers from 0 to 1000.';
      if (!Number.isFinite(max) || max <= 0) return 'Enter the highest possible score for every assessment with a learner score.';
      if (score > max) return 'A learner score exceeds its highest possible score.';
    }
  }
  return '';
}

/** One writer for a sheet: snapshot acknowledgements never clear newer typing.
 * Failed/uncertain batches require an edit or explicit retry, preventing loops. */
export default function useClassRecordAutosave(context, reloadVersion = 0) {
  const key = context.section_id && context.subject_id && context.subject_name
    ? `${context.section_id}:${context.subject_id}:${context.grading_period}` : '';
  const [state, setState] = useState({ key: '', context: {}, ready: false, loading: false,
    enrollments: [], paces: [], setup: DEFAULT_RECORD_SETUP, setupDraft: null, drafts: {},
    revision: 0, saving: false, error: '', savedAt: null });
  const stateRef = useRef(state);
  const mounted = useRef(true);
  const writer = useRef(null);
  const lastAttempt = useRef(-1);
  const update = useCallback((change) => {
    const next = change(stateRef.current);
    stateRef.current = next;
    if (mounted.current) setState(next);
    return next;
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => {
    if (!key) return undefined;
    let live = true;
    const sheetContext = { section_id: Number(context.section_id), subject_id: Number(context.subject_id), grading_period: context.grading_period, subject_name: context.subject_name };
    update((current) => ({ ...current, loading: true, ready: false, error: '' }));
    Promise.all([
      enrollmentApi.list({ section_id: sheetContext.section_id, is_active: true }),
      paceApi.list({ section_id: sheetContext.section_id }),
      classRecordApi.getSetup(sheetContext),
    ]).then(([enrollments, paces, setup]) => {
      if (!live) return;
      // A failed refresh must never replace a sheet's still-unsaved entries.
      update((current) => ({ key, context: sheetContext, ready: true, loading: false,
        enrollments: enrollments || [], paces: paces || [], setup: normalizeSetup(setup || DEFAULT_RECORD_SETUP),
        setupDraft: current.key === key ? current.setupDraft : null,
        drafts: current.key === key ? current.drafts : {}, revision: current.revision,
        saving: false, error: '', savedAt: current.key === key ? current.savedAt : null }));
    }).catch((err) => {
      if (live) update((current) => ({ ...current, loading: false, ready: false, error: `Could not load this class record: ${err.message}` }));
    });
    return () => { live = false; };
    // The key contains these context values. Reload is explicit after import.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reloadVersion, update]);

  const setCell = useCallback((enrollmentId, field, index, value) => {
    update((current) => {
      const row = current.drafts[enrollmentId] || {};
      const values = [...(row[field] || [])]; values[index] = value;
      return { ...current, drafts: { ...current.drafts, [enrollmentId]: { ...row, [field]: values } }, revision: current.revision + 1, error: '' };
    });
  }, [update]);
  const setSetupDraft = useCallback((value) => update((current) => ({ ...current,
    setupDraft: typeof value === 'function' ? value(current.setupDraft || current.setup) : value,
    revision: current.revision + 1, error: '' })), [update]);

  const save = useCallback(() => {
    if (writer.current) return writer.current;
    const captured = stateRef.current;
    if (!captured.ready) return Promise.resolve(false);
    if (!hasChanges(captured)) return Promise.resolve(true);
    lastAttempt.current = captured.revision;
    const task = (async () => {
      update((current) => ({ ...current, saving: true, error: '' }));
      try {
        const head = captured.setupDraft || captured.setup;
        const problem = setupProblem(head);
        if (problem) throw new Error(problem);
        if (setupChanged(captured)) {
          const invalidExisting = recordRows(captured).find((row) => rowProblem(row, head));
          if (invalidExisting) {
            throw new Error(`${invalidExisting.enrollment.student_name}: ${rowProblem(invalidExisting, head)} Correct the learner score or highest possible score before saving this setup.`);
          }
          const saved = await classRecordApi.saveSetup(captured.context, normalizeSetup(head));
          update((current) => ({ ...current, setup: normalizeSetup(saved || head), savedAt: Date.now() }));
        }
        const changed = recordRows(captured).filter((row) => row.dirty);
        const valid = changed.filter((row) => !rowProblem(row, head));
        const invalid = changed.filter((row) => rowProblem(row, head));
        if (valid.length) {
          const entries = valid.map((row) => ({
            student_id: row.enrollment.student_id, enrollment_id: row.enrollment.enrollment_id,
            subject: captured.context.subject_name,
            current_pace: row.stored?.current_pace ?? 0, expected_pace: row.stored?.expected_pace ?? 0,
            total_paces: row.stored?.total_paces ?? 0, attendance: row.stored?.attendance ?? 100,
            average_score: row.stored?.average_score ?? 0,
            ww_scores: row.ww.map(number), pt_scores: row.pt.map(number), exam_scores: row.exam.map(number),
          }));
          const result = await paceApi.bulkSave(entries, captured.context);
          if (result.saved !== entries.length || result.skipped?.length) {
            throw new Error(`The server saved ${result.saved ?? 0} of ${entries.length} learners. All uncertain entries are retained. ${(result.skipped || []).join(' ')}`);
          }
          update((current) => {
            const drafts = { ...current.drafts };
            const paces = [...current.paces];
            for (const entry of entries) {
              const index = paces.findIndex((pace) => pace.enrollment_id === entry.enrollment_id && pace.subject === entry.subject && (pace.grading_period ?? 1) === captured.context.grading_period);
              const saved = { ...paces[index], ...entry, grading_period: captured.context.grading_period };
              if (index < 0) paces.push(saved); else paces[index] = saved;
              if (drafts[entry.enrollment_id] === captured.drafts[entry.enrollment_id]) delete drafts[entry.enrollment_id];
            }
            return { ...current, paces, drafts, savedAt: Date.now() };
          });
        }
        if (invalid.length) throw new Error(`${invalid.length} learner${invalid.length === 1 ? '' : 's'} still need corrections. ${invalid[0].enrollment.student_name}: ${rowProblem(invalid[0], head)}${valid.length ? ' Other valid learner entries were saved.' : ''}`);
        return !hasChanges(stateRef.current);
      } catch (err) {
        update((current) => ({ ...current, error: `Not saved: ${err.message} Your edits remain on this page.` }));
        return false;
      } finally {
        update((current) => ({ ...current, saving: false }));
      }
    })();
    writer.current = task;
    task.finally(() => { if (writer.current === task) writer.current = null; });
    return task;
  }, [update]);

  useEffect(() => {
    if (!state.ready || state.saving || !hasChanges(state) || lastAttempt.current >= state.revision) return undefined;
    const timer = window.setTimeout(save, 1200);
    return () => window.clearTimeout(timer);
  }, [state, save]);

  useEffect(() => {
    const warn = (event) => {
      if (!hasChanges(stateRef.current) && !writer.current) return;
      event.preventDefault(); event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);

  const allRows = useMemo(() => recordRows(state), [state]);
  const flush = useCallback(async () => {
    // A normal save may have captured an older revision just before a context
    // switch. Its acknowledgement is followed by one batch for newer typing.
    for (let pass = 0; pass < 3; pass += 1) {
      if (await save()) return true;
      if (stateRef.current.error || !stateRef.current.ready) return false;
    }
    return !hasChanges(stateRef.current);
  }, [save]);
  const saveOnBlur = useCallback(() => {
    if (stateRef.current.revision > lastAttempt.current && hasChanges(stateRef.current)) save();
  }, [save]);
  return { ...state, allRows, setCell, setSetupDraft, save,
    flush, saveOnBlur,
    dirtyCount: Object.keys(state.drafts).length, setupDirty: setupChanged(state),
    pending: hasChanges(state), ready: state.ready && state.key === key,
    loading: state.loading || Boolean(key && state.key !== key && !state.error),
  };
}
