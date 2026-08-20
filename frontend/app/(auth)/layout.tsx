/**
 * Layout for the signed-out pages.
 *
 * The (auth) folder is a ROUTE GROUP. Parentheses mean "group these
 * routes but do not put the name in the URL", so this file styles
 * /login and /register without making them /auth/login.
 *
 * That is what lets the app have two completely different shells - a
 * centred card here, a sidebar in (app) - with no conditional logic
 * inside a single layout.
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <main className="flex min-h-svh items-center justify-center bg-muted/30 p-6">
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
