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
import { fieldErrors, registerSchema } from "@/lib/validation";

export default function RegisterPage() {
  const { register } = useSession();
  const router = useRouter();

  const [values, setValues] = useState({
    email: "",
    password: "",
    full_name: "",
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();

    const parsed = registerSchema.safeParse(values);

    if (!parsed.success) {
      setErrors(fieldErrors(parsed.error));
      return;
    }

    setSubmitting(true);
    setErrors({});

    try {
      await register({
        email: parsed.data.email,
        password: parsed.data.password,
        // An empty input means "not provided", not "the empty string".
        // Sending "" would store a blank name the user never chose.
        full_name: parsed.data.full_name || null,
      });

      // Registering signs you in, so go straight to the product. Making
      // a new user log in again immediately is friction for nothing.
      router.replace("/dashboard");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setErrors({ email: "That email is already registered." });
      } else if (error instanceof ApiError && error.status === 422) {
        setErrors({ form: error.message });
      } else {
        setErrors({ form: "Could not create your account. Try again." });
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Create your account</CardTitle>
        <CardDescription>
          Connect a service, build an agent, start asking questions.
        </CardDescription>
      </CardHeader>

      <form onSubmit={handleSubmit} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="full_name">Name</Label>
            <Input
              id="full_name"
              autoComplete="name"
              value={values.full_name}
              onChange={(e) =>
                setValues((v) => ({ ...v, full_name: e.target.value }))
              }
            />
          </div>

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
              autoComplete="new-password"
              value={values.password}
              onChange={(e) =>
                setValues((v) => ({ ...v, password: e.target.value }))
              }
              aria-invalid={Boolean(errors.password)}
              aria-describedby={
                errors.password ? "password-error" : "password-hint"
              }
            />
            {errors.password ? (
              <p id="password-error" className="text-sm text-destructive">
                {errors.password}
              </p>
            ) : (
              <p id="password-hint" className="text-xs text-muted-foreground">
                At least 8 characters.
              </p>
            )}
          </div>

          {errors.form && (
            <p role="alert" className="text-sm text-destructive">
              {errors.form}
            </p>
          )}
        </CardContent>

        <CardFooter className="flex-col gap-3 pt-2">
          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? "Creating account..." : "Create account"}
          </Button>

          <p className="text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="underline underline-offset-4">
              Sign in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}
