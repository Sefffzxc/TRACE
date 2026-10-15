import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Download,
  Eye,
  Search,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import {
  Avatar,
  EmptyState,
  ErrorNote,
  PageHead,
  RiskBadge,
  TableSkeleton,
} from '../../components/common/ui';
import { AiRiskBadge } from '../../components/common/ai';
import { aiApi, sectionApi, warningApi } from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';

const LEVELS = [
  { key: 'all', label: 'All flags' },
  { key: 'critical', label: 'Critical' },
  { key: 'high', label: 'At risk' },
  { key: 'moderate', label: 'Watch' },
];

function Trend({ value }) {
  if (value === 'improving') {
    return (
      <span className="row" style={{ gap: '.3rem', color: 'var(--ok-500)', fontSize: 'var(--text-xs)' }}>
        <TrendingUp size={13} /> Improving
      </span>
    );
  }
  if (value === 'declining') {
    return (
      <span className="row" style={{ gap: '.3rem', color: 'var(--crit-500)', fontSize: 'var(--text-xs)' }}>
        <TrendingDown size={13} /> Declining
      </span>
    );
  }
  return <span className="faint" style={{ fontSize: 'var(--text-xs)' }}>Stable</span>;
}

export default function EarlyWarningPage() {
  const navigate = useNavigate();
  const toast = useToast();

  const [warnings, setWarnings] = useState([]);
  const [predictions, setPredictions] = useState([]);
  const [summary, setSummary] = useState(null);
  const [sections, setSections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [level, setLevel] = useState('all');
  const [sectionId, setSectionId] = useState('');
  const [search, setSearch] = useState('');
  const [includeResolved, setIncludeResolved] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [rows, stats, sectionRows] = await Promise.all([
        warningApi.list({ include_resolved: includeResolved }),
        warningApi.summary(),
        sectionApi.list(),
      ]);
      setWarnings(rows || []);
      setSummary(stats);
      setSections(sectionRows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [includeResolved]);

  // The model's opinion is loaded separately and on purpose: if it is slow or
  // down, this screen still renders in full, because the flags staff act on
  // come from the PACE rules and do not depend on the model at all.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const rows = await aiApi.predictions({ latest_only: true, limit: 1000 });
        if (!cancelled) setPredictions(rows || []);
      } catch {
        if (!cancelled) setPredictions([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keyed by student + subject, which is how a warning row identifies itself.
  const predictionFor = useMemo(() => {
    const map = new Map();
    predictions.forEach((row) => map.set(`${row.student_id}::${row.subject}`, row));
    return map;
  }, [predictions]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return warnings.filter((warning) => {
      if (level !== 'all' && warning.risk_level !== level) return false;
      if (sectionId && warning.section_name !== sectionId) return false;
      if (term && !(warning.student_name || '').toLowerCase().includes(term)) return false;
      return true;
    });
  }, [warnings, level, sectionId, search]);

  const resolve = async (warning) => {
    setBusyId(warning.warning_id);
    try {
      await warningApi.update(warning.warning_id, { is_resolved: true });
      toast.success(`${warning.student_name} — ${warning.subject} marked as handled.`);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const exportCsv = () => {
    const header = [
      'Student', 'Section', 'Subject', 'Risk', 'Score', 'Behind', 'Completion %',
      'Standing', 'Basis', 'Trend', 'Action',
      // Carried through so the export can be checked, not just read.
      'AI expected final grade', 'AI range low', 'AI range high', 'AI chance of failing',
      'AI level', 'AI source', 'AI model version', 'AI inputs imputed',
    ];
    const lines = filtered.map((warning) => {
      const prediction = predictionFor.get(`${warning.student_id}::${warning.subject}`);
      return [
        warning.student_name,
        warning.section_name || '',
        warning.subject,
        warning.risk_level,
        warning.risk_score,
        warning.paces_behind,
        warning.pace_percent,
        warning.standing ?? '',
        warning.basis ?? '',
        warning.trend,
        warning.intervention || '',
        prediction?.predicted_final ?? '',
        prediction?.range_low ?? '',
        prediction?.range_high ?? '',
        prediction ? prediction.risk_score : '',
        prediction ? prediction.risk_level : 'not scored',
        prediction ? prediction.source : '',
        prediction ? prediction.model_version : '',
        prediction ? (prediction.imputed_fields || []).join('; ') : '',
      ]
        .map((cell) => `"${String(cell).replace(/"/g, '""')}"`)
        .join(',');
    });
    const blob = new Blob([[header.join(','), ...lines].join('\n')], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `lbca-early-warnings-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success('Warning list exported.');
  };

  const counts = summary?.counts || { critical: 0, high: 0, moderate: 0 };

  return (
    <div className="page">
      <PageHead
        title="Early warning"
        subtitle="Students who need attention, ranked by how urgent the gap is."
        actions={
          <button type="button" className="btn btn-secondary" onClick={exportCsv} disabled={!filtered.length}>
            <Download size={16} /> Export list
          </button>
        }
      />

      <ErrorNote message={error} />

      {/* Summary tiles */}
      <section className="grid grid-4">
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Critical</span>
            <span className="stat-icon crit">
              <AlertOctagon size={18} />
            </span>
          </div>
          <div className="stat-value">{counts.critical}</div>
          <div className="stat-foot">Furthest behind, needs escalating</div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">At risk</span>
            <span className="stat-icon risk">
              <AlertTriangle size={18} />
            </span>
          </div>
          <div className="stat-value">{counts.high}</div>
          <div className="stat-foot">Needs a plan this week</div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Watch</span>
            <span className="stat-icon warn">
              <Eye size={18} />
            </span>
          </div>
          <div className="stat-value">{counts.moderate}</div>
          <div className="stat-foot">Slipping, but recoverable</div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Students flagged</span>
            <span className="stat-icon">
              <ShieldCheck size={18} />
            </span>
          </div>
          <div className="stat-value">{summary?.students_flagged ?? 0}</div>
          <div className="stat-foot">
            Across {summary?.total_flagged ?? 0} subject flag
            {summary?.total_flagged === 1 ? '' : 's'}
          </div>
        </div>
      </section>

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
            checked={includeResolved}
            onChange={(event) => setIncludeResolved(event.target.checked)}
          />
          Show handled
        </label>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <h2>Flagged students</h2>
            <div className="card-subtitle">
              Two independent readings. <strong>School rules</strong> is the school&rsquo;s own
              judgement from where the learner stands against the passing mark, and it is what the flags
              and interventions are based on. <strong>AI model</strong> is a separate estimate, made
              once the first term is graded, of the learner&rsquo;s final grade and the chance it
              ends below 75. It is blank until then, and when the AI service is down a school rule
              answers in its place, labelled rule-based. They will
              sometimes disagree — where they do, the disagreement is the useful part, and neither
              one decides anything on its own.
            </div>
          </div>
        </div>
        <div className="card-body flush">
          {loading ? (
            <TableSkeleton rows={8} columns={7} />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={CheckCircle2}
              title="No warnings to show"
              description={
                warnings.length === 0
                  ? 'Every monitored student is on track. Flags appear automatically as records are encoded.'
                  : 'No flag matches these filters.'
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Student</th>
                    <th>Subject</th>
                    <th className="right">Behind</th>
                    <th>Completion</th>
                    <th className="right">Standing</th>
                    <th>Trend</th>
                    <th>School rules</th>
                    <th>AI model</th>
                    <th className="actions">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((warning) => (
                    <tr key={warning.warning_id} className={warning.is_resolved ? 'muted' : ''}>
                      <td>
                        <div className="cell-name">
                          <Avatar name={warning.student_name} size="sm" />
                          <div>
                            <button
                              type="button"
                              style={{
                                background: 'none',
                                border: 0,
                                padding: 0,
                                cursor: 'pointer',
                                font: 'inherit',
                                fontWeight: 600,
                                color: 'var(--text-strong)',
                              }}
                              onClick={() => navigate(`/students/${warning.student_id}`)}
                            >
                              {warning.student_name}
                            </button>
                            <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                              {warning.section_name || '—'}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <div>{warning.subject}</div>
                        <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                          {warning.teacher}
                        </div>
                      </td>
                      <td className="right cell-strong">{warning.paces_behind}</td>
                      <td style={{ minWidth: 140 }}>
                        <div className="progress-row">
                          <div className="progress">
                            <div
                              className={`progress-fill is-${warning.risk_level}`}
                              style={{ width: `${warning.pace_percent}%` }}
                            />
                          </div>
                          <span className="progress-value">{warning.pace_percent.toFixed(0)}%</span>
                        </div>
                      </td>
                      <td className="right">
                        {warning.standing === null || warning.standing === undefined ? (
                          <span className="faint">—</span>
                        ) : (
                          <>
                            <span className="cell-strong">
                              {warning.standing.toFixed(0)}
                            </span>
                            {/* Flagged off coursework rather than a finished
                                record: the grade is not settled yet. */}
                            {warning.basis === 'coursework' && (
                              <span
                                className="faint"
                                title="No term grade yet — flagged on the coursework banked so far"
                              >
                                {' '}*
                              </span>
                            )}
                          </>
                        )}
                      </td>
                      <td>
                        <Trend value={warning.trend} />
                      </td>
                      <td>
                        <RiskBadge level={warning.risk_level} showScore score={warning.risk_score} />
                      </td>
                      <td>
                        <AiRiskBadge
                          prediction={predictionFor.get(
                            `${warning.student_id}::${warning.subject}`,
                          )}
                          compact
                        />
                      </td>
                      <td className="actions">
                        {warning.is_resolved ? (
                          <span className="badge badge-low">Handled</span>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => resolve(warning)}
                            disabled={busyId === warning.warning_id}
                          >
                            {busyId === warning.warning_id ? 'Saving…' : 'Mark handled'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {summary?.by_subject?.length > 0 && (
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Where the flags are concentrated</h2>
              <div className="card-subtitle">Subjects generating the most warnings right now</div>
            </div>
          </div>
          <div className="card-body">
            <div className="grid grid-4">
              {summary.by_subject.slice(0, 8).map((row) => (
                <div key={row.subject} className="row" style={{ justifyContent: 'space-between' }}>
                  <span>{row.subject}</span>
                  <span className="badge badge-neutral badge-plain">{row.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
