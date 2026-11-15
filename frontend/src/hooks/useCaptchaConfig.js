import { useEffect, useState } from 'react';
import { authApi } from '../api/endpoints';

/** Read the public widget configuration from the API; failures never disable it. */
export default function useCaptchaConfig() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError('');
    authApi.captchaConfig().then((value) => {
      if (typeof value?.enabled !== 'boolean' || (value.enabled && !value.site_key)) {
        throw new Error('Security verification settings could not be loaded.');
      }
      if (live) setConfig(value);
    }).catch((failure) => {
      if (live) {
        setConfig(null);
        setError(failure.message || 'Security verification settings could not be loaded.');
      }
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [attempt]);
  return { config, loading, error, retry: () => setAttempt((current) => current + 1) };
}
