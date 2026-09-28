import { useCallback, useEffect, useMemo, useState } from 'react';
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

export default function TeachersPage() {
  const toast = useToast();

  const [staff, setStaff] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
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
    return staff.filter((person) => {
      if (roleFilter && person.role !== roleFilter) return false;
      if (!term) return true;
      return `${person.first_name} ${person.last_name} ${person.email}`.toLowerCase().includes(term);
    });
  }, [staff, search, roleFilter]);

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
      message: 'They will be signed out and unable to sign in again until reactivated.',
      label: 'Deactivate',
      tone: 'danger',
    },
    reactivate: {
      title: 'Reactivate this account?',
      message: 'They will be able to sign in again immediately.',
      label: 'Reactivate',
      tone: 'success',
    },
    unlock: {
      title: 'Unlock this account?',
      message: 'The failed sign-in counter is cleared and any lockout is lifted.',
      label: 'Unlock',
      tone: 'primary',
    },
    force_reset: {
      title: 'Issue a temporary password?',
      message:
        'Their current password stops working. A temporary one is generated and shown once — they must change it at next sign-in.',
      label: 'Issue password',
      tone: 'primary',
    },
  };

  return (
    <div className="page">
      <PageHead title="Staff directory" subtitle="Teachers and administrators with access to the system." />

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
        <select
          className="select"
          value={roleFilter}
          onChange={(event) => setRoleFilter(event.target.value)}
          aria-label="Filter by role"
        >
          <option value="">All roles</option>
          <option value="teacher">Teachers</option>
          <option value="admin">Administrators</option>
        </select>
        <span className="spacer" />
        <span className="result-count">
          {filtered.length} account{filtered.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="card">
        <div className="card-body flush">
          {loading ? (
            <TableSkeleton rows={6} columns={5} />
          ) : filtered.length === 0 ? (
            <EmptyState icon={UserCheck} title="No staff accounts match" />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Contact</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th className="actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((person) => (
                    <tr key={person.id}>
                      <td>
                        <div className="cell-name">
                          <Avatar
                            name={`${person.first_name} ${person.last_name}`}
                            src={person.profile_pic}
                            size="sm"
                          />
                          <div>
                            <div className="cell-strong">
                              {person.first_name} {person.last_name}
                            </div>
                            <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                              {person.department || person.employee_no || '—'}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="row" style={{ gap: '.35rem', fontSize: 'var(--text-xs)' }}>
                          <Mail size={12} className="faint" /> {person.email}
                        </div>
                        <div
                          className="row faint"
                          style={{ gap: '.35rem', fontSize: 'var(--text-2xs)' }}
                        >
                          <Phone size={11} /> {person.contact_number}
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${person.role === 'admin' ? 'badge-brand' : 'badge-neutral'}`}>
                          {person.role === 'admin' ? 'Administrator' : 'Teacher'}
                        </span>
                      </td>
                      <td>
                        <span className={`badge ${STATUS_BADGE[person.account_status] || 'badge-neutral'}`}>
                          {person.account_status}
                        </span>
                        {person.permanent_lock && (
                          <span className="badge badge-critical" style={{ marginLeft: 4 }}>
                            Locked
                          </span>
                        )}
                      </td>
                      <td className="actions">
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
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

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
