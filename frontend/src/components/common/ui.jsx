/**
 * Small presentational pieces shared by every screen.
 */
import { useEffect } from 'react';
import { AlertTriangle, Inbox, Loader2, X } from 'lucide-react';
import { assetUrl } from '../../api/client';

/* ── Risk badge ──────────────────────────────────────────────────────────── */
const RISK_LABEL = {
  critical: 'Critical',
  high: 'At risk',
  moderate: 'Watch',
  low: 'On track',
};

export function RiskBadge({ level = 'low', showScore, score }) {
  const key = RISK_LABEL[level] ? level : 'low';
  return (
    <span className={`badge badge-${key}`}>
      {RISK_LABEL[key]}
      {showScore && score !== undefined ? ` · ${Math.round(score)}` : ''}
    </span>
  );
}

/* ── Avatar ──────────────────────────────────────────────────────────────── */
export function Avatar({ name = '', src, size = '', className = '' }) {
  const initials =
    name
      .split(' ')
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join('')
      .toUpperCase() || '—';
  const url = assetUrl(src);
  return (
    <span className={`avatar ${size ? `avatar-${size}` : ''} ${className}`.trim()} title={name}>
      {url ? <img src={url} alt="" /> : initials}
    </span>
  );
}

/* ── Progress bar ────────────────────────────────────────────────────────── */
export function ProgressBar({ value = 0, level = 'low', showValue = true }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className="progress-row">
      <div
        className="progress"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`progress-fill is-${level}`} style={{ width: `${pct}%` }} />
      </div>
      {showValue && <span className="progress-value">{pct.toFixed(0)}%</span>}
    </div>
  );
}

/* ── Empty state ─────────────────────────────────────────────────────────── */
export function EmptyState({ icon: Icon = Inbox, title, description, action }) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon size={24} />
      </span>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}

/* ── Alerts & loaders ────────────────────────────────────────────────────── */
export function ErrorNote({ message }) {
  if (!message) return null;
  return (
    <div className="alert alert-error" role="alert">
      <AlertTriangle size={17} />
      <span>{message}</span>
    </div>
  );
}

export function Spinner({ label }) {
  return (
    <span className="row" style={{ color: 'var(--text-muted)' }}>
      <Loader2 size={16} className="spin" style={{ animation: 'spin .8s linear infinite' }} />
      {label && <span>{label}</span>}
    </span>
  );
}

export function TableSkeleton({ rows = 6, columns = 5 }) {
  return (
    <div style={{ padding: '1rem' }}>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div
          key={rowIndex}
          style={{ display: 'flex', gap: '1rem', marginBottom: '0.7rem' }}
          aria-hidden="true"
        >
          {Array.from({ length: columns }).map((__, colIndex) => (
            <div
              key={colIndex}
              className="skeleton"
              style={{ height: 14, flex: colIndex === 0 ? 2 : 1 }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/* ── Modal ───────────────────────────────────────────────────────────────── */
export function Modal({ title, subtitle, onClose, children, footer, size = '' }) {
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      <div className={`modal ${size ? `modal-${size}` : ''}`} role="dialog" aria-modal="true">
        <div className="modal-header">
          <div>
            <h2>{title}</h2>
            {subtitle && <div className="card-subtitle">{subtitle}</div>}
          </div>
          <button type="button" className="btn-icon" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

/* ── Confirm dialog ──────────────────────────────────────────────────────── */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  tone = 'danger',
  busy = false,
  onConfirm,
  onClose,
}) {
  return (
    <Modal
      title={title}
      onClose={onClose}
      size="sm"
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={`btn btn-${tone}`} onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <p>{message}</p>
    </Modal>
  );
}

/* ── Pagination ──────────────────────────────────────────────────────────── */
export function Pagination({ page, pageCount, total, pageSize, onChange }) {
  if (pageCount <= 1) {
    return (
      <div className="pagination">
        <span>
          {total} {total === 1 ? 'record' : 'records'}
        </span>
      </div>
    );
  }

  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const numbers = [];
  const window = 1;
  for (let index = 1; index <= pageCount; index += 1) {
    const near = Math.abs(index - page) <= window;
    if (index === 1 || index === pageCount || near) numbers.push(index);
    else if (numbers[numbers.length - 1] !== '…') numbers.push('…');
  }

  return (
    <div className="pagination">
      <span>
        Showing {from}–{to} of {total}
      </span>
      <div className="pages">
        <button
          type="button"
          className="page-btn"
          onClick={() => onChange(page - 1)}
          disabled={page === 1}
        >
          Prev
        </button>
        {numbers.map((item, index) =>
          item === '…' ? (
            <span key={`gap-${index}`} className="faint" style={{ padding: '0 .25rem' }}>
              …
            </span>
          ) : (
            <button
              key={item}
              type="button"
              className={`page-btn ${item === page ? 'is-active' : ''}`}
              onClick={() => onChange(item)}
            >
              {item}
            </button>
          ),
        )}
        <button
          type="button"
          className="page-btn"
          onClick={() => onChange(page + 1)}
          disabled={page === pageCount}
        >
          Next
        </button>
      </div>
    </div>
  );
}

/* ── Page header ─────────────────────────────────────────────────────────── */
export function PageHead({ title, subtitle, actions }) {
  return (
    <header className="page-head">
      <div className="page-title">
        <h1>{title}</h1>
        {subtitle && <span className="page-sub">{subtitle}</span>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
