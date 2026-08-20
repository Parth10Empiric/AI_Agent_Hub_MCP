"use client";

/**
 * Who is logged in, for the whole app.
 *
 * "use client" matters here. React Context lives in the browser's
 * memory, and so does our access token - neither exists in a Server
 * Component, which runs once on the server and streams HTML.
 *
 * The split to keep in mind for the rest of Phase 4:
 *
 *   Server Component   fetches data, renders HTML, no state, no events
 *   Client Component   state, effects, onClick - anything interactive
 *
 * Auth is interactive and memory-resident, so it is a client concern.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";

import * as authApi from "@/lib/api/auth";
import { onSessionEnded } from "@/lib/api/tokens";
import type { LoginRequest, RegisterRequest, User } from "@/lib/types";

interface SessionValue {
  user: User | null;
  /**
   * True only while the FIRST restore attempt is running.
   *
   * Without this the app cannot tell "not logged in" from "we do not
   * know yet", and it will flash the login page at an authenticated
   * user on every reload.
   */
  isLoading: boolean;
  login: (payload: LoginRequest) => Promise<void>;
  register: (payload: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  // On mount, try to trade the httpOnly refresh cookie for a new access
  // token. This is what makes a page reload survivable.
  useEffect(() => {
    let cancelled = false;

    authApi.restoreSession().then((restored) => {
      // The component may have unmounted while the request was in
      // flight. Setting state on an unmounted component is a leak.
      if (cancelled) return;

      setUser(restored);
      setIsLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  // The API client cannot navigate - it has no router. It announces
  // that the session died and this subscriber turns that into a
  // redirect. One place decides what "logged out" looks like.
  useEffect(() => {
    return onSessionEnded(() => {
      setUser(null);
      router.replace("/login");
    });
  }, [router]);

  const login = useCallback(async (payload: LoginRequest) => {
    const result = await authApi.login(payload);
    setUser(result.user);
  }, []);

  const register = useCallback(async (payload: RegisterRequest) => {
    const result = await authApi.register(payload);
    setUser(result.user);
  }, []);

  const logout = useCallback(async () => {
    // authApi.logout() no longer throws, but the local state change and
    // the redirect are kept AFTER it rather than inside it so that this
    // hook owns "what logged out looks like" in exactly one place.
    await authApi.logout();

    setUser(null);
    router.replace("/login");
  }, [router]);

  // useMemo so the context VALUE is not a brand-new object on every
  // render. Without it every consumer re-renders whenever this
  // provider does, which is the classic Context performance trap.
  const value = useMemo(
    () => ({ user, isLoading, login, register, logout }),
    [user, isLoading, login, register, logout],
  );

  return (
    <SessionContext.Provider value={value}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);

  // Throwing beats returning null. A missing provider is a wiring
  // mistake, and this turns it into one loud error at the right
  // component instead of "cannot read property user of null" three
  // files away.
  if (!value) {
    throw new Error("useSession must be used inside <SessionProvider>.");
  }

  return value;
}
