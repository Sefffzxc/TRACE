import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  Brain,
  Download,
  RefreshCw,
  Search,
  ServerOff,
  Sparkles,
} from 'lucide-react';
import {
  AI_HELP,
  AiInputTable,
  AiRiskBadge,
  AiServiceNote,
  ModelCard,
  finalGradeText,
} from '../../components/common/ai';
import {
  Avatar,
  EmptyState,
  ErrorNote,
  Modal,
  PageHead,
  TableSkeleton,
} from '../../components/common/ui';
import { aiApi, sectionApi } from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';

const LEVELS = [
  { key: 'all', label: 'All' },
  { key: 'HIGH', label: 'Flagged at risk' },
  { key: 'LOW', label: 'Not flagged' },
];

/**
 * The classifier's own screen.
 *
 * It is deliberately not the screen staff act on — that is still Early
 * Warning, which runs on the school's PACE rules. This page answers three
 * questions instead: is the model running, what does it say, and what is it
 * not allowed to be used for.
 */
export default function AiInsightsPage() {
  const navigate = useNavigate();
  const toast = useToast();

  const [status, setStatus] = useState(null);
  const [card, setCard] = useState(null);
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState([]);
  const [sections, setSections] = useState([]);

  const [loading, setLoading] = useState(true);
  const [waking, setWaking] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [error, setError] = useState('');

  const [level, setLevel] = useState('all');
  const [sectionId, setSectionId] = useState('');
  const [search, setSearch] = useState('');
  const [onlyPartial, setOnlyPartial] = useState(false);
  const [detail, setDetail] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // Status first: it wakes a sleeping service, so the model card and the
      // re-score that follow are far more likely to succeed.
      const serviceStatus = await aiApi.status(true);
      setStatus(serviceStatus);

      const [predictions, stats, sectionRows] = await Promise.all([
        aiApi.predictions({ latest_only: true, limit: 1000 }),
        aiApi.summary(),
        sectionApi.list(),
      ]);
      setRows(predictions || []);
      setSummary(stats);
      setSections(sectionRows || []);

      if (serviceStatus.reachable && serviceStatus.model_loaded) {
        setCard(await aiApi.modelCard().catch(() => null));
      } else {
        setCard(null);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const wake = async () => {
    setWaking(true);
    try {
      const next = await aiApi.status(true);
      setStatus(next);
      if (next.reachable && next.model_loaded) {
        setCard(await aiApi.modelCard().catch(() => null));
        toast.success('The AI service is awake and the model is loaded.');
      } else {
        toast.warning(next.detail || 'The AI service is still not answering.');
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setWaking(false);
    }
  };

  const rescore = async () => {
    setRescoring(true);
    try {
      const result = await aiApi.rescore(
        sectionId ? { section_id: Number(sectionId), limit: 2000 } : { limit: 2000 },
      );
      if (result.scored === 0) {
        toast.info('No PACE records matched, so nothing was scored.');
      } else if (result.source === 'model') {
        toast.success(
          `Re-scored ${result.scored} record${result.scored === 1 ? '' : 's'} with ${
            result.model_version
          }.`,
        );
      } else if (result.source === 'fallback') {
        toast.warning(
          `The AI service did not answer, so all ${result.scored} record${
            result.scored === 1 ? '' : 's'
          } fell back to the PACE rules. ${result.detail || ''}`,
        );
      } else {
        toast.warning(
          `${result.scored - result.fallback} scored by the model, ${
            result.fallback
          } by the rules.`,
        );
      }
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setRescoring(false);
    }
  };

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (level !== 'all' && row.risk_level !== level) return false;
      if (sectionId && row.section_name !== sectionId) return false;
      if (onlyPartial && !row.used_baseline_defaults) return false;
      if (term && !(row.student_name || '').toLowerCase().includes(term)) return false;
      return true;
    });
  }, [rows, level, sectionId, onlyPartial, search]);

  const exportCsv = () => {
    const header = [
      'Student', 'Section', 'Subject', 'Expected final grade', 'Range low', 'Range high',
      'Chance of failing', 'Level', 'Threshold',
      'Primary factor', 'Source', 'Model version', 'Inputs imputed', 'Scored at',
    ];
    const lines = filtered.map((row) =>
      [
        row.student_name, row.section_name || '', row.subject,
        row.predicted_final ?? '', row.range_low ?? '', row.range_high ?? '', row.risk_score,
        row.risk_level, row.used_threshold, row.primary_factor || '', row.source,
        row.model_version, (row.imputed_fields || []).join('; '), row.created_at || '',
      ]
        .map((cell) => `"${String(cell).replace(/"/g, '""')}"`)
        .join(','),
    );
    const blob = new Blob([[header.join(','), ...lines].join('\n')], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `lbca-ai-predictions-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success('Predictions exported with their provenance columns.');
  };

  return (
    <div className="page">
      <PageHead
        title="AI risk classification"
        subtitle={AI_HELP}
        actions={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={exportCsv}
              disabled={!filtered.length}
            >
              <Download size={16} /> Export
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={rescore}
              disabled={rescoring || loading}
            >
              <RefreshCw size={16} /> {rescoring ? 'Re-scoring…' : 'Re-score records'}
            </button>
          </>
        }
      />

      <ErrorNote message={error} />
      <AiServiceNote status={status} onRetry={wake} busy={waking} />

      {/* ── What the model currently says ───────────────────────────────── */}
      <section className="grid grid-4">
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Flagged at risk</span>
            <span className="stat-icon risk">
              <Brain size={18} />
            </span>
          </div>
          <div className="stat-value">{summary?.high ?? 0}</div>
          <div className="stat-foot">
            {summary?.students_high ?? 0} student{summary?.students_high === 1 ? '' : 's'}, model
            only
          </div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Records scored</span>
            <span className="stat-icon">
              <Activity size={18} />
            </span>
          </div>
          <div className="stat-value">{summary?.total_scored ?? 0}</div>
          <div className="stat-foot">
            {summary?.from_model ?? 0} by the model
          </div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Rule-based fallbacks</span>
            <span className="stat-icon warn">
              <ServerOff size={18} />
            </span>
          </div>
          <div className="stat-value">{summary?.from_fallback ?? 0}</div>
          <div className="stat-foot">Scored while the model was unreachable</div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Incomplete input</span>
            <span className="stat-icon warn">
              <AlertTriangle size={18} />
            </span>
          </div>
          <div className="stat-value">{summary?.incomplete_input ?? 0}</div>
          <div className="stat-foot">Missing values filled from training medians</div>
        </div>
      </section>

      {summary?.top_factors?.length > 0 && (
        <div className="card">
          <div className="card-header">
            <div>
              <h2>What is driving the flags</h2>
              <div className="card-subtitle">
                The largest contributing input per flagged record. This is a per-student
                attribution, not a claim about which factor causes failure.
              </div>
            </div>
          </div>
          <div className="card-body">
            <div className="grid grid-4">
              {summary.top_factors.map((row) => (
                <div key={row.factor} className="row" style={{ justifyContent: 'space-between' }}>
                  <span>{row.factor}</span>
                  <span className="badge badge-neutral badge-plain">{row.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Filters ─────────────────────────────────────────────────────── */}
      <div className="toolbar">
        <div className="search">
          <Search size={15} />
          <input
            className="input"
            type="search"
            placeholder="Find a student…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <select
          className="select"
          value={sectionId}
          onChange={(event) => setSectionId(event.target.value)}
          aria-label="Filter by section"
        >
          <option value="">All sections</option>
          {sections.map((section) => (
            <option key={section.section_id} value={section.name}>
              {section.name}
            </option>
          ))}
        </select>

        <div className="filter-chips">
          {LEVELS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`chip ${level === item.key ? 'is-on' : ''}`}
              onClick={() => setLevel(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <span className="spacer" />

        <label className="checkbox">
          <input
            type="checkbox"
            checked={onlyPartial}
            onChange={(event) => setOnlyPartial(event.target.checked)}
          />
          Only partial data
        </label>
      </div>

      {/* ── Predictions ─────────────────────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <div>
            <h2>Latest prediction per record</h2>
            <div className="card-subtitle">
              Newest score for each student-subject. Every row keeps the input it was made from —
              open one to see it.
            </div>
          </div>
        </div>
        <div className="card-body flush">
          {loading ? (
            <TableSkeleton rows={8} columns={6} />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={Sparkles}
              title={rows.length === 0 ? 'Nothing scored yet' : 'No prediction matches these filters'}
              description={
                rows.length === 0
                  ? 'Predictions are logged automatically when PACE and assessment data are saved. You can also re-score the existing records with the button above.'
                  : 'Try widening the filters.'
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Student</th>
                    <th>Subject</th>
                    <th>Model says</th>
                    <th>Biggest factor</th>
                    <th>Provenance</th>
                    <th className="actions">Input</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((row) => (
                    <tr key={row.prediction_id}>
                      <td>
                        <div className="cell-name">
                          <Avatar name={row.student_name} size="sm" />
                          <div>
                            <button
                              type="button"
                              className="link-button"
                              style={{ fontWeight: 600, color: 'var(--text-strong)' }}
                              onClick={() => navigate(`/students/${row.student_id}`)}
                            >
                              {row.student_name || `Student ${row.student_id}`}
                            </button>
                            <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                              {row.section_name || '—'}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>{row.subject}</td>
                      <td>
                        <AiRiskBadge prediction={row} />
                      </td>
                      <td className="faint">{row.primary_factor || '—'}</td>
                      <td>
                        <div style={{ fontSize: 'var(--text-2xs)' }}>
                          <div>{row.source === 'model' ? row.model_version : 'PACE rules'}</div>
                          <div className="faint">
                            {row.created_at ? new Date(row.created_at).toLocaleString() : '—'}
                          </div>
                        </div>
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => setDetail(row)}
                        >
                          Show input
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <ModelCard card={card} />

      {!card && status?.reachable === false && (
        <div className="card">
          <div className="card-body">
            <p className="faint">
              The model card — its measured performance and its documented limitations — is read
              from the AI service itself, so it cannot be shown while the service is unreachable.
              Nothing on this page is cached from an earlier version on purpose: a stale accuracy
              figure is worse than none.
            </p>
          </div>
        </div>
      )}

      {detail && (
        <Modal
          title={`${detail.student_name || 'Student'} — ${detail.subject}`}
          subtitle={
            detail.source === 'model'
              ? detail.predicted_final != null
                ? `${detail.model_version}: expected final grade ${finalGradeText(detail)}, ` +
                  `${Math.round(detail.risk_score * 100)}% chance of finishing below 75 ` +
                  `(flagged at ${Math.round(detail.used_threshold * 100)}%)`
                : `Scored by ${detail.model_version} at ${Math.round(
                    detail.risk_score * 100,
                  )}%, flagged above ${Math.round(detail.used_threshold * 100)}%`
              : detail.predicted_final != null
                ? `Produced by the school rule because the AI service was unreachable: ` +
                  `expected final grade ${finalGradeText(detail)}, ` +
                  `${Math.round(detail.risk_score * 100)}% chance of finishing below 75`
                : 'Produced by the school rules because the AI service was unreachable'
          }
          onClose={() => setDetail(null)}
        >
          {detail.source === 'fallback' && (
            <div className="alert alert-warning">
              <ServerOff size={17} />
              <span>
                This row is not a model prediction. {detail.error}
              </span>
            </div>
          )}
          {detail.used_baseline_defaults && (
            <div className="alert alert-warning">
              <AlertTriangle size={17} />
              <span>
                {detail.imputed_fields?.length} of the model&rsquo;s{' '}
                {Object.keys(detail.features || {}).length} inputs were not encoded
                and were replaced with the median from the training data. The score is still
                reported, but it rests on fewer facts about this student.
              </span>
            </div>
          )}
          <AiInputTable prediction={detail} />
          {detail.suggested_action && (
            <p style={{ marginTop: '0.9rem' }}>
              <strong>Suggested next step:</strong> {detail.suggested_action}
            </p>
          )}
          <p className="faint" style={{ marginTop: '0.9rem', fontSize: 'var(--text-xs)' }}>
            Prediction #{detail.prediction_id}
            {detail.latency_ms != null && ` · answered in ${detail.latency_ms} ms`}. Re-scoring adds
            a new row rather than replacing this one, so the history stays intact.
          </p>
        </Modal>
      )}
    </div>
  );
}
