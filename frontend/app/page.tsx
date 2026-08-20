"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useSession } from "@/lib/hooks/use-session";

/**
 * The front door.
 *
 * Its only job is to decide where "/" means. It cannot decide until the
 * session restore finishes, so it renders nothing while isLoading is
 * true rather than guessing and redirecting twice.
 */
export default function RootPage() {
  const { user, isLoading } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;

    // replace, not push: the user should not be able to press Back
    // into a redirect page that immediately redirects them again.
    router.replace(user ? "/dashboard" : "/login");
  }, [user, isLoading, router]);

  return null;
}
