import { useCallback, useEffect, useState } from 'react';
import {
  Building2,
  Check,
  Clock,
  GraduationCap,
  Mail,
  MapPin,
  RotateCcw,
  School,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import { EmptyState, ErrorNote, Modal, PageHead, TableSkeleton } from '../../components/common/ui';
import { schoolApi } from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';

/**
 * The filters, in the order an operator works through them: the queue first,
 * then the schools already decided on. "Suspended" is the API's `deactivated` —
 * a school whose access was withdrawn after approval, which is a different
 * thing from one that was never let in.
 */
const STATUSES = [
  { key: 'pending', label: 'Pending' },
  { key: 'approved', label: 'Approved' },
  { key: 'rejected', label: 'Rejected' },
  { key: 'deactivated', label: 'Suspended' },
  { key: '', label: 'All' },
];

const STATUS_BADGE = {
  pending: { className: 'badge-moderate', label: 'Pending' },
  approved: { className: 'badge-low', label: 'Approved' },
  rejected: { className: 'badge-critical', label: 'Rejected' },
  deactivated: { className: 'badge-neutral', label: 'Suspended' },
};

/**
 * The same mechanism as account approvals, one level up: there, an
 * administrator decides who may use their school's records; here, the system
 * operator decides which schools exist on the deployment at all.
 *
 * Everything on this screen is about the school as an organisation — who runs
 * it, how big it is, whether it may sign in. The operator never reaches a
 * student record from here; see the note at the top of the page.
 */
export default function SchoolsAdminPage() {
  const toast = useToast();

  const [status, setStatus] = useState('pending');
  const [schools, setSchools] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState(null);

  /* One piece of state for both modals — the action being confirmed and the
     school it applies to. Rejecting requires a reason (the API answers 400
     without one); suspending records whatever the operator cares to add. */
  const [deciding, setDeciding] = useState(null); // { school, action }
  const [reason, setReason] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const rows = await schoolApi.list(status ? { status } : {});
      setSchools(rows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  const decide = async (school, action, withReason) => {
    setBusyId(school.school_id);
    try {
      const result = await schoolApi.decide(school.school_id, action, withReason);
      toast.success(result?.message || `${school.name} updated.`);
      setDeciding(null);
      setReason('');
      await load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const closeModal = () => {
    setDeciding(null);
    setReason('');
  };

  const rejecting = deciding?.action === 'reject';
  /* The reject modal's submit stays disabled until there is a reason, so the
     operator sees why rather than getting a 400 back from the server. */
  const modalBlocked = rejecting && reason.trim().length === 0;

  return (
    <div className="page">
      <PageHead
        title="Schools"
        subtitle="Registered schools and their status."
        actions={
          <span className="header-chip">
            <Building2 size={13} /> {schools.length} shown
          </span>
        }
      />

      {/* The boundary this console is built around, stated where the operator
          will read it. It is a design decision, not an unfinished screen. */}
      <div className="alert alert-info" role="note">
        <ShieldCheck size={17} />
        <span>
          You manage <strong>schools and accounts only</strong>. Student records are not visible here.
        </span>
      </div>

      <ErrorNote message={error} />

      <div className="toolbar">
        <div className="filter-chips">
          {STATUSES.map((item) => (
            <button
              key={item.key || 'all'}
              type="button"
              className={`chip ${status === item.key ? 'is-on' : ''}`}
              onClick={() => setStatus(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <span className="result-count">
          {schools.length} school{schools.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="card">
        <div className="card-header">
          <div>
            <h2>{STATUSES.find((item) => item.key === status)?.label || 'All'} schools</h2>
            <div className="card-subtitle">Approving lets the principal sign in.</div>
          </div>
        </div>
        <div className="card-body flush">
          {loading ? (
            <TableSkeleton rows={4} columns={4} />
          ) : schools.length === 0 ? (
            <EmptyState
              icon={School}
              title={status === 'pending' ? 'Nothing waiting for review' : 'No schools to show'}
              description={
                status === 'pending'
                  ? 'New registrations appear here.'
                  : 'Try another filter.'
              }
            />
          ) : (
            schools.map((school) => {
              const badge = STATUS_BADGE[school.status] || STATUS_BADGE.pending;
              const busy = busyId === school.school_id;
              return (
                <div className="approval" key={school.school_id}>
                  <span className="avatar avatar-lg" title={school.name}>
                    <Building2 size={20} />
                  </span>

                  <div className="who">
                    <div className="n">{school.name}</div>
                    <div className="e row" style={{ gap: '.35rem' }}>
                      <MapPin size={12} /> {school.city || '—'}
                      {school.province ? `, ${school.province}` : ''}
                    </div>
                    <div className="row" style={{ gap: '.4rem', marginTop: '.3rem', flexWrap: 'wrap' }}>
                      <span className={`badge ${badge.className}`}>{badge.label}</span>
                      <span className="badge badge-brand badge-plain">
                        {school.school_type === 'public' ? 'Public' : 'Private'}
                      </span>
                      {school.tracks_units ? (
                        <span className="badge badge-info badge-plain">
                          <GraduationCap size={11} /> Tracks{' '}
                          {school.unit_label_plural || school.unit_label_singular || 'units'}
                        </span>
                      ) : (
                        <span className="badge badge-neutral badge-plain">No unit tracking</span>
                      )}
                    </div>
                    {school.status === 'rejected' && school.rejection_reason && (
                      <div className="e faint" style={{ fontSize: 'var(--text-xs)', marginTop: '.3rem' }}>
                        Reason on file: {school.rejection_reason}
                      </div>
                    )}
                  </div>

                  <div className="facts">
                    <div>
                      <span className="k">Principal</span>
                      <span className="v">{school.principal_name || '—'}</span>
                    </div>
                    <div>
                      <span className="k">
                        <Mail size={11} /> Principal email
                      </span>
                      <span className="v">{school.principal_email || '—'}</span>
                    </div>
                    <div>
                      <span className="k">
                        <Users size={11} /> Staff
                      </span>
                      <span className="v">{school.staff_count ?? 0}</span>
                    </div>
                    <div>
                      <span className="k">Students</span>
                      <span className="v">{school.student_count ?? 0}</span>
                    </div>
                    <div>
                      <span className="k">
                        <Clock size={11} /> Registered
                      </span>
                      <span className="v">
                        {school.created_at ? new Date(school.created_at).toLocaleDateString() : '—'}
                      </span>
                    </div>
                  </div>

                  <div className="acts">
                    {school.status === 'pending' && (
                      <>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => setDeciding({ school, action: 'reject' })}
                          disabled={busy}
                        >
                          <X size={15} /> Reject
                        </button>
                        <button
                          type="button"
                          className="btn btn-success btn-sm"
                          onClick={() => decide(school, 'approve')}
                          disabled={busy}
                        >
                          <Check size={15} /> {busy ? 'Approving…' : 'Approve'}
                        </button>
                      </>
                    )}

                    {school.status === 'approved' && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => setDeciding({ school, action: 'deactivate' })}
                        disabled={busy}
                      >
                        <X size={15} /> Suspend
                      </button>
                    )}

                    {(school.status === 'rejected' || school.status === 'deactivated') && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => decide(school, 'reactivate')}
                        disabled={busy}
                      >
                        <RotateCcw size={15} /> {busy ? 'Restoring…' : 'Restore'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {deciding && (
        <Modal
          title={rejecting ? 'Reject this registration?' : 'Suspend this school?'}
          subtitle={`${deciding.school.name}${deciding.school.city ? ` — ${deciding.school.city}` : ''}`}
          size="sm"
          onClose={closeModal}
          footer={
            <>
              <button type="button" className="btn btn-secondary" onClick={closeModal}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => decide(deciding.school, deciding.action, reason.trim())}
                disabled={busyId === deciding.school.school_id || modalBlocked}
              >
                {busyId === deciding.school.school_id
                  ? 'Working…'
                  : rejecting
                    ? 'Reject registration'
                    : 'Suspend school'}
              </button>
            </>
          }
        >
          <p className="muted" style={{ marginBottom: '.75rem' }}>
            {rejecting
              ? 'The school head sees your reason.'
              : 'Everyone at this school is signed out. Records are kept.'}
          </p>
          <div className="field">
            <label className="field-label" htmlFor="school_decision_reason">
              {rejecting ? 'Reason (required)' : 'Reason (optional)'}
            </label>
            <textarea
              id="school_decision_reason"
              className="textarea"
              placeholder={
                rejecting
                  ? 'e.g. Please re-apply with a DepEd permit number.'
                  : 'e.g. Closed for the school year.'
              }
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            {modalBlocked && (
              <span className="field-hint">A reason is required before a school can be rejected.</span>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
