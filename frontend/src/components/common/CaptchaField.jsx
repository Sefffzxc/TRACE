import { useEffect, useRef, useState } from 'react';
import { useTheme } from '../../context/ThemeContext';
import { FieldError } from './ui';

const SCRIPT_ID = 'trace-turnstile-script';
const SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
let scriptPromise;

// A shared loader keeps route changes and React StrictMode from loading the
// external script twice. Failed loads are discarded so Retry can load it again.
function loadTurnstile() {
  if (scriptPromise) return scriptPromise;
  const pending = new Promise((resolve, reject) => {
    let script = document.getElementById(SCRIPT_ID);
    let finished = false;
    let timeout;
    const cleanup = () => {
      clearTimeout(timeout);
      script?.removeEventListener('load', ready);
      script?.removeEventListener('error', fail);
    };
    const fail = () => {
      if (finished) return;
      finished = true;
      cleanup();
      script?.remove();
      reject(new Error('Security verification could not load.'));
    };
    const ready = () => {
      if (finished) return;
      const api = window.turnstile;
      if (!api?.ready) {
        fail();
        return;
      }
      try {
        // Script load and API readiness are distinct when loading asynchronously.
        api.ready(() => {
          if (finished) return;
          if (typeof api.render !== 'function') {
            fail();
            return;
          }
          finished = true;
          cleanup();
          resolve(api);
        });
      } catch {
        fail();
      }
    };
    timeout = window.setTimeout(fail, 20000);
    if (window.turnstile?.ready) {
      ready();
      return;
    }
    if (!script) {
      script = document.createElement('script');
      script.id = SCRIPT_ID;
      script.src = SCRIPT_URL;
      script.async = true;
      script.defer = true;
      script.addEventListener('load', ready, { once: true });
      script.addEventListener('error', fail, { once: true });
      document.head.appendChild(script);
    } else {
      script.addEventListener('load', ready, { once: true });
      script.addEventListener('error', fail, { once: true });
    }
  });
  scriptPromise = pending;
  pending.catch(() => {
    if (scriptPromise === pending) scriptPromise = undefined;
  });
  return pending;
}

export default function CaptchaField({
  siteKey,
  action,
  token,
  onTokenChange,
  error,
  resetKey,
  id = 'captcha',
}) {
  const { isDark } = useTheme();
  const hostRef = useRef(null);
  const callbackRef = useRef(onTokenChange);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [widgetError, setWidgetError] = useState('');
  const [canRetry, setCanRetry] = useState(false);

  // An inline parent callback must not reset a solved challenge on every edit.
  callbackRef.current = onTokenChange;

  useEffect(() => {
    let cancelled = false;
    let api;
    let widgetId;
    const host = hostRef.current;
    const sizeForWidth = () => host?.clientWidth > 0 && host.clientWidth < 300 ? 'compact' : 'flexible';
    let size = sizeForWidth();
    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
      if (cancelled) return;
      const nextSize = sizeForWidth();
      if (nextSize !== size) {
        size = nextSize;
        setAttempt((value) => value + 1);
      }
    }) : null;
    if (host) resizeObserver?.observe(host);
    callbackRef.current?.('');
    setLoading(true);
    setWidgetError('');
    setCanRetry(false);

    const invalidate = (message) => {
      if (cancelled) return;
      callbackRef.current?.('');
      setLoading(false);
      setWidgetError(message);
      setCanRetry(true);
    };

    if (!siteKey) {
      invalidate('Security verification is unavailable. Please try again later.');
    } else {
      loadTurnstile()
        .then((turnstile) => {
          if (cancelled || !host) return;
          api = turnstile;
          widgetId = api.render(host, {
            sitekey: siteKey,
            action,
            theme: isDark ? 'dark' : 'light',
            // Flexible widgets have a 300px minimum; compact fits narrow phones.
            size,
            'response-field': false,
            retry: 'never',
            'refresh-expired': 'auto',
            'refresh-timeout': 'auto',
            callback: (value) => {
              if (cancelled) return;
              callbackRef.current?.(value);
              setLoading(false);
              setWidgetError('');
              setCanRetry(false);
            },
            'expired-callback': () => invalidate('Verification expired. Please complete it again.'),
            'timeout-callback': () => invalidate('Verification timed out. Please try again.'),
            'error-callback': () => {
              invalidate('Security verification failed. Please try again.');
              return true;
            },
            'unsupported-callback': () => invalidate(
              'Security verification is not supported in this browser. Please use an updated browser.',
            ),
          });
          if (widgetId === undefined || widgetId === null) {
            throw new Error('Security verification could not start.');
          }
          setLoading(false);
        })
        .catch(() => invalidate('Security verification could not load. Check your connection and try again.'));
    }

    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      if (api && widgetId !== undefined && widgetId !== null) {
        try {
          api.remove(widgetId);
        } catch {
          // A failed external widget may already have removed itself.
        }
      }
      host?.replaceChildren();
    };
  }, [siteKey, action, isDark, resetKey, attempt]);

  const message = widgetError || error;
  return (
    <div className="field captcha-field">
      <span className="field-label captcha-label" id={`${id}-label`}>Security verification</span>
      <FieldError id={id} message={message} />
      <div
        id={id}
        className={`captcha-control${message ? ' is-invalid' : ''}`}
        role="group"
        tabIndex={-1}
        aria-labelledby={`${id}-label`}
        aria-invalid={Boolean(message) || undefined}
        aria-describedby={message ? `${id}-error` : undefined}
      >
        <div ref={hostRef} className="captcha-widget" />
        {loading && <p className="field-hint" role="status">Loading security verification...</p>}
        {canRetry && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAttempt((value) => value + 1)}>
            Retry verification
          </button>
        )}
        {!loading && !canRetry && token && <span className="visually-hidden" role="status">Security verification completed.</span>}
      </div>
    </div>
  );
}
