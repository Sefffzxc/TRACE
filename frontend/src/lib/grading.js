/**
 * DepEd class-record arithmetic, on the client.
 *
 * A deliberate mirror of backend/app/services/grading.py. The two must be
 * changed together, and the backend is the authority: what it computes on
 * save is what gets stored and what the model reads. This copy exists only so
 * the totals, percentages and grades move as the teacher types, instead of
 * appearing a second later when the save comes back.
 *
 * It follows the UPDATED Grades 2-10 three-term E-Class Record:
 *
 *   Component total = sum of the item scores recorded
 *   Component PS    = Total / Highest Possible Score x 100
 *   Component WS    = PS x the component's weight
 *
 *   Examinations work differently: ST1, ST2 and the term examination each
 *   carry their own share of the component (30, 30, 40), so each is
 *   converted separately and the three are added to make the component's PS.
 *
 *   Initial Grade = WS(WW) + WS(PT) + WS(Exams)
 *   Term Grade    = Initial Grade, through the transmutation table
 */

export const CORE = 'core';
export const MAPEH = 'mapeh';

export const TEMPLATES = {
  [CORE]: {
    label: 'Science · Mathematics · English · Filipino · Araling Panlipunan',
    short: 'Core subject',
    ww_weight: 20,
    pt_weight: 50,
    ex_weight: 30,
  },
  [MAPEH]: {
    label: 'Music and Arts · Physical Education and Health',
    short: 'MAPEH',
    ww_weight: 20,
    pt_weight: 60,
    ex_weight: 20,
  },
};

export const EXAM_SUBWEIGHTS = [30, 30, 40];
export const EXAM_LABELS = ['ST1', 'ST2', 'TE'];
export const TERMS = [1, 2, 3];
export const TERM_LABELS = { 1: 'First Term', 2: 'Second Term', 3: 'Third Term' };

/**
 * (minimum initial grade, transmuted grade), highest band first.
 *
 * Reproduced as data rather than as a formula. The MATATAG bands are
 * irregular — 1.18 apart above an initial grade of 70 and about 4.67 below
 * it, with boundaries published to two decimal places — and a formula fitted
 * to them lands on the wrong side of an edge.
 */
const TRANSMUTATION = [
  [99.5, 100], [98.32, 99], [97.14, 98], [95.96, 97], [94.78, 96],
  [93.6, 95], [92.42, 94], [91.24, 93], [90.06, 92], [88.88, 91],
  [87.7, 90], [86.52, 89], [85.34, 88], [84.16, 87], [82.98, 86],
  [81.8, 85], [80.62, 84], [79.44, 83], [78.26, 82], [77.08, 81],
  [75.9, 80], [74.72, 79], [73.54, 78], [72.36, 77], [71.18, 76],
  [70.0, 75], [65.34, 74], [60.67, 73], [56.01, 72], [51.34, 71],
  [46.67, 70], [42.01, 69], [37.34, 68], [32.68, 67], [28.01, 66],
  [23.35, 65], [18.68, 64], [14.01, 63], [9.35, 62], [4.68, 61],
  [0.0, 60],
];

const DESCRIPTORS = [
  [90, 'Advancing'],
  [80, 'Benchmarking'],
  [75, 'Connecting'],
  [65, 'Developing'],
  [0, 'Emerging'],
];

const blank = (v) => v === null || v === undefined || v === '';

/**
 * Round to two places the way the server does.
 *
 * `Math.round(n * 100) / 100` is the obvious form and it disagrees with
 * Python on values such as 24.665, whose nearest double is a hair BELOW the
 * tie: multiplying by 100 first lands on 2466.5 and rounds up, while Python
 * rounds the exact value and goes down. `toFixed` reads the exact value too,
 * so it agrees. The difference is one hundredth, and one hundredth is enough
 * to show a teacher a grade that is not the one the server stored.
 */
const round2 = (n) => Number(n.toFixed(2));

export function componentTotal(scores) {
  const got = (scores || []).filter((s) => !blank(s)).map(Number);
  return got.length ? round2(got.reduce((a, b) => a + b, 0)) : null;
}

/**
 * Total over the component's FULL highest possible score.
 *
 * The denominator is every item in the HIGHEST POSSIBLE SCORE row, not only
 * the items the learner has a mark for — that is what the workbook does, and
 * what the records the model was trained on were computed with. The
 * consequence is that part-way through a term this reads low for everybody,
 * which is why `complete` travels with it.
 */
export function percentageScore(scores, highest) {
  const total = componentTotal(scores);
  const cap = (highest || []).filter((h) => !blank(h)).reduce((a, b) => a + Number(b), 0);
  if (total === null || cap <= 0) return null;
  return round2((total / cap) * 100);
}

export function weightedScore(percentage, weight) {
  if (percentage === null || percentage === undefined) return null;
  return round2((percentage * weight) / 100);
}

/** ST1, ST2 and the term examination, each on its own scale and share. */
export function examComponent(scores, highest, subweights = EXAM_SUBWEIGHTS) {
  const parts = subweights.map((share, i) => {
    const score = (scores || [])[i];
    const cap = (highest || [])[i];
    if (blank(score) || blank(cap) || Number(cap) === 0) return null;
    return round2((Number(score) / Number(cap)) * share);
  });
  const taken = parts.filter((p) => p !== null);
  return {
    parts,
    // Blank examinations contribute nothing, exactly as the workbook's sum
    // over the three weighted cells does.
    percentage: taken.length ? round2(taken.reduce((a, b) => a + b, 0)) : null,
    complete: parts.every((p) => p !== null),
  };
}

export function transmute(initialGrade) {
  if (initialGrade === null || initialGrade === undefined) return null;
  const value = Number(initialGrade);
  const band = TRANSMUTATION.find(([min]) => value >= min);
  return band ? band[1] : 60;
}

export function descriptor(transmuted) {
  if (transmuted === null || transmuted === undefined) return null;
  const band = DESCRIPTORS.find(([floor]) => transmuted >= floor);
  return band ? band[1] : null;
}

/** Every derived figure for one row of the class record. */
export function computeRow(row, setup) {
  const profile = TEMPLATES[setup.template] || TEMPLATES[CORE];
  const wWw = setup.ww_weight ?? profile.ww_weight;
  const wPt = setup.pt_weight ?? profile.pt_weight;
  const wEx = setup.ex_weight ?? profile.ex_weight;

  const wwPct = percentageScore(row.ww_scores, setup.ww_highest);
  const ptPct = percentageScore(row.pt_scores, setup.pt_highest);
  const exam = examComponent(row.exam_scores, setup.exam_highest, setup.exam_subweights);

  const wwWs = weightedScore(wwPct, wWw);
  const ptWs = weightedScore(ptPct, wPt);
  const exWs = weightedScore(exam.percentage, wEx);

  // The workbook computes an initial grade as soon as ANY component has a
  // mark, summing the others as zero. That is the defect that made 65 rows
  // of the school's own export read as failing learners when they were
  // really empty columns, so this requires all three. An incomplete record
  // shows no grade rather than a wrong one.
  const parts = [wwWs, ptWs, exWs];
  const ready = parts.every((p) => p !== null);
  const initial = ready ? round2(parts[0] + parts[1] + parts[2]) : null;
  const final = transmute(initial);

  return {
    ww_total: componentTotal(row.ww_scores),
    ww_pct: wwPct,
    ww_ws: wwWs,
    pt_total: componentTotal(row.pt_scores),
    pt_pct: ptPct,
    pt_ws: ptWs,
    exam_parts: exam.parts,
    exam_pct: exam.percentage,
    exam_ws: exWs,
    initial_grade: initial,
    transmuted_grade: final,
    descriptor: descriptor(final),
    complete: ready && exam.complete,
    // Coursework banked before the examinations, which the rule reads for a
    // term in progress. The year model reads the finished first term instead.
    pre_exam_weighted: wwWs !== null && ptWs !== null ? round2(wwWs + ptWs) : null,
  };
}

/** For display: a number to at most two places, or an em dash when blank. */
export function show(value, places = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return Number(value)
    .toFixed(places)
    .replace(/\.00$/, '')
    .replace(/(\.\d)0$/, '$1');
}
