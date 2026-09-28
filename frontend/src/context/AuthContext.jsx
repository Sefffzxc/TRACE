import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi, staffApi } from '../api/endpoints';
import { storedUser, tokens } from '../api/client';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => storedUser.get());
  const [booting, setBooting] = useState(() => Boolean(tokens.access));

  /* Re-validate the stored session on a cold start, so a token that expired
     while the tab was closed does not show a signed-in shell with no data. */
  useEffect(() => {
    let cancelled = false;
    if (!tokens.access) {
      setBooting(false);
      return undefined;
    }
    (async () => {
      try {
        const fresh = await authApi.me();
        if (!cancelled) {
          setUser(fresh);
          storedUser.set(fresh);
        }
      } catch {
        if (!cancelled) {
          tokens.clear();
          setUser(null);
        }
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /* The API client fires this when a refresh attempt fails. */
  useEffect(() => {
    const onExpired = () => {
      setUser(null);
      storedUser.set(null);
    };
    window.addEventListener('lbca:session-expired', onExpired);
    return () => window.removeEventListener('lbca:session-expired', onExpired);
  }, []);

  const signIn = useCallback((tokenResponse) => {
    tokens.save(tokenResponse.access_token, tokenResponse.refresh_token);
    const profile = tokenResponse.user;
    storedUser.set(profile);
    setUser(profile);
    return profile;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      /* Signing out locally matters more than the server acknowledging it. */
    }
    tokens.clear();
    setUser(null);
  }, []);

  const refreshProfile = useCallback(async () => {
    const fresh = await staffApi.me();
    setUser(fresh);
    storedUser.set(fresh);
    return fresh;
  }, []);

  const value = useMemo(
    () => ({
      user,
      booting,
      isAdmin: user?.role === 'admin',
      isTeacher: user?.role === 'teacher',
      signIn,
      signOut,
      refreshProfile,
    }),
    [user, booting, signIn, signOut, refreshProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>.');
  return context;
}
