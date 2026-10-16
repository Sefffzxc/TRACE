import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
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

  // A dashboard card can open this page already filtered: ?level=critical,
  // or ?level=at-risk for At risk and Watch together, as the dashboard counts them.
  const [searchParams] = useSearchParams();
  const [level, setLevel] = useState(() => searchParams.get('level') || 'all');
  const [sectionId, setSectionId] = useState('');
  const [search, setSearch] = useState('');
  const [includeResolved, setIncludeResolved] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [rows, stats, sectionRows] = await Promise.all([
        // The teacher's own sections, as on their dashboard, so the two agree.
        warningApi.list({ include_resolved: includeResolved, mine: true }),
        warningApi.summary({ mine: true }),
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
  // come from the school rule and do not depend on the model at all. A school
  // that tracks units has no AI forecast, so nothing is fetched.
  useEffect(() => {
    let cancelled = false;
    if (units) {
      setPredictions([]);
      return undefined;
    }
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
  }, [units]);

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
      if (level === 'at-risk') {
        if (warning.risk_level !== 'high' && warning.risk_level !== 'moderate') return false;
      } else if (level !== 'all' && warning.risk_level !== level) return false;
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
      // Carried through so the export can be checked, not just read. A school
      // that tracks units has no AI forecast, so it gets no AI columns.
      ...(units
        ? []
        : [
            'AI expected final grade', 'AI range low', 'AI range high', 'AI chance of failing',
            'AI level', 'AI source', 'AI model version', 'AI inputs imputed',
          ]),
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
        ...(units
          ? []
          : [
              prediction?.predicted_final ?? '',
              prediction?.range_low ?? '',
              prediction?.range_high ?? '',
              prediction ? prediction.risk_score : '',
              prediction ? prediction.risk_level : 'not scored',
              prediction ? prediction.source : '',
              prediction ? prediction.model_version : '',
              prediction ? (prediction.imputed_fields || []).join('; ') : '',
            ]),
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
    link.download = `trace-early-warnings-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success('Warning list exported.');
  };

  // The tiles count the rows this page lists: open flags, one per student
  // and subject. Underneath, how many students those flags belong to (one
  // learner can be flagged in several subjects), which is how the dashboard
  // counts.
  const openFlags = warnings.filter((warning) => !warning.is_resolved);
  const tile = (level) => {
    const rows = level ? openFlags.filter((w) => w.risk_level === level) : openFlags;
    return { flags: rows.length, students: new Set(rows.map((w) => w.student_id)).size };
  };
  const counts = { critical: tile('critical'), high: tile('high'), moderate: tile('moderate') };
  const all = tile(null);
  const studentText = (n) => `${n} student${n === 1 ? '' : 's'}`;

  return (
    <div className="page">
      <PageHead
        title="Early warning"
        subtitle="One row per student and subject, most urgent first."
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
          <div className="stat-value">{counts.critical.flags}</div>
          <div className="stat-foot">{studentText(counts.critical.students)}</div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">At risk</span>
            <span className="stat-icon risk">
              <AlertTriangle size={18} />
            </span>
          </div>
          <div className="stat-value">{counts.high.flags}</div>
          <div className="stat-foot">{studentText(counts.high.students)}</div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">Watch</span>
            <span className="stat-icon warn">
              <Eye size={18} />
            </span>
          </div>
          <div className="stat-value">{counts.moderate.flags}</div>
          <div className="stat-foot">{studentText(counts.moderate.students)}</div>
        </div>
        <div className="stat">
          <div className="stat-top">
            <span className="stat-label">All flags</span>
            <span className="stat-icon">
              <ShieldCheck size={18} />
            </span>
          </div>
          <div className="stat-value">{all.flags}</div>
          <div className="stat-foot">{studentText(all.students)}</div>
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
              className={`chip ${
                level === item.key ||
                (level === 'at-risk' && (item.key === 'high' || item.key === 'moderate'))
                  ? 'is-on'
                  : ''
              }`}
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
              {units
                ? 'Pace check is the school rule.'
                : 'Grade check is the school rule; AI forecast predicts the final grade.'}
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
                  ? 'Every student is on track.'
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
                    {!units && <th>AI forecast</th>}
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
                      {!units && (
                        <td>
                          <AiRiskBadge
                            prediction={predictionFor.get(
                              `${warning.student_id}::${warning.subject}`,
                            )}
                            compact
                          />
                        </td>
                      )}
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
