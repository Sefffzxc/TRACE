import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  KeyRound,
  Lock,
  Mail,
  Phone,
  Search,
  ShieldCheck,
  UserCheck,
  UserX,
} from 'lucide-react';
import {
  Avatar,
  ConfirmDialog,
  EmptyState,
  ErrorNote,
  PageHead,
  TableSkeleton,
} from '../../components/common/ui';
import { staffApi } from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';

const STATUS_BADGE = {
  approved: 'badge-low',
  pending: 'badge-moderate',
  rejected: 'badge-critical',
  deactivated: 'badge-neutral',
};

const STATUS_LABEL = {
  approved: 'Approved',
  pending: 'Pending',
  rejected: 'Rejected',
  deactivated: 'Deactivated',
};

const COLUMNS = [
  { key: 'approved', label: 'Approved', color: 'var(--ok-500)' },
  { key: 'pending', label: 'Pending', color: 'var(--warn-500)' },
  { key: 'rejected', label: 'Rejected', color: 'var(--crit-500)' },
  { key: 'deactivated', label: 'Deactivated', color: 'var(--text-faint)' },
];

export default function TeachersPage() {
  const toast = useToast();

  const [staff, setStaff] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // The header search opens this page filtered (?q=name).
  const [params] = useSearchParams();
  const urlQuery = params.get('q') || '';
  const [search, setSearch] = useState(urlQuery);
  useEffect(() => {
    setSearch(urlQuery);
  }, [urlQuery]);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await staffApi.list();
      setStaff(rows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    // Teachers only: the administrator's own account is managed from their
    // profile, not listed here among the people they manage.
    return staff.filter((person) => {
      if (person.role !== 'teacher') return false;
      if (!term) return true;
      return `${person.first_name} ${person.last_name} ${person.email}`.toLowerCase().includes(term);
    });
  }, [staff, search]);

  // Group the filtered teachers into one board column per account status.
  const columns = useMemo(
    () =>
      COLUMNS.map((col) => ({
        ...col,
        people: filtered.filter((person) => {
          const known = COLUMNS.some((c) => c.key === person.account_status);
          return (known ? person.account_status : 'deactivated') === col.key;
        }),
      })),
    [filtered],
  );

  const runAction = async () => {
    const { person, action } = confirm;
    setBusy(true);
    try {
      const result = await staffApi.adminAction(person.id, action);
      if (result.temporary_password) {
        toast.warning(
          `Temporary password for ${person.first_name}: ${result.temporary_password} — share it securely.`,
        );
      } else {
        toast.success(result.message);
      }
      setConfirm(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const ACTIONS = {
    deactivate: {
      title: 'Deactivate this account?',
      message: 'They will be signed out until reactivated.',
      label: 'Deactivate',
      tone: 'danger',
    },
    reactivate: {
      title: 'Reactivate this account?',
      message: 'They can sign in again right away.',
      label: 'Reactivate',
      tone: 'success',
    },
    unlock: {
      title: 'Unlock this account?',
      message: 'Clears failed sign-ins and any lockout.',
      label: 'Unlock',
      tone: 'primary',
    },
    force_reset: {
      title: 'Issue a temporary password?',
      message:
        'A temporary password is shown once. They change it at next sign-in.',
      label: 'Issue password',
      tone: 'primary',
    },
  };

  return (
    <div className="page">
      <PageHead title="Teachers" subtitle="Teacher accounts in your school." />

      <ErrorNote message={error} />

      <div className="toolbar">
        <div className="search">
          <Search size={15} />
          <input
            className="input"
            type="search"
            placeholder="Search by name or email…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <span className="spacer" />
        <span className="result-count">
          {filtered.length} teacher{filtered.length === 1 ? '' : 's'}
        </span>
      </div>

      {loading ? (
        <div className="card">
          <div className="card-body flush">
            <TableSkeleton rows={6} columns={4} />
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card">
          <div className="card-body flush">
            <EmptyState icon={UserCheck} title={search.trim() ? 'No teachers match' : 'No teachers yet'} />
          </div>
        </div>
      ) : (
        <div className="tboard">
          {columns.map((col) => (
            <section key={col.key} className="tboard-col">
              <header className="tboard-col-head">
                <span className="tboard-dot" style={{ background: col.color }} />
                <span className="tboard-col-title">{col.label}</span>
                <span className="tboard-count">
                  {col.people.length} Teacher{col.people.length === 1 ? '' : 's'}
                </span>
              </header>

              <div className="tboard-list">
                {col.people.length === 0 && <div className="tboard-empty">No teachers</div>}
                {col.people.map((person) => (
                  <article key={person.id} className="tcard">
                    <div className="tcard-top">
                      <Avatar
                        name={`${person.first_name} ${person.last_name}`}
                        src={person.profile_pic}
                        size="lg"
                      />
                      <div className="tcard-id">
                        <div className="tcard-name">
                          {person.first_name} {person.last_name}
                        </div>
                        <div className="tcard-sub">
                          Joined{' '}
                          {person.created_at ? new Date(person.created_at).toLocaleDateString() : '—'}
                        </div>
                      </div>
                      <div className="tcard-actions">
                        {person.permanent_lock && (
                          <button
                            type="button"
                            className="btn-icon"
                            title="Unlock account"
                            onClick={() => setConfirm({ person, action: 'unlock' })}
                          >
                            <Lock size={15} />
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn-icon"
                          title="Issue temporary password"
                          onClick={() => setConfirm({ person, action: 'force_reset' })}
                        >
                          <KeyRound size={15} />
                        </button>
                        {person.is_active ? (
                          <button
                            type="button"
                            className="btn-icon"
                            title="Deactivate account"
                            onClick={() => setConfirm({ person, action: 'deactivate' })}
                          >
                            <UserX size={15} />
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn-icon"
                            title="Reactivate account"
                            onClick={() => setConfirm({ person, action: 'reactivate' })}
                          >
                            <ShieldCheck size={15} />
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="tcard-line">
                      <Phone size={14} /> {person.contact_number || '—'}
                    </div>
                    <div className="tcard-line">
                      <Mail size={14} /> <span className="tcard-email">{person.email}</span>
                    </div>

                    <div className="tcard-tags">
                      <span className={`badge ${STATUS_BADGE[person.account_status] || 'badge-neutral'}`}>
                        {STATUS_LABEL[person.account_status] || person.account_status}
                      </span>
                      {person.permanent_lock && <span className="badge badge-critical">Locked</span>}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {confirm && (
        <ConfirmDialog
          title={ACTIONS[confirm.action].title}
          message={`${confirm.person.first_name} ${confirm.person.last_name} — ${
            ACTIONS[confirm.action].message
          }`}
          confirmLabel={ACTIONS[confirm.action].label}
          tone={ACTIONS[confirm.action].tone}
          busy={busy}
          onConfirm={runAction}
          onClose={() => setConfirm(null)}
        />
      )}
    </div>
  );
}