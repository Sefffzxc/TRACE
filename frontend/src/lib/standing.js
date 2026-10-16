/**
 * What a learner's current grade in a subject is, in words.
 *
 * The backend reports a `standing` (0–100, the same axis as a grade) and a
 * `basis` saying which reading produced it — see `basis` in
 * backend/app/services/risk.py. Every screen that shows a current grade takes
 * its wording from here, so the same grade is never described two ways.
 */

/** The short label shown under a grade. */
export const BASIS_LABEL = {
  grades: 'term grade',
  year: 'year average',
  coursework: 'so far',
};

/** The longer explanation shown on hover. */
export const BASIS_HINT = {
  grades: 'The grade of the finished term.',
  year: 'The average of the terms finished so far this year.',
  coursework:
    'The term is not finished: written works and performance tasks banked so far, ' +
    'on the same 0–100 scale as a grade. It will change.',
};
