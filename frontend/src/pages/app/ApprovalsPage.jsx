import { useCallback, useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Check, Clock, Mail, Phone, ShieldCheck, UserCheck, X } from 'lucide-react';
import {
  Avatar,
  EmptyState,
  ErrorNote,
  Modal,
  PageHead,
  TableSkeleton,
} from '../../components/common/ui';
import { staffApi } from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';

/**
 * The gate every new staff account passes through. Nothing a teacher submits
 * becomes usable until an administrator approves it here.
 */
export default function ApprovalsPage() {
  const toast = useToast();
  const context = useOutletContext();

  const [pending, setPending] = useState([]);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [rejecting, setRejecting] = useState(null);
  const [reason, setReason] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rows, auditRows] = await Promise.all([
        staffApi.pending(),
        staffApi.auditLogs({ limit: 15 }),
      ]);
      setPending(rows || []);
      setLogs(auditRows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const approve = async (person) => {
    setBusyId(person.id);
    try {
      const result = await staffApi.adminAction(person.id, 'approve');
      toast.success(result.message);
      await load();
      context?.refreshChrome?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const reject = async () => {
    setBusyId(rejecting.id);
    try {
      const result = await staffApi.adminAction(rejecting.id, 'reject', { reason });
      toast.success(result.message);
      setRejecting(null);
      setReason('');
      await load();
      context?.refreshChrome?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="page">
      <PageHead
        title="Account approvals"
        subtitle="New staff registrations waiting for review."
        actions={
          <span className="header-chip">
            <Clock size={13} /> {pending.length} waiting
          </span>
        }
      />

      <ErrorNote message={error} />

      <div className="card">
        <div className="card-header">
          <div>
            <h2>Pending requests</h2>
            <div className="card-subtitle">
              Approving grants immediate access with the role the person requested.
            </div>
          </div>
        </div>
        <div className="card-body flush">
          {loading ? (
            <TableSkeleton rows={3} columns={4} />
          ) : pending.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title="Nothing waiting for review"
              description="New registration requests appear here as soon as they are submitted."
            />
          ) : (
            pending.map((person) => (
              <div className="approval" key={person.id}>
                <Avatar name={`${person.first_name} ${person.last_name}`} size="lg" />
                <div className="who">
                  <div className="n">
                    {person.first_name} {person.middle_name ? `${person.middle_name} ` : ''}
                    {person.last_name}
                  </div>
                  <div className="e row" style={{ gap: '.35rem' }}>
                    <Mail size={12} /> {person.email}
                  </div>
                  <div className="e row faint" style={{ gap: '.35rem', fontSize: 'var(--text-xs)' }}>
                    <Phone size={11} /> {person.contact_number}
                  </div>
                </div>

                <div className="facts">
                  <div>
                    <span className="k">Requested role</span>
                    <span className="v" style={{ textTransform: 'capitalize' }}>
                      {person.role}
                    </span>
                  </div>
                  <div>
                    <span className="k">Employee no.</span>
                    <span className="v">{person.employee_no || '—'}</span>
                  </div>
                  <div>
                    <span className="k">Department</span>
                    <span className="v">{person.department || '—'}</span>
                  </div>
                  <div>
                    <span className="k">Submitted</span>
                    <span className="v">
                      {person.created_at ? new Date(person.created_at).toLocaleDateString() : '—'}
                    </span>
                  </div>
                </div>

                <div className="acts">
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => setRejecting(person)}
                    disabled={busyId === person.id}
                  >
                    <X size={15} /> Reject
                  </button>
                  <button
                    type="button"
                    className="btn btn-success btn-sm"
                    onClick={() => approve(person)}
                    disabled={busyId === person.id}
                  >
                    <Check size={15} /> {busyId === person.id ? 'Approving…' : 'Approve'}
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <h2>Audit trail</h2>
            <div className="card-subtitle">Every privileged action, newest first</div>
          </div>
        </div>
        <div className="card-body flush">
          {logs.length === 0 ? (
            <EmptyState icon={UserCheck} title="No recorded actions yet" />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Performed by</th>
                    <th>Affecting</th>
                    <th>Detail</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id}>
                      <td className="cell-strong">{log.action.replace(/_/g, ' ')}</td>
                      <td>{log.admin_name || 'System'}</td>
                      <td>{log.target_name || '—'}</td>
                      <td className="muted">{log.detail || '—'}</td>
                      <td className="faint nowrap">
                        {log.created_at ? new Date(log.created_at).toLocaleString() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {rejecting && (
        <Modal
          title="Reject this registration?"
          subtitle={`${rejecting.first_name} ${rejecting.last_name} — ${rejecting.email}`}
          size="sm"
          onClose={() => {
            setRejecting(null);
            setReason('');
          }}
          footer={
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  setRejecting(null);
                  setReason('');
                }}
              >
                Cancel
              </button>
              <button type="button" className="btn btn-danger" onClick={reject} disabled={busyId === rejecting.id}>
                {busyId === rejecting.id ? 'Rejecting…' : 'Reject request'}
              </button>
            </>
          }
        >
          <div className="field">
            <label className="field-label" htmlFor="reject_reason">
              Reason (recorded in the audit trail)
            </label>
            <textarea
              id="reject_reason"
              className="textarea"
              placeholder="e.g. Not a member of teaching staff — please apply through HR first."
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
        </Modal>
      )}
    </div>
  );
}
