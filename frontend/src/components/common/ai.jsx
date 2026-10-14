/**
 * Everything the UI shows about the machine-learning classifier.
 *
 * One rule runs through all of it: a number never appears on its own. Every
 * model figure is shown with where it came from (the model, or the rule-based
 * fallback), whether any input was missing, and which model version produced
 * it. The pieces that enforce that live here so no screen can accidentally
 * render a bare probability.
 */
import { AlertTriangle, Brain, CircleSlash, Info, ServerOff, Sparkles } from 'lucide-react';

export const AI_HELP =
  'The classifier estimates the chance of finishing the quarter below the 75 passing mark. ' +
  'It is a second opinion beside the school’s own rules — not a replacement for them, ' +
  'and not a decision.';

/* ── The score itself ────────────────────────────────────────────────────── */
/**
 * `prediction` is a row from /api/ai/predictions (or the `ai` object on a
 * student profile subject). `null` renders a "not scored yet" chip rather than
 * nothing, so an unscored record is visibly unscored.
 */
export function AiRiskBadge({ prediction, compact = false }) {
  if (!prediction) {
    return (
      <span className="badge badge-neutral badge-plain" title="No prediction logged for this record yet.">
        <CircleSlash size={12} /> Not scored
      </span>
    );
  }

  const percent = Math.round((prediction.risk_score || 0) * 100);
  const isFallback = prediction.source === 'fallback';
  const isHigh = prediction.risk_level === 'HIGH';

  if (isFallback) {
    return (
      <span
        className="badge badge-neutral ai-badge is-fallback"
        title={`Rule-based fallback — the AI service did not answer. ${prediction.error || ''}`}
      >
        <ServerOff size={12} />
        Rule-based{compact ? '' : ` · ${percent}%`}
      </span>
    );
  }

  return (
    <span className="row" style={{ gap: '.35rem', flexWrap: 'nowrap' }}>
      <span
        className={`badge ai-badge ${isHigh ? 'is-high' : 'is-low'}`}
        title={`Model probability ${percent}% · flags at ${Math.round(
          (prediction.used_threshold || 0) * 100,
        )}% · ${prediction.model_version}`}
      >
        <Brain size={12} />
        {isHigh ? 'AI: at risk' : 'AI: on track'} · {percent}%
      </span>
      {prediction.used_baseline_defaults && (
        <span
          className="badge badge-moderate badge-plain"
          title={
            prediction.imputed_fields?.length
              ? `Scored with ${prediction.imputed_fields.length} value(s) filled in from the ` +
                `training median: ${prediction.imputed_fields.join(', ')}. Treat this as weaker ` +
                `evidence.`
              : 'Some inputs were not encoded and were filled in from the training median. ' +
                'Treat this as weaker evidence.'
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
            Produced by the school rules, not the model — the AI service was unavailable.
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
          The AI classifier is switched off for this deployment. Risk levels come from the school
          rules only.
        </span>
      </div>
    );
  }

  if (!status.reachable) {
    return (
      <div className="alert alert-warning">
        <ServerOff size={17} />
        <span>
          The AI service is not answering, so new records are being scored by the school rules and
          marked <em>rule-based</em>. On free hosting the service sleeps after 15 minutes of
          inactivity and takes about a minute to start.
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
          The AI service is running but its model file did not load, so nothing can be scored.
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
export function ModelCard({ card }) {
  if (!card) return null;
  const metrics = card.reported_metrics || {};
  const baseline = card.baseline_for_comparison || {};

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <h2>About the model</h2>
          <div className="card-subtitle">
            {card.algorithm} · {card.features?.length ?? 0} inputs · flags at{' '}
            {Math.round((card.threshold || 0) * 100)}% · version {card.model_version}
          </div>
        </div>
      </div>
      <div className="card-body">
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
            <h3 className="ai-h3">Compared with the simple attendance rule</h3>
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
    </div>
  );
}

/* ── The exact input behind one prediction ───────────────────────────────── */
export function AiInputTable({ prediction }) {
  if (!prediction?.features) return null;
  const imputed = new Set(prediction.imputed_fields || []);
  // Keyed to the model's own feature names — see FEATURES in
  // ml/train_risk_model.py and MODEL_FIELDS in app/services/ml_client.py.
  // A name missing here shows the raw feature name rather than nothing, so a
  // model that gains an input is never silently hidden from the teacher.
  const LABELS = {
    ww_total: 'Written works (raw total)',
    ww_pct: 'Written works (%)',
    ww_weighted: 'Written works (weighted)',
    pt_total: 'Performance tasks (raw total)',
    pt_pct: 'Performance tasks (%)',
    pt_weighted: 'Performance tasks (weighted)',
    pre_exam_weighted: 'Coursework banked before the examinations',
    component_gap: 'Gap between the two components',
    grade_level: 'Grade level',
    quarter: 'Term',
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
