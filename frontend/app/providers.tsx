"use client";

import { useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import { SessionProvider } from "@/lib/hooks/use-session";
import { ApiError } from "@/lib/api/client";

/**
 * Client-side providers, wrapped around the whole app.
 *
 * WHY useState AND NOT A MODULE-LEVEL QueryClient
 *
 * `const client = new QueryClient()` at module scope is created once
 * per PROCESS. On the server that process is shared by every visitor,
 * so one user's cached agent list could be served to another. Creating
 * it inside useState gives each browser session its own cache, and the
 * lazy initialiser means it is constructed once per mount rather than
 * on every render.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Data is considered fresh for 30s. Below that, remounting
            // a component reads the cache instead of refetching -
            // which is what stops a sidebar from hammering /api/agents
            // on every navigation.
            staleTime: 30_000,

            retry: (failureCount, error) => {
              // Never retry a request the server answered definitively.
              // A 401, 403, 404 or 422 will return exactly the same
              // answer three times; retrying only delays the error the
              // user needs to see.
              if (error instanceof ApiError && error.status < 500) {
                return false;
              }

              return failureCount < 2;
            },
          },

          mutations: {
            // Mutations are never retried automatically. A retried POST
            // can create two agents, or send a chat message twice.
            // Retrying is the user's decision, not the library's.
            retry: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider>{children}</SessionProvider>
    </QueryClientProvider>
  );
}
