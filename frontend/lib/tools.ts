import type { Operation, RiskLevel } from "./types";

/**
 * How the UI talks about tools.
 *
 * NOTHING here decides what a tool IS. Every tool arrives from the API
 * already carrying `operation`, `risk_level`, `read_only` and
 * `requires_approval`, computed once by Phase 2's classifier. This file
 * only decides how those existing facts are worded and coloured.
 *
 * That separation is the point. Add a service to the MCP server and its
 * tools group and badge themselves correctly with no change here. If
 * you ever find yourself writing `if (toolName.startsWith("github_"))`,
 * the classification is being re-implemented in the wrong place.
 */

export const OPERATION_ORDER: Operation[] = [
  "read",
  "write",
  "delete",
  "admin",
];

export const OPERATION_LABEL: Record<Operation, string> = {
  read: "Read",
  write: "Write",
  delete: "Delete",
  admin: "Access control",
};

/**
 * Written for someone who does not know what an API is.
 *
 * "admin" is a developer's word. "Can change who is allowed to see your
 * data" is what actually happens, and it is the sentence that makes a
 * user pause before ticking the box.
 */
export const OPERATION_HELP: Record<Operation, string> = {
  read: "Only looks at your data. Cannot change anything.",
  write: "Creates or edits things in your account.",
  delete: "Removes things. Some deletions cannot be undone.",
  admin: "Can change who is allowed to see your data.",
};

export const RISK_ORDER: RiskLevel[] = [
  "safe",
  "low",
  "medium",
  "high",
  "critical",
];

export const RISK_LABEL: Record<RiskLevel, string> = {
  safe: "Safe",
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
  critical: "Critical",
};

/**
 * Tailwind classes per risk level.
 *
 * Every entry sets a text colour, a background AND a border. Colour
 * alone is not an accessible signal - roughly one in twelve men cannot
 * reliably separate red from green - so risk is always accompanied by
 * the written label above, never conveyed by the colour by itself.
 */
export const RISK_CLASS: Record<RiskLevel, string> = {
  safe: "text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-950 dark:border-emerald-900",
  low: "text-sky-700 bg-sky-50 border-sky-200 dark:text-sky-300 dark:bg-sky-950 dark:border-sky-900",
  medium:
    "text-amber-700 bg-amber-50 border-amber-200 dark:text-amber-300 dark:bg-amber-950 dark:border-amber-900",
  high: "text-orange-700 bg-orange-50 border-orange-200 dark:text-orange-300 dark:bg-orange-950 dark:border-orange-900",
  critical:
    "text-red-700 bg-red-50 border-red-300 dark:text-red-300 dark:bg-red-950 dark:border-red-900",
};

export function isRiskLevel(value: string | null): value is RiskLevel {
  return RISK_ORDER.includes(value as RiskLevel);
}

export function isOperation(value: string | null): value is Operation {
  return OPERATION_ORDER.includes(value as Operation);
}

/**
 * Normalise whatever the API sent into something safe to index with.
 *
 * The API types these as plain `string`. If a future backend adds a
 * fifth operation, this returns "read"-safe defaults instead of
 * crashing on an undefined lookup - the UI degrades, it does not break.
 */
export function safeOperation(value: string | null | undefined): Operation {
  return isOperation(value ?? null) ? (value as Operation) : "read";
}

export function safeRisk(value: string | null | undefined): RiskLevel {
  return isRiskLevel(value ?? null) ? (value as RiskLevel) : "safe";
}

/**
 * Turn "github_create_issue" into "Create issue".
 *
 * The namespace prefix is dropped because it is already shown by the
 * group heading the tool sits under - repeating it in every row is
 * noise the user has to read past.
 */
export function prettyToolName(name: string, namespace?: string | null): string {
  let base = name;

  if (namespace && base.startsWith(`${namespace}_`)) {
    base = base.slice(namespace.length + 1);
  }

  base = base.replace(/_/g, " ").trim();

  return base.charAt(0).toUpperCase() + base.slice(1);
}

export const NAMESPACE_LABEL: Record<string, string> = {
  github: "GitHub",
  google_drive: "Google Drive",
  google_calendar: "Google Calendar",
  slack: "Slack",
};

export function prettyNamespace(key: string | null | undefined): string {
  if (!key) return "Unknown";

  return (
    NAMESPACE_LABEL[key] ??
    key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/**
 * Group any list of tool-ish objects by operation, in a fixed order.
 *
 * Fixed order matters: reads always first, destructive things always
 * last. A user scanning two different plugin pages should not have to
 * re-learn the layout each time.
 */
export function groupByOperation<T extends { operation?: string | null }>(
  items: T[],
): { operation: Operation; items: T[] }[] {
  const buckets = new Map<Operation, T[]>();

  for (const item of items) {
    const key = safeOperation(item.operation);
    const bucket = buckets.get(key);

    if (bucket) bucket.push(item);
    else buckets.set(key, [item]);
  }

  return OPERATION_ORDER.filter((op) => buckets.has(op)).map((op) => ({
    operation: op,
    items: buckets.get(op) ?? [],
  }));
}

/** The highest risk present, used to badge a whole group at once. */
export function highestRisk<T extends { risk_level?: string | null }>(
  items: T[],
): RiskLevel {
  return items.reduce<RiskLevel>((worst, item) => {
    const risk = safeRisk(item.risk_level);

    return RISK_ORDER.indexOf(risk) > RISK_ORDER.indexOf(worst) ? risk : worst;
  }, "safe");
}

export function formatDuration(ms: number): string {
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;

  return `${(ms / 1000).toFixed(1)}s`;
}

export function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);

  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;

  return new Date(iso).toLocaleDateString();
}
