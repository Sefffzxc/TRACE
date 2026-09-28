import { useNavigate } from 'react-router-dom';
import { Compass } from 'lucide-react';

export default function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="notfound">
      <span className="code">404</span>
      <h1>That page doesn&rsquo;t exist</h1>
      <p className="muted" style={{ maxWidth: '42ch' }}>
        The link may be out of date, or the record it pointed at has been removed.
      </p>
      <button
        type="button"
        className="btn btn-primary"
        style={{ marginTop: '.75rem' }}
        onClick={() => navigate('/dashboard')}
      >
        <Compass size={16} /> Back to the dashboard
      </button>
    </div>
  );
}
