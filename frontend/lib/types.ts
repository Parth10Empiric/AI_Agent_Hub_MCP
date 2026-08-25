/**
 * The backend's types, as TypeScript.
 *
 * NOTHING in this file is hand-written. `schema.d.ts` is generated from
 * the live FastAPI OpenAPI document by `npm run gen:api`, and this file
 * only gives its deeply-nested names a readable alias.
 *
 * WHY GENERATE RATHER THAN TYPE BY HAND
 *
 * Two hand-maintained copies of the same shape always drift, and the
 * drift is silent: the backend renames `risk_level` to `risk`, the
 * frontend still compiles, and a badge quietly renders `undefined` in
 * production. Generated types turn that into a build error.
 *
 * The rule: if you find yourself writing `interface Agent { ... }`,
 * stop and regenerate instead.
 */

import type { components } from "./api/schema";

type Schemas = components["schemas"];

// --- Auth -----------------------------------------------------------

export type User = Schemas["UserRead"];
export type TokenPair = Schemas["TokenPair"];
export type AuthResponse = Schemas["AuthResponse"];
export type LoginRequest = Schemas["LoginRequest"];
export type RegisterRequest = Schemas["RegisterRequest"];

// --- Plugins --------------------------------------------------------

export type PluginSummary = Schemas["PluginSummary"];
export type PluginDetail = Schemas["PluginDetail"];
export type ConnectionRead = Schemas["ConnectionRead"];
export type ConnectionCheck = Schemas["ConnectionCheck"];
export type ConnectRequest = Schemas["ConnectRequest"];
export type ToolSummary = Schemas["ToolSummary"];

// --- Agents ---------------------------------------------------------

export type AgentSummary = Schemas["AgentSummary"];
export type AgentDetail = Schemas["AgentDetail"];
export type AgentCreate = Schemas["AgentCreate"];
export type AgentUpdate = Schemas["AgentUpdate"];

// --- Conversations --------------------------------------------------

export type ConversationRead = Schemas["ConversationRead"];
export type MessageRead = Schemas["MessageRead"];
export type MessagePage = Schemas["MessagePage"];

// --- Chat -----------------------------------------------------------

export type ChatResponse = Schemas["ChatResponse"];
export type ExecutionRead = Schemas["ExecutionRead"];
export type RoutingSummary = Schemas["RoutingSummary"];
export type ApprovalRequired = Schemas["ApprovalRequired"];

// --- Permissions (Phase 5.1) ----------------------------------------

export type ScopeRead = Schemas["ScopeRead"];
export type ScopeOption = Schemas["ScopeOption"];
export type AgentScopes = Schemas["AgentScopes"];
export type ScopeGrant = Schemas["ScopeGrant"];
export type PermissionAuditRead = Schemas["PermissionAuditRead"];
export type PermissionAuditPage = Schemas["PermissionAuditPage"];

// --- Approvals (Phase 5.2) ------------------------------------------

export type ApprovalRead = Schemas["ApprovalRead"];
export type ApprovalPage = Schemas["ApprovalPage"];

// --- Activity (Phase 5.8/5.9) ---------------------------------------

export type AuditEntryRead = Schemas["AuditEntryRead"];
export type AuditPage = Schemas["AuditPage"];

/** The four groups a person actually thinks in. */
export type AuditCategory =
  | "sign-in"
  | "permissions"
  | "connections"
  | "approvals"
  | "other";

// --- Rate limits (Phase 5.7) ----------------------------------------

export type LimitRead = Schemas["LimitRead"];
export type LimitsRead = Schemas["LimitsRead"];

// --- OAuth (Phase 5.3) ----------------------------------------------

export type OAuthStart = Schemas["OAuthStart"];

/**
 * The lifecycle of one approval, from api/approvals.py.
 *
 * `expired` is deliberately distinct from `denied`: one is a human
 * saying no, the other is a human never seeing the question, and the
 * UI should not tell someone they refused something they never saw.
 */
export type ApprovalStatus =
  | "pending"
  | "approved"
  | "denied"
  | "expired";

/** service:resource:action, e.g. "github:issue:write". */
export type ScopeAction = "read" | "write" | "admin";

/**
 * The vocabulary the UI groups and colours by.
 *
 * These are typed as plain `string` in the OpenAPI document because the
 * backend serialises the enum VALUE. Narrowing them here gives
 * autocomplete and stops a typo like "critcal" from compiling, while
 * the runtime data stays exactly what the API sent.
 *
 * The values mirror agent/schemas.py exactly. Operation has only four
 * members on purpose - ADMIN is separate from WRITE because granting
 * someone access to your Drive is not the same kind of act as creating
 * a file, and Phase 5 will treat them differently.
 */
export type Operation = "read" | "write" | "delete" | "admin";

export type RiskLevel = "safe" | "low" | "medium" | "high" | "critical";

export type ExecutionStatus = "success" | "failed" | "denied";
