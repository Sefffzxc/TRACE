import { useCallback, useEffect, useRef, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Ban, Check, Clock, History, Mail, Phone, RefreshCw, RotateCcw, ShieldCheck, UserCheck, X } from 'lucide-react';
import { Avatar, EmptyState, ErrorNote, Modal, PageHead, TableSkeleton } from '../../components/common/ui';
import ApplicationHistoryModal from '../../components/common/ApplicationHistoryModal';
import EmailCorrectionModal from '../../components/common/EmailCorrectionModal';
import { staffApi } from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';
import { formatPersonName } from '../../lib/personName';

const STATUSES = [
  { key: 'pending', label: 'Pending' },
  { key: 'rejected', label: 'Rejected' },
  { key: 'blocked', label: 'Blocked' },
];

/** Reviews retain the same account and its previous decisions. */
export default function ApprovalsPage() {
  const toast = useToast();
  const context = useOutletContext();
  const [status, setStatus] = useState('pending');
  const [applications, setApplications] = useState([]);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [auditError, setAuditError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [deciding, setDeciding] = useState(null);
  const [reason, setReason] = useState('');
  const [history, setHistory] = useState(null);
  const [emailCorrection, setEmailCorrection] = useState(null);
  const requests = useRef(new Map());
  const auditRequest = useRef(null);
  const activeStatus = useRef(status);
  activeStatus.current = status;

  const loadApplications = useCallback(async ({ afterDecision = false } = {}) => {
    // A mutation needs a fresh read. Polls and StrictMode share their request.
    while (requests.current.has(status)) {
      await requests.current.get(status);
      if (!afterDecision) return;
    }
    if (activeStatus.current === status) setRefreshing(true);
    const request = (status === 'pending' ? staffApi.pending() : staffApi.applicationList(status))
      .then((rows) => {
        if (activeStatus.current !== status) return;
        setApplications(rows || []);
        setError('');
      })
      .catch((err) => { if (activeStatus.current === status) setError(err.message); })
      .finally(() => {
        requests.current.delete(status);
        if (activeStatus.current === status) {
          setLoading(false);
          setRefreshing(false);
        }
      });
    requests.current.set(status, request);
    await request;
  }, [status]);

  const loadAudit = useCallback(async () => {
    if (auditRequest.current) return auditRequest.current;
    const request = staffApi.auditLogs({ limit: 15 })
      .then((rows) => { setLogs(rows || []); setAuditError(''); })
      .catch((err) => setAuditError(err.message))
      .finally(() => { auditRequest.current = null; });
    auditRequest.current = request;
    await request;
  }, []);

  const load = useCallback((options) => {
    loadAudit();
    return loadApplications(options);
  }, [loadApplications, loadAudit]);

  useEffect(() => {
    load();
    const refreshVisibleRequests = () => { if (!document.hidden) loadApplications(); };
    const timer = window.setInterval(refreshVisibleRequests, 30000);
    window.addEventListener('focus', refreshVisibleRequests);
    document.addEventListener('visibilitychange', refreshVisibleRequests);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refreshVisibleRequests);
      document.removeEventListener('visibilitychange', refreshVisibleRequests);
    };
  }, [load, loadApplications]);

  const changeStatus = (next) => {
    if (status === next) return;
    setApplications([]);
    setLoading(true);
    setError('');
    setStatus(next);
  };

  const decide = async (person, action, decisionReason) => {
    if (['reject', 'block'].includes(action) && !decisionReason?.trim()) return;
    setBusyId(person.id);
    try {
      const result = await staffApi.adminAction(person.id, action, decisionReason ? { reason: decisionReason.trim() } : {});
      const fullName = result.user?.full_name || person.full_name || `${person.first_name}${person.middle_name ? ` ${person.middle_name}` : ''} ${person.last_name}`;
      toast.success(result.message.replace(fullName, () => formatPersonName(fullName)));
      setDeciding(null);
      setReason('');
      await load({ afterDecision: true });
      context?.refreshChrome?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const openHistory = (person) => setHistory({
    name: `${formatPersonName(`${person.first_name} ${person.last_name}`)} — ${person.email}`,
    load: () => staffApi.applicationHistory(person.id),
  });
  const closeDecision = () => { if (busyId) return; setDeciding(null); setReason(''); };
  const title = STATUSES.find((item) => item.key === status).label;
  const blocking = deciding?.action === 'block';

  return (
    <div className="page">
      <PageHead title="Account approvals" subtitle="Review staff requests, earlier decisions and resubmissions."
        actions={<span className="header-chip"><Clock size={13} /> {applications.length} {status === 'pending' ? 'waiting' : status}</span>} />
      <ErrorNote message={error} />
      <div className="toolbar">
        <div className="filter-chips" role="group" aria-label="Application status">
          {STATUSES.map((item) => (
            <button key={item.key} type="button" className={`chip ${status === item.key ? 'is-on' : ''}`}
              aria-pressed={status === item.key} onClick={() => changeStatus(item.key)}>{item.label}</button>
          ))}
        </div>
      </div>
      <div className="card">
        <div className="card-header" style={{ flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 14rem' }}>
            <h2>{title} requests</h2>
            <div className="card-subtitle">{status === 'blocked' ? 'Applicants cannot resubmit until an administrator reopens the request.' : status === 'rejected' ? 'Applicants may verify their email, correct their details and resubmit. Earlier decisions are kept.' : 'Updates every 30 seconds. Approving gives access right away.'}</div>
          </div>
          <button type="button" className="btn btn-secondary btn-sm" disabled={refreshing} onClick={() => load()}>
            <RefreshCw size={15} /> {refreshing ? 'Refreshing…' : 'Refresh requests'}
          </button>
        </div>
        <div className="card-body flush">
          {loading ? <TableSkeleton rows={3} columns={4} /> : applications.length === 0 ? (
            <EmptyState icon={ShieldCheck} title={error ? 'Requests could not be loaded' : status === 'pending' ? 'Nothing waiting for review' : `No ${status} requests`}
              description={error ? 'Refresh requests to try again.' : status === 'pending' ? 'New requests appear here.' : 'Try another status.'} />
          ) : applications.map((person) => (
            <div className="approval" key={person.id}>
              <Avatar name={formatPersonName(`${person.first_name} ${person.last_name}`)} size="lg" />
              <div className="who" style={{ overflowWrap: 'anywhere' }}>
                <div className="n">{formatPersonName(`${person.first_name} ${person.middle_name ? `${person.middle_name} ` : ''}${person.last_name}`)}</div>
                <div className="e row" style={{ gap: '.35rem' }}><Mail size={12} /> {person.email}</div>
                <div className="e row faint" style={{ gap: '.35rem', fontSize: 'var(--text-xs)' }}><Phone size={11} /> {person.contact_number}</div>
                {person.rejection_reason && <div className="e" style={{ marginTop: '.4rem', whiteSpace: 'pre-wrap' }}>Reason on file: {person.rejection_reason}</div>}
              </div>
              <div className="facts">
                <div><span className="k">Requested role</span><span className="v">{person.role === 'admin' ? 'Administrator' : 'Teacher'}</span></div>
                <div><span className="k">Submitted</span><span className="v">{person.created_at ? new Date(person.created_at).toLocaleDateString() : '—'}</span></div>
              </div>
              <div className="acts" style={{ flexWrap: 'wrap' }}>
                {person.role === 'teacher' && <button type="button" className="btn btn-secondary btn-sm" disabled={busyId !== null} onClick={() => setEmailCorrection(person)}><Mail size={15} /> Correct email</button>}
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => openHistory(person)}><History size={15} /> History</button>
                {status === 'pending' && <button type="button" className="btn btn-secondary btn-sm" disabled={busyId !== null} onClick={() => { setReason(''); setDeciding({ person, action: 'reject' }); }}><X size={15} /> Reject</button>}
                {status !== 'pending' && <button type="button" className="btn btn-secondary btn-sm" disabled={busyId !== null} onClick={() => decide(person, 'reopen')}><RotateCcw size={15} /> Reopen</button>}
                {status === 'rejected' && <button type="button" className="btn btn-secondary btn-sm" disabled={busyId !== null} onClick={() => { setReason(''); setDeciding({ person, action: 'block' }); }}><Ban size={15} /> Block</button>}
                {status !== 'blocked' && <button type="button" className="btn btn-success btn-sm" disabled={busyId !== null} onClick={() => decide(person, 'approve')}><Check size={15} /> {busyId === person.id ? 'Working…' : 'Approve'}</button>}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><div><h2>Audit trail</h2><div className="card-subtitle">Every privileged action, newest first</div></div></div>
        <ErrorNote message={auditError ? `Audit trail: ${auditError}` : ''} />
        <div className="card-body flush">
          {logs.length === 0 && !auditError ? <EmptyState icon={UserCheck} title="No recorded actions yet" /> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Action</th><th>Performed by</th><th>Affecting</th><th>Detail</th><th>When</th></tr></thead>
              <tbody>{logs.map((log) => <tr key={log.id}><td className="cell-strong">{log.action.replace(/_/g, ' ')}</td><td>{formatPersonName(log.admin_name) || 'System'}</td><td>{formatPersonName(log.target_name) || '—'}</td><td className="muted">{log.detail || '—'}</td><td className="faint nowrap">{log.created_at ? new Date(log.created_at).toLocaleString() : '—'}</td></tr>)}</tbody>
            </table></div>
          )}
        </div>
      </div>

      {deciding && <Modal title={blocking ? 'Block this application?' : 'Reject this registration?'}
        subtitle={<span style={{ overflowWrap: 'anywhere' }}>{`${formatPersonName(`${deciding.person.first_name} ${deciding.person.last_name}`)} — ${deciding.person.email}`}</span>} size="sm" onClose={closeDecision}
        footer={<><button type="button" className="btn btn-secondary" onClick={closeDecision} disabled={busyId !== null}>Cancel</button><button type="button" className="btn btn-danger" onClick={() => decide(deciding.person, deciding.action, reason)} disabled={busyId !== null || !reason.trim()}>{busyId ? 'Working…' : blocking ? 'Block application' : 'Reject request'}</button></>}>
        <p className="muted" style={{ marginBottom: '.75rem' }}>{blocking ? 'This prevents further resubmissions. An administrator can reopen the request later.' : 'The applicant receives your reason and can correct and resubmit using the same account.'}</p>
        <div className="field"><label className="field-label" htmlFor="reject_reason">Reason (required)</label>
          <textarea id="reject_reason" className="textarea" required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} placeholder={blocking ? 'Explain why further applications are blocked.' : 'Explain what the applicant needs to correct.'} />
          {!reason.trim() && <span className="field-hint">Enter a reason before continuing.</span>}
        </div>
      </Modal>}
      {history && <ApplicationHistoryModal name={history.name} loadHistory={history.load} onClose={() => setHistory(null)} />}
      {emailCorrection && <EmailCorrectionModal
        targetId={emailCorrection.id}
        targetName={formatPersonName(`${emailCorrection.first_name} ${emailCorrection.last_name}`)}
        currentEmail={emailCorrection.email}
        onClose={() => setEmailCorrection(null)}
      />}
    </div>
  );
}
