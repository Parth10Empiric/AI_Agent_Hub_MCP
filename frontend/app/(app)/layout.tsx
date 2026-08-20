"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/hooks/use-session";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/plugins", label: "Plugins" },
  { href: "/agents", label: "Agents" },
  { href: "/executions", label: "Executions" },
];

/**
 * The signed-in shell: sidebar, header, and the auth gate.
 *
 * WHY THE GATE IS HERE AND NOT IN EVERY PAGE
 *
 * A layout wraps every route beneath it, so one check protects the
 * whole (app) group. Put the check in each page instead and protecting
 * a new page becomes something you must remember - and one day will
 * not.
 *
 * This is a CONVENIENCE gate, not a security boundary. It hides UI; it
 * does not protect data. The real enforcement is the backend returning
 * 401/404, because anyone can bypass client-side routing. Phase 3's
 * ownership checks are what actually keep user B out of user A's
 * agents.
 */
export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, isLoading, logout } = useSession();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    // Waiting for isLoading is what prevents a logged-in user being
    // bounced to /login on every refresh: on first paint `user` is
    // null simply because the refresh call has not returned yet.
    if (!isLoading && !user) {
      router.replace("/login");
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-svh items-center justify-center">
        <Skeleton className="h-32 w-64" />
      </div>
    );
  }

  // Render nothing during the redirect rather than flashing the shell.
  if (!user) return null;

  return (
    <div className="flex min-h-svh">
      <aside className="hidden w-56 shrink-0 border-r bg-muted/20 p-4 md:block">
        <Link href="/dashboard" className="mb-6 block text-lg font-semibold">
          Agent Hub
        </Link>

        <nav className="space-y-1">
          {NAV.map((item) => {
            // startsWith so /agents/<id>/chat still highlights "Agents".
            const active =
              pathname === item.href || pathname.startsWith(`${item.href}/`);

            return (
              <Link
                key={item.href}
                href={item.href}
                // aria-current is how a screen reader conveys "you are
                // here". Colour alone does not communicate it.
                aria-current={active ? "page" : undefined}
                className={cn(
                  "block rounded-md px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-background font-medium shadow-sm"
                    : "text-muted-foreground hover:bg-background/60",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b px-6">
          <span className="text-sm text-muted-foreground">
            {user.full_name || user.email}
          </span>

          <Button variant="ghost" size="sm" onClick={() => logout()}>
            Sign out
          </Button>
        </header>

        {/* min-w-0 on the flex parent and here: without it a wide child
            (a long tool argument, a code block) forces the whole shell
            to scroll horizontally instead of scrolling itself. */}
        <main className="min-w-0 flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
