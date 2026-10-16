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
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { BASIS_HINT, BASIS_LABEL } from '../../lib/standing';

/** Where the learner sits against the passing mark, e.g. "4 below 75". */
function GapToPassing({ standing, passing }) {
  if (standing === null || standing === undefined) return <span className="faint">—</span>;
  const gap = Math.round(standing - passing);
  if (gap < 0) {
    return (
      <span style={{ color: 'var(--crit-500)', fontWeight: 600 }}>
        {Math.abs(gap)} below {passing}
      </span>
    );
  }
  if (gap === 0) return <span style={{ color: 'var(--warn-500)' }}>At {passing}</span>;
  return (
    <span className={gap < 5 ? '' : 'faint'}>
      {gap} above {passing}
    </span>
  );
}

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
  /* A school that tracks units (PACE booklets) is read on how far behind a
     learner is; every other school is read on grades, and has no booklets,
     completion or pace trend to show. Until the school's settings arrive the
     table waits, so neither layout flashes up for the wrong kind of school. */
  const { school, tracksUnits: units, passingMark: passing } = useAuth();

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
      'Student', 'Section', 'Subject', units ? 'Pace check' : 'Grade check', 'Score',
      ...(units
        ? ['Behind', 'Completion %', 'Trend']
        : ['Current grade', 'Grade type', `Gap to passing (${passing})`]),
      'Action',
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
        ...(units
          ? [warning.paces_behind, warning.pace_percent, warning.trend]
          : [
              warning.standing ?? '',
              BASIS_LABEL[warning.basis] || warning.basis || '',
              warning.standing == null ? '' : Math.round(warning.standing - passing),
            ]),
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
          <div className="stat-foot">
            {units ? 'Furthest behind, needs escalating' : 'Well below passing, needs escalating'}
          </div>
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
          <div className="stat-foot">
            {units ? 'Slipping, but recoverable' : 'Close to the passing mark, still recoverable'}
          </div>
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
              Two independent readings.{' '}
              {units ? (
                <>
                  <strong>Pace check</strong> is the school&rsquo;s own rule, from how far the
                  learner is behind the expected pace; it raises the flags on this page.
                </>
              ) : (
                <>
                  <strong>Grade check</strong> is the school&rsquo;s own rule, from the
                  learner&rsquo;s current grade against the passing mark of {passing}; it raises the
                  flags on this page. <strong>Current grade</strong> is the finished term&rsquo;s
                  grade, the year average so far, or — marked &ldquo;so far&rdquo; — the coursework
                  banked in a term that is not finished yet.
                </>
              )}{' '}
              <strong>AI forecast</strong> is a separate estimate, made once the first term is graded,
              of the final grade and the chance it ends below 75. It is blank until then; if the AI
              service is down, a school rule answers in its place, labelled rule-based. Where the two
              disagree, that is worth a closer look — neither decides anything on its own.
            </div>
          </div>
        </div>
        <div className="card-body flush">
          {loading || !school ? (
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
                    {units ? (
                      <>
                        <th className="right">Behind</th>
                        <th>Completion</th>
                        <th>Trend</th>
                        <th>Pace check</th>
                      </>
                    ) : (
                      <>
                        <th>Current grade</th>
                        <th>Gap to passing</th>
                        <th>Grade check</th>
                      </>
                    )}
                    <th>AI forecast</th>
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
                      {units ? (
                        <>
                          <td className="right cell-strong">{warning.paces_behind}</td>
                          <td style={{ minWidth: 140 }}>
                            <div className="progress-row">
                              <div className="progress">
                                <div
                                  className={`progress-fill is-${warning.risk_level}`}
                                  style={{ width: `${warning.pace_percent}%` }}
                                />
                              </div>
                              <span className="progress-value">
                                {warning.pace_percent.toFixed(0)}%
                              </span>
                            </div>
                          </td>
                          <td>
                            <Trend value={warning.trend} />
                          </td>
                        </>
                      ) : (
                        <>
                          <td title={BASIS_HINT[warning.basis] || ''}>
                            {warning.standing === null || warning.standing === undefined ? (
                              <span className="faint">—</span>
                            ) : (
                              <>
                                <span className="cell-strong">{warning.standing.toFixed(0)}</span>
                                <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                                  {BASIS_LABEL[warning.basis] || ''}
                                </div>
                              </>
                            )}
                          </td>
                          <td>
                            <GapToPassing standing={warning.standing} passing={passing} />
                          </td>
                        </>
                      )}
                      <td>
                        <RiskBadge level={warning.risk_level} />
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
