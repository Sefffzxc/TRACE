import { useEffect, useState } from 'react';
import { GraduationCap, Moon, ShieldCheck, Sun, TrendingUp, Users } from 'lucide-react';
import { IMAGES, SCHOOL } from '../../config/images';
import { useTheme } from '../../context/ThemeContext';

/**
 * The shared frame for every signed-out screen.
 *
 * The photograph is loaded through an `Image` object rather than straight into
 * CSS, so that if the file is unreachable the panel simply keeps its gradient
 * instead of showing a broken frame.
 */
export default function AuthLayout({ children }) {
  const { isDark, toggle } = useTheme();
  const [photo, setPhoto] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const candidates = [IMAGES.loginBackground, IMAGES.loginBackgroundAlt];

    const tryNext = (index) => {
      if (cancelled || index >= candidates.length) return;
      const image = new Image();
      image.src = candidates[index];
      image.onload = () => {
        if (!cancelled) setPhoto(candidates[index]);
      };
      image.onerror = () => tryNext(index + 1);
    };

    tryNext(0);
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="auth">
      <button
        type="button"
        className="auth-theme-toggle"
        onClick={toggle}
        aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
      >
        {isDark ? <Sun size={17} /> : <Moon size={17} />}
      </button>

      <aside className="auth-visual">
        {photo && (
          <div className="auth-photo" style={{ backgroundImage: `url(${photo})` }} aria-hidden="true" />
        )}

        <div className="auth-brand">
          <span className="brand-seal">
            <GraduationCap size={26} />
          </span>
          <span className="brand-text">
            <span className="brand-name">{SCHOOL.shortName}</span>
            <span className="brand-sub">Monitoring System</span>
          </span>
        </div>

        <div className="auth-pitch">
          <h1>
            Every learner&rsquo;s progress, <em>seen early enough to act.</em>
          </h1>
          <p>
            {SCHOOL.name} tracks PACE completion subject by subject and raises a flag the
            moment a student starts falling behind — while there is still time to help.
          </p>
          <div className="auth-highlights">
            <span>
              <TrendingUp size={14} /> Live PACE tracking
            </span>
            <span>
              <Users size={14} /> Section &amp; adviser views
            </span>
            <span>
              <ShieldCheck size={14} /> Two-factor sign-in
            </span>
          </div>
        </div>

        <div className="auth-foot">
          <span>{SCHOOL.address}</span>
          <span>&copy; {new Date().getFullYear()} {SCHOOL.shortName}</span>
        </div>
      </aside>

      <main className="auth-panel">
        <div className="auth-card">
          <div className="auth-mobile-brand">
            <span className="brand-seal">
              <GraduationCap size={22} />
            </span>
            <span>
              <div className="brand-name">{SCHOOL.shortName}</div>
              <div className="brand-sub">Monitoring System</div>
            </span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
