/**
 * Chart styling that follows the design tokens.
 *
 * The palette is read from CSS custom properties at call time, so charts
 * recolour themselves when the theme switches rather than baking in hex
 * values that only work in light mode.
 */

function readToken(name, fallback) {
  if (typeof window === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function chartColors() {
  return {
    grid: readToken('--border-subtle', '#e6e9f0'),
    axis: readToken('--text-muted', '#667085'),
    brand: readToken('--brand-500', '#3366f2'),
    brandSoft: readToken('--brand-300', '#8eb5ff'),
    gold: readToken('--gold-400', '#f0b429'),
    low: readToken('--ok-500', '#12946a'),
    moderate: readToken('--warn-500', '#c07b05'),
    high: readToken('--risk-500', '#d9541f'),
    critical: readToken('--crit-500', '#d32345'),
  };
}

/** The categorical series palette, ordered so neighbours stay distinguishable. */
export function seriesPalette() {
  const colors = chartColors();
  return [colors.brand, colors.gold, colors.low, colors.high, colors.brandSoft, colors.critical];
}

export const axisProps = (colors) => ({
  stroke: colors.axis,
  tick: { fill: colors.axis, fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: colors.grid },
});
