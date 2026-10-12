/**
 * DepEd class-record arithmetic, on the client.
 *
 * A deliberate mirror of backend/app/services/grading.py. The two must be
 * changed together, and the backend is the authority: what it computes on
 * save is what gets stored and what the model reads. This copy exists only so
 * the totals, percentages and grades move as the teacher types, instead of
 * appearing a second later when the save comes back.
 *
 * Every rule here follows DepEd Order 8, s. 2015:
 *
 *   Total      = sum of the scores actually recorded
 *   PS         = Total / Highest Possible Score x 100
 *   WS         = PS x the component's weight
 *   Initial    = WS(Written Works) + WS(Performance Tasks) + WS(Exams)
 *   Transmuted = Initial, through the transmutation table
 */

/** A blank cell is not a zero. Only cells with a number in them count. */
const recorded = (scores) =>
  (scores || []).filter((s) => s !== null && s !== undefined && s !== '');

export function componentTotal(scores) {
  const got = recorded(scores);
  if (!got.length) return null;
  return round2(got.reduce((a, b) => a + Number(b), 0));
}

/**
 * The denominator. Only items the learner has a score for are counted, so a
 * class record that is half-filled in week three does not report the whole
 * section as failing.
 */
export function highestPossible(scores, highest) {
  if (!scores || !highest) return null;
  let total = 0;
  for (let i = 0; i < scores.length; i += 1) {
    const s = scores[i];
    const cap = Number(highest[i]);
    if (s !== null && s !== undefined && s !== '' && cap) total += cap;
  }
  return total > 0 ? round2(total) : null;
}

/**
 * Not capped at 100. The school's own records contain learners who scored
 * above the highest possible mark on a component, and silently rewriting
 * those would put this sheet out of step with the teacher's.
 */
export function percentageScore(scores, highest) {
  const total = componentTotal(scores);
  const cap = highestPossible(scores, highest);
  if (total === null || !cap) return null;
  return round2((total / cap) * 100);
}

export function weightedScore(percentage, weightPercent) {
  if (percentage === null || percentage === undefined) return null;
  return round2((percentage * weightPercent) / 100);
}

/**
 * The transmutation table, as arithmetic.
 *
 * Above an initial grade of 60 the table advances one point every 1.6 marks
 * from a base of 75; below 60, one point every 4 marks from a base of 60.
 * Both are computed in integer hundredths on purpose: band edges land on
 * values such as 69.6 and 80.8 that binary floating point holds slightly low,
 * and computing them in floats puts rows one grade point under where the
 * published table puts them.
 */
export function transmute(initialGrade) {
  if (initialGrade === null || initialGrade === undefined) return null;
  const hundredths = Math.round(Number(initialGrade) * 100);
  if (hundredths >= 6000) {
    return Math.min(100, 75 + Math.floor((hundredths - 6000) / 160));
  }
  return 60 + Math.floor(hundredths / 400);
}

const DESCRIPTORS = [
  [90, 'O', 'Outstanding'],
  [85, 'VS', 'Very Satisfactory'],
  [80, 'S', 'Satisfactory'],
  [75, 'FS', 'Fairly Satisfactory'],
  [0, 'DNME', 'Did Not Meet Expectations'],
];

export function letterGrade(transmuted) {
  if (transmuted === null || transmuted === undefined) return null;
  return (DESCRIPTORS.find(([floor]) => transmuted >= floor) || [])[1] || null;
}

export function descriptor(transmuted) {
  if (transmuted === null || transmuted === undefined) return null;
  return (DESCRIPTORS.find(([floor]) => transmuted >= floor) || [])[2] || null;
}

/** Every derived figure for one row of the class record. */
export function computeRow(row, setup) {
  const examScores = [...(row.sa_scores || []), row.te_score ?? null];
  const examHighest = [...(setup.sa_highest || []), setup.te_highest ?? null];

  const wwPct = percentageScore(row.ww_scores, setup.ww_highest);
  const ptPct = percentageScore(row.pt_scores, setup.pt_highest);
  const tePct = percentageScore(examScores, examHighest);

  const wwWs = weightedScore(wwPct, setup.ww_weight);
  const ptWs = weightedScore(ptPct, setup.pt_weight);
  const teWs = weightedScore(tePct, setup.te_weight);

  // An initial grade needs all three components. Summing whatever happens to
  // be present treats an un-sat examination as a zero, and that is exactly
  // the defect that made 65 rows of the school's own export read as failures.
  const parts = [wwWs, ptWs, teWs];
  const initial = parts.every((p) => p !== null) ? round2(parts[0] + parts[1] + parts[2]) : null;
  const final = transmute(initial);

  return {
    ww_total: componentTotal(row.ww_scores),
    ww_pct: wwPct,
    ww_ws: wwWs,
    pt_total: componentTotal(row.pt_scores),
    pt_pct: ptPct,
    pt_ws: ptWs,
    te_total: componentTotal(examScores),
    te_pct: tePct,
    te_ws: teWs,
    initial_grade: initial,
    transmuted_grade: final,
    letter_grade: letterGrade(final),
    descriptor: descriptor(final),
    // What the model is shown. The examination is excluded even once it has
    // been recorded: it is the event the warning is meant to come before.
    pre_exam_weighted: wwWs !== null && ptWs !== null ? round2(wwWs + ptWs) : null,
  };
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

/** For display: a number to at most two places, or an em dash when blank. */
export function show(value, places = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return Number(value).toFixed(places).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1');
}
