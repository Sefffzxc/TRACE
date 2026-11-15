import { useEffect, useState } from 'react';
import { History, RefreshCw } from 'lucide-react';
import { EmptyState, ErrorNote, Modal, TableSkeleton } from './ui';

const label = (value) => value ? value.replace(/_/g, ' ') : 'Not submitted';

/** Kept separate from the queue so a history failure never hides applications. */
export default function ApplicationHistoryModal({ name, loadHistory, onClose }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError('');
    loadHistory()
      .then((rows) => { if (current) setEntries(rows || []); })
      .catch((err) => { if (current) setError(err.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [loadHistory, attempt]);

  return (
    <Modal title="Application history" subtitle={<span style={{ overflowWrap: 'anywhere' }}>{name}</span>} onClose={onClose}>
      {loading ? <TableSkeleton rows={3} columns={2} /> : error ? (
        <>
          <ErrorNote message={error} />
          <button className="btn btn-secondary btn-sm" type="button" onClick={() => setAttempt((value) => value + 1)}>
            <RefreshCw size={14} /> Retry history
          </button>
        </>
      ) : entries.length === 0 ? (
        <EmptyState icon={History} title="No earlier decisions recorded" description="Future reviews and resubmissions will appear here." />
      ) : (
        <ol style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: '1rem' }}>
          {entries.map((entry) => (
            <li key={entry.id} style={{ borderBottom: '1px solid var(--border-subtle)', paddingBottom: '1rem', overflowWrap: 'anywhere' }}>
              <div style={{ fontWeight: 650, textTransform: 'capitalize' }}>{label(entry.action)}</div>
              <div className="muted" style={{ fontSize: 'var(--text-sm)', textTransform: 'capitalize' }}>
                {label(entry.from_status)} → {label(entry.to_status)}
              </div>
              {entry.reason && <p style={{ margin: '.5rem 0', whiteSpace: 'pre-wrap' }}>{entry.reason}</p>}
              {entry.detail && entry.detail !== entry.reason && <p className="muted" style={{ margin: '.5rem 0', whiteSpace: 'pre-wrap' }}>{entry.detail}</p>}
              <div className="faint" style={{ fontSize: 'var(--text-xs)' }}>
                {entry.actor_name || 'Applicant / system'} · {entry.created_at ? new Date(entry.created_at).toLocaleString() : 'Date unavailable'}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Modal>
  );
}
