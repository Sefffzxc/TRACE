import { useCallback, useEffect, useState } from 'react';

/** Keep server cooldowns accurate even when a browser tab pauses its timers. */
export default function useRetryAfter() {
  const [until, setUntil] = useState(0);
  const [seconds, setSeconds] = useState(0);

  const start = useCallback((duration) => {
    if (!Number.isFinite(duration) || duration <= 0) return;
    setSeconds(Math.ceil(duration));
    setUntil(Date.now() + duration * 1000);
  }, []);

  useEffect(() => {
    if (!until) return undefined;
    const update = () => {
      const remaining = Math.max(0, Math.ceil((until - Date.now()) / 1000));
      setSeconds(remaining);
      if (remaining === 0) setUntil(0);
    };
    const timer = window.setInterval(update, 250);
    window.addEventListener('focus', update);
    document.addEventListener('visibilitychange', update);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', update);
      document.removeEventListener('visibilitychange', update);
    };
  }, [until]);

  return { seconds, start };
}
