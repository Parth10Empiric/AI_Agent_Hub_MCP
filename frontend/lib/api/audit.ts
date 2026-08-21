/**
 * The user's own activity trail (Phase 5.8, surfaced in 5.9).
 *
 * Everything Phase 5 built is invisible when it is working - a
 * permission that holds, a token that stays encrypted, a limit not yet
 * reached. This is the one place a user can see the record and answer
 * "did anything happen to my account that I did not do?" without
 * asking anyone to run a query.
 */

import { api } from "./client";
import type { AuditCategory, AuditPage } from "../types";

export function listActivity(
  options: { category?: AuditCategory; limit?: number; cursor?: string } = {},
): Promise<AuditPage> {
  const params = new URLSearchParams();

  if (options.category) params.set("category", options.category);
  if (options.limit) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);

  const query = params.toString();

  return api<AuditPage>(`/api/audit${query ? `?${query}` : ""}`);
}
