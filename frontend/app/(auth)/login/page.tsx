"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { useSession } from "@/lib/hooks/use-session";
import { ApiError } from "@/lib/api/client";
import { fieldErrors, loginSchema } from "@/lib/validation";

export default function LoginPage() {
  const { login } = useSession();
  const router = useRouter();

  const [values, setValues] = useState({ email: "", password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    // Without this the browser does a full page POST and reload, which
    // would throw away the in-memory access token we are about to set.
    event.preventDefault();

    const parsed = loginSchema.safeParse(values);

    if (!parsed.success) {
      setErrors(fieldErrors(parsed.error));
      return;
    }

    setSubmitting(true);
    setErrors({});

    try {
      await login(parsed.data);
      router.replace("/dashboard");
    } catch (error) {
      // Deliberately vague, and deliberately the SAME message for a
      // wrong password and an unknown email. Distinguishing them tells
      // an attacker which addresses are registered.
      setErrors({
        form:
          error instanceof ApiError && error.status === 401
            ? "Email or password is incorrect."
            : "Could not sign in. Please try again.",
      });
    } finally {
      // In `finally` so the button re-enables even when the request
      // throws. Otherwise one failure leaves the form stuck forever.
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Welcome back</CardTitle>
        <CardDescription>Sign in to your Agent Hub account.</CardDescription>
      </CardHeader>

      <form onSubmit={handleSubmit} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              value={values.email}
              onChange={(e) =>
                setValues((v) => ({ ...v, email: e.target.value }))
              }
              aria-invalid={Boolean(errors.email)}
              aria-describedby={errors.email ? "email-error" : undefined}
            />
            {errors.email && (
              <p id="email-error" className="text-sm text-destructive">
                {errors.email}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={values.password}
              onChange={(e) =>
                setValues((v) => ({ ...v, password: e.target.value }))
              }
              aria-invalid={Boolean(errors.password)}
              aria-describedby={
                errors.password ? "password-error" : undefined
              }
            />
            {errors.password && (
              <p id="password-error" className="text-sm text-destructive">
                {errors.password}
              </p>
            )}
          </div>

          {errors.form && (
            // role="alert" makes a screen reader announce this the
            // moment it appears. A silently-rendered error is invisible
            // to anyone not looking at that part of the screen.
            <p role="alert" className="text-sm text-destructive">
              {errors.form}
            </p>
          )}
        </CardContent>

        <CardFooter className="flex-col gap-3 pt-2">
          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in"}
          </Button>

          <p className="text-sm text-muted-foreground">
            No account?{" "}
            <Link href="/register" className="underline underline-offset-4">
              Create one
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}
