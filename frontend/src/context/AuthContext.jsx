import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi, schoolApi, staffApi } from '../api/endpoints';
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
    window.addEventListener('trace:session-expired', onExpired);
    return () => window.removeEventListener('trace:session-expired', onExpired);
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

  /* ── The signed-in user's school ───────────────────────────────────────────
     Loaded once after sign-in and kept here, because almost every screen needs
     it: whether the lesson-progress pages exist at all, and what this school
     calls its units. The system operator has no school, which is the whole
     point — they manage schools and accounts and never see a student record,
     so there is nothing here for them to load. */
  const [school, setSchool] = useState(null);
  const isSuperAdmin = user?.role === 'super_admin';

  const loadSchool = useCallback(async () => {
    const fresh = await schoolApi.mine();
    setSchool(fresh);
    return fresh;
  }, []);

  useEffect(() => {
    if (!user || isSuperAdmin) {
      setSchool(null);
      return;
    }
    let cancelled = false;
    schoolApi
      .mine()
      .then((result) => {
        if (!cancelled) setSchool(result);
      })
      .catch(() => {
        /* The shell still works without it; screens fall back to neutral
           wording rather than blocking on a settings lookup. */
        if (!cancelled) setSchool(null);
      });
    return () => {
      cancelled = true;
    };
  }, [user, isSuperAdmin]);

  const value = useMemo(
    () => ({
      user,
      booting,
      isSuperAdmin,
      isAdmin: user?.role === 'admin',
      isTeacher: user?.role === 'teacher',

      school,
      loadSchool,
      /* Does this school track lesson units at all? Drives whether the PACE
         screens appear. Defaults to false, so a school whose settings have not
         loaded sees the smaller, always-valid version of the app rather than a
         screen it may have no use for. */
      tracksUnits: Boolean(school?.tracks_units),
      /* What this school calls them. "PACE" at a School of Tomorrow school,
         "Module" elsewhere — never hard-coded in a screen. */
      unitLabel: school?.unit_label_singular || 'Module',
      unitLabelPlural: school?.unit_label_plural || 'Modules',
      passingMark: school?.passing_mark ?? 75,

      signIn,
      signOut,
      refreshProfile,
    }),
    [user, booting, isSuperAdmin, school, loadSchool, signIn, signOut, refreshProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>.');
  return context;
}
