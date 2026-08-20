import { z } from "zod";

/**
 * Runtime validation at the boundary.
 *
 * TypeScript types vanish at runtime - they cannot check what a user
 * typed into an input. Zod schemas are ordinary JavaScript objects that
 * exist while the app runs, so they can.
 *
 * The division of labour worth internalising:
 *
 *   TypeScript   catches mistakes YOU make, at build time
 *   Zod          catches data the OUTSIDE WORLD sends, at run time
 *
 * This is client-side validation for FAST FEEDBACK only. The backend
 * validates independently and is the real authority - anyone can POST
 * straight past this file with curl.
 */

export const loginSchema = z.object({
  email: z.email({ message: "Enter a valid email address." }),
  password: z.string().min(1, "Enter your password."),
});

export const registerSchema = z.object({
  email: z.email({ message: "Enter a valid email address." }),

  // Matches the backend's own minimum. If the two ever disagree the
  // user gets a server error on a form that said it was fine - the
  // most confusing kind of validation bug.
  password: z
    .string()
    .min(8, "Use at least 8 characters.")
    .max(128, "That is too long."),

  full_name: z
    .string()
    .max(255)
    .optional()
    .or(z.literal("")),
});

export type LoginValues = z.infer<typeof loginSchema>;
export type RegisterValues = z.infer<typeof registerSchema>;

/**
 * Turn a Zod failure into { field: message }.
 *
 * Only the FIRST error per field is kept. Showing a user three
 * complaints about one input at once is noise; fix one, see the next.
 */
export function fieldErrors(
  error: z.ZodError,
): Record<string, string> {
  const result: Record<string, string> = {};

  for (const issue of error.issues) {
    const key = String(issue.path[0] ?? "form");
    if (!result[key]) result[key] = issue.message;
  }

  return result;
}
