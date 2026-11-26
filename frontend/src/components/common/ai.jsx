/**
 * Everything the UI shows about the machine-learning classifier.
 *
 * One rule runs through all of it: a number never appears on its own. Every
 * model figure is shown with where it came from (the model, or the rule-based
 * fallback), whether any input was missing, and which model version produced
 * it. The pieces that enforce that live here so no screen can accidentally
 * render a bare probability.
 */
import { AlertTriangle, Brain, ChevronDown, CircleSlash, Info, ServerOff, Sparkles } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

export const AI_HELP =
  'Predicts the final grade from completed periods. Update after the second period is graded.';

export function predictionBasis(prediction, periodLabel = 'Period') {
  if (!prediction || prediction.predicted_final == null) return null;
  const updated = prediction.features?.q2_term_grade != null ||
    prediction.model_version?.includes('second-term');
  return `Based on ${updated ? 'First and Second' : 'First'} ${periodLabel} results`;
}

export function AiPredictionBasis({ prediction }) {
  const { periodLabel } = useAuth();
  const basis = predictionBasis(prediction, periodLabel);
  return basis ? <span className="faint" style={{ fontSize: 'var(--text-xs)' }}>{basis}</span> : null;
}

/** "74 (72–76)", or null when the prediction carries no final grade. */
export function finalGradeText(prediction) {
  if (prediction?.predicted_final == null) return null;
  const grade = Math.round(prediction.predicted_final);
  if (prediction.range_low == null || prediction.range_high == null) return `${grade}`;
  return `${grade} (${Math.round(prediction.range_low)}–${Math.round(prediction.range_high)})`;
}

/* ── The score itself ────────────────────────────────────────────────────── */
/**
 * `prediction` is a row from /api/ai/predictions (or the `ai` object on a
 * student profile subject). `null` renders a "not scored yet" chip rather than
 * nothing, so an unscored record is visibly unscored.
 */
export function AiRiskBadge({ prediction, compact = false }) {
  if (!prediction) {
    return (
      <span
        className="badge badge-neutral badge-plain"
        title="Starts once the first period is graded."
      >
        <CircleSlash size={12} /> No forecast yet
      </span>
    );
  }

  const percent = Math.round((prediction.risk_score || 0) * 100);
  const isFallback = prediction.source === 'fallback';
  const isHigh = prediction.risk_level === 'HIGH';
  const finalText = finalGradeText(prediction);
  // One wording for every screen and both sources: the verdict, then the
  // expected final grade. The short form (tables) stops there; the full form
  // adds the range and the chance of failing. Rows from earlier models carry
  // no final grade and show their percentage instead.
  const verdict = isHigh ? 'likely to fail' : 'on track';
  const tail = finalText
    ? compact
      ? ` · ${Math.round(prediction.predicted_final)}`
      : ` · final ≈ ${finalText} · ${percent}% fail`
    : ` · ${percent}%`;

  if (isFallback) {
    return (
      <span
        className="badge badge-neutral ai-badge is-fallback"
        title={
          `Rule-based: the AI service was unavailable` +
          (finalText ? `: expected final grade ${finalText}, ${percent}% chance of finishing below 75. ` : '. ') +
          `${prediction.error || ''}`
        }
      >
        <ServerOff size={12} />
        Rule-based: {verdict}
        {tail}
      </span>
    );
  }

  return (
    <span className="row" style={{ gap: '.35rem', flexWrap: 'nowrap' }}>
      <span
        className={`badge ai-badge ${isHigh ? 'is-high' : 'is-low'}`}
        title={
          finalText
            ? `Expected final grade ${finalText} · ${percent}% chance of finishing below 75 · ` +
              `flags at ${Math.round((prediction.used_threshold || 0) * 100)}% · ` +
              `${prediction.model_version}`
            : `Model probability ${percent}% · flags at ${Math.round(
                (prediction.used_threshold || 0) * 100,
              )}% · ${prediction.model_version}`
        }
      >
        <Brain size={12} />
        AI: {verdict}
        {tail}
      </span>
      {prediction.used_baseline_defaults && (
        <span
          className="badge badge-moderate badge-plain"
          title={
            prediction.imputed_fields?.length
              ? `Missing inputs filled with typical values: ${prediction.imputed_fields.join(', ')}.`
              : 'Some inputs were missing and filled with typical values.'
          }
        >
          <AlertTriangle size={11} /> partial data
        </span>
      )}
    </span>
  );
}

/* ── Why this student ────────────────────────────────────────────────────── */
export function AiReason({ prediction }) {
  if (!prediction || prediction.risk_level !== 'HIGH') return null;
  return (
    <div className="ai-reason">
      <Sparkles size={14} />
      <div>
        {prediction.primary_factor && (
          <div>
            Biggest contributing factor: <strong>{prediction.primary_factor}</strong>
          </div>
        )}
        {prediction.suggested_action && <div className="faint">{prediction.suggested_action}</div>}
        {prediction.source === 'fallback' && (
          <div className="faint">
            From the school rule; the AI service was unavailable.
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Service state ───────────────────────────────────────────────────────── */
export function AiServiceNote({ status, onRetry, busy }) {
  if (!status) return null;

  if (!status.enabled) {
    return (
      <div className="alert alert-info">
        <Info size={17} />
        <span>
          AI forecasts are off. Only the school rules are used.
        </span>
      </div>
    );
  }

  if (!status.reachable) {
    return (
      <div className="alert alert-warning">
        <ServerOff size={17} />
        <span>
          The AI service is asleep. Forecasts use the school rule until it wakes (about a minute).
          {status.detail ? ` (${status.detail})` : ''}
        </span>
        {onRetry && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry} disabled={busy}>
            {busy ? 'Waking…' : 'Wake it up'}
          </button>
        )}
      </div>
    );
  }

  if (!status.model_loaded) {
    return (
      <div className="alert alert-error">
        <AlertTriangle size={17} />
        <span>
          The AI model failed to load.
          {status.detail ? ` (${status.detail})` : ''}
        </span>
      </div>
    );
  }

  return null;
}

/* ── Model card ──────────────────────────────────────────────────────────── */
/**
 * The limitations come from the model service verbatim and are rendered as
 * plainly as the metrics. A screen that shows the accuracy without them would
 * be the exact overclaim this project set out to avoid.
 */
export function ModelCard({ card, title = 'About the model' }) {
  if (!card) return null;
  const metrics = card.reported_metrics || {};
  const baseline = card.baseline_for_comparison || {};

  return (
    <details className="card ai-model-card">
      <summary className="card-header ai-model-toggle">
        <h2>{title}</h2>
        <ChevronDown size={20} className="ai-model-chevron" aria-hidden="true" />
      </summary>
      <div className="card-body">
        <p className="ai-model-overview">
          {card.algorithm} · {card.features?.length ?? 0} inputs · flags at{' '}
          {Math.round((card.threshold || 0) * 100)}% chance of failing · version{' '}
          {card.model_version}
        </p>
        <div className="detail-row">
          <span className="k">Predicts</span>
          <span className="v">{card.target}</span>
        </div>

        {Object.keys(metrics).length > 0 && (
          <>
            <h3 className="ai-h3">Measured on held-out students</h3>
            <div className="ai-metrics">
              {Object.entries(metrics).map(([name, value]) => (
                <div key={name} className="ai-metric">
                  <span className="k">{name.replace(/_/g, ' ')}</span>
                  <span className="v">
                    {typeof value === 'number' ? value.toFixed(3) : String(value)}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}

        {Object.keys(baseline).length > 0 && (
          <>
            <h3 className="ai-h3">
              Compared with the school rule
            </h3>
            <div className="ai-metrics">
              {Object.entries(baseline).map(([name, value]) => (
                <div key={name} className="ai-metric">
                  <span className="k">{name.replace(/_/g, ' ')}</span>
                  <span className="v">
                    {typeof value === 'number' ? value.toFixed(3) : String(value)}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}

        {card.limitations?.length > 0 && (
          <div className="ai-limits">
            <h3 className="ai-h3">
              <AlertTriangle size={14} /> What it cannot do
            </h3>
            <ul>
              {card.limitations.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        )}

        {card.features?.length > 0 && (
          <details className="ai-details">
            <summary>Inputs it reads ({card.features.length})</summary>
            <ul className="ai-feature-list">
              {card.features.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </details>
  );
}

/* ── The exact input behind one prediction ───────────────────────────────── */
export function AiInputTable({ prediction }) {
  const { periodLabel } = useAuth();
  if (!prediction?.features) return null;
  const imputed = new Set(prediction.imputed_fields || []);
  // Keyed to the model's own feature names — see FEATURES in
  // ml/train_year_model.py and MODEL_FIELDS in app/services/ml_client.py.
  // A name missing here shows the raw feature name rather than nothing, so a
  // model that gains an input is never silently hidden from the teacher.
  // The second group belongs to the earlier per-term model, kept so its
  // stored predictions still read properly in the history.
  const LABELS = {
    q1_term_grade: `First ${periodLabel}: grade`,
    q1_initial_grade: `First ${periodLabel}: initial grade (before transmutation)`,
    q1_ww_pct: `First ${periodLabel}: written works (%)`,
    q1_pt_pct: `First ${periodLabel}: performance tasks (%)`,
    q1_exam_pct: `First ${periodLabel}: examinations (%)`,
    q1_composite: `First ${periodLabel}: overall (30/50/20 weights)`,
    q1_component_gap: `First ${periodLabel}: gap between components`,
    q2_term_grade: `Second ${periodLabel}: grade`,
    q2_initial_grade: `Second ${periodLabel}: initial grade (before transmutation)`,
    q2_ww_pct: `Second ${periodLabel}: written works (%)`,
    q2_pt_pct: `Second ${periodLabel}: performance tasks (%)`,
    q2_exam_pct: `Second ${periodLabel}: examinations (%)`,
    q2_component_gap: `Second ${periodLabel}: gap between components`,
    ww_total: 'Written works (raw total)',
    ww_pct: 'Written works (%)',
    ww_weighted: 'Written works (weighted)',
    pt_total: 'Performance tasks (raw total)',
    pt_pct: 'Performance tasks (%)',
    pt_weighted: 'Performance tasks (weighted)',
    pre_exam_weighted: 'Coursework banked before the examinations',
    component_gap: 'Gap between the two components',
    grade_level: 'Grade level',
    quarter: periodLabel,
  };

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Input</th>
            <th className="right">Value used</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {/* Driven by what the prediction actually carries, not by the list
              above: a model that gains an input then shows up here under its
              own name rather than vanishing from the table. */}
          {Object.keys(prediction.features).map((key) => {
            // The service reports what it substituted using the model's own
            // feature names, which is what `key` is. Looking the display
            // label up here instead meant the badge never appeared.
            const wasImputed = imputed.has(key);
            const value = prediction.features[key];
            return (
              <tr key={key}>
                <td>{LABELS[key] || key}</td>
                <td className="right cell-strong">
                  {value === null || value === undefined ? '—' : value}
                </td>
                <td>
                  {wasImputed ? (
                    <span className="badge badge-moderate badge-plain">
                      training median (not encoded)
                    </span>
                  ) : (
                    <span className="faint">encoded</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
