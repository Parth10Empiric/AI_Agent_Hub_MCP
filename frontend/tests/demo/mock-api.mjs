/**
 * A stand-in for the FastAPI backend, used ONLY to capture the
 * screenshots and the demo video in docs/media.
 *
 * WHY THIS EXISTS RATHER THAN "JUST RUN THE REAL THING"
 *
 * The real stack is Postgres + Redis + FastAPI + the MCP server + a
 * live model, and a chat turn needs real GitHub credentials. That is
 * the right thing to run when you are developing. It is the wrong
 * thing to depend on when the only goal is a picture of the UI:
 *
 *   - a screenshot of a real turn contains real repository names,
 *     real file paths and a real account label
 *   - the same command would produce a different image every run,
 *     because the model writes different prose each time
 *   - anyone re-generating the images a year from now would need
 *     working credentials for four third-party services
 *
 * So the pixels are real - this is the actual production bundle, the
 * actual React components, the actual CSS - and only the JSON behind
 * them is fixed. Change a component and the images change with it.
 *
 * WHY IT IS A REAL HTTP SERVER AND NOT `page.route()` MOCKING
 *
 * Playwright's request interception fulfils a route with ONE complete
 * body. The interesting half of this product is a Server-Sent Events
 * stream that arrives in pieces over several seconds and then STOPS,
 * mid-turn, waiting for a human to approve a tool call. Delivered all
 * at once, the tool timeline never animates and the approval prompt
 * never has anything to wait for - the exact two things the media is
 * meant to show.
 *
 * A real socket can hold the response open. So this speaks real SSE,
 * with real delays, and genuinely blocks until POST
 * /api/approvals/{id}/{approve,deny} arrives - the same way the real
 * executor parks on an asyncio.Event.
 *
 * Run standalone:
 *   node tests/demo/mock-api.mjs --port 8099
 */

import { createServer } from "node:http";

const PORT = Number(
  process.argv.includes("--port")
    ? process.argv[process.argv.indexOf("--port") + 1]
    : process.env.MOCK_API_PORT ?? 8099,
);

// --- Fixtures -------------------------------------------------------
//
// Dates are FIXED, not `new Date()`. A relative timestamp ("2 minutes
// ago") re-renders differently on every capture run, which turns every
// regenerated screenshot into a diff even when nothing changed.

const NOW = "2026-08-27T09:41:00Z";

const USER = {
  id: "usr_7c1f0a94",
  email: "demo@example.com",
  full_name: "Demo User",
  is_active: true,
  email_verified_at: NOW,
  created_at: "2026-08-01T10:00:00Z",
};

const TOKENS = {
  access_token: "demo.access.token",
  refresh_token: "demo.refresh.token",
  token_type: "bearer",
  expires_in: 900,
};

/**
 * Tool counts are the REAL ones, counted from each service's tools.py:
 * 65 + 25 + 43 + 28 = 161. Inventing rounder numbers here would put a
 * figure on the README that no part of the code agrees with.
 */
const PLUGINS = [
  {
    key: "github",
    label: "GitHub",
    description:
      "Repositories, issues, pull requests, commits and file contents.",
    icon: "github",
    category: "development",
    auth_type: "personal_access_token",
    docs_url: "https://github.com/settings/tokens",
    tool_count: 65,
    operations: { admin: 4, delete: 5, read: 38, write: 18 },
    connected: true,
    account_label: "demo-user",
    status: "connected",
    oauth_available: false,
    oauth_scopes: [],
  },
  {
    key: "google_drive",
    label: "Google Drive",
    description: "Search, read, create and share files and folders.",
    icon: "drive",
    category: "storage",
    auth_type: "oauth2",
    docs_url: "https://console.cloud.google.com/apis/credentials",
    tool_count: 25,
    operations: { admin: 2, delete: 3, read: 13, write: 7 },
    connected: true,
    account_label: "demo@example.com",
    status: "connected",
    oauth_available: true,
    oauth_scopes: ["https://www.googleapis.com/auth/drive"],
  },
  {
    key: "slack",
    label: "Slack",
    description: "Channels, messages, threads, users and files.",
    icon: "slack",
    category: "communication",
    auth_type: "bot_token",
    docs_url: "https://api.slack.com/apps",
    tool_count: 43,
    operations: { admin: 3, delete: 4, read: 24, write: 12 },
    connected: true,
    account_label: "Acme Workspace",
    status: "connected",
    oauth_available: false,
    oauth_scopes: [],
  },
  {
    key: "google_calendar",
    label: "Google Calendar",
    description: "Events, availability and calendar management.",
    icon: "calendar",
    category: "productivity",
    auth_type: "oauth2",
    docs_url: "https://console.cloud.google.com/apis/credentials",
    tool_count: 28,
    operations: { admin: 1, delete: 4, read: 15, write: 8 },
    connected: false,
    account_label: null,
    status: null,
    oauth_available: true,
    oauth_scopes: ["https://www.googleapis.com/auth/calendar"],
  },
];

const CONNECTIONS = PLUGINS.filter((p) => p.connected).map((p, i) => ({
  id: `con_${i + 1}`,
  plugin_key: p.key,
  status: "connected",
  account_label: p.account_label,
  scopes: [`${p.key}:*:read`, `${p.key}:*:write`],
  connected_at: "2026-08-20T11:02:00Z",
  last_used_at: NOW,
  expires_at: null,
  created_at: "2026-08-20T11:02:00Z",
}));

const AGENT_ID = "agt_3f9b2c71";

const AGENT = {
  id: AGENT_ID,
  name: "Release Assistant",
  description:
    "Reads the repository, drafts release notes and files issues - " +
    "with a human on every write.",
  avatar_url: null,
  model: "gpt-oss:120b-cloud",
  temperature: 0.2,
  is_archived: false,
  created_at: "2026-08-22T08:15:00Z",
  updated_at: "2026-08-26T16:40:00Z",
  tool_count: 9,
  namespaces: ["github", "slack"],
  services: ["github", "slack"],
  system_prompt:
    "You are a release assistant. Prefer reading before writing, and " +
    "always summarise what you changed.",
};

const AGENTS = [
  AGENT,
  {
    ...AGENT,
    id: "agt_5d2a8e40",
    name: "Inbox Triage",
    description: "Summarises unread Slack threads each morning.",
    tool_count: 6,
    namespaces: ["slack"],
    services: ["slack"],
    created_at: "2026-08-18T09:00:00Z",
    updated_at: "2026-08-27T07:05:00Z",
  },
  {
    ...AGENT,
    id: "agt_9a7c1b33",
    name: "Docs Librarian",
    description: "Finds and files documents in Drive.",
    tool_count: 11,
    namespaces: ["google_drive"],
    services: ["google_drive"],
    created_at: "2026-08-14T13:20:00Z",
    updated_at: "2026-08-25T18:12:00Z",
  },
];

const CONVERSATION_ID = "cnv_a1b2c3d4";

const CONVERSATIONS = [
  {
    id: CONVERSATION_ID,
    agent_id: AGENT_ID,
    title: "Release notes for v2",
    is_archived: false,
    created_at: "2026-08-27T09:38:00Z",
    updated_at: NOW,
    last_message_at: NOW,
    message_count: 2,
  },
  {
    id: "cnv_9988aabb",
    agent_id: AGENT_ID,
    title: "Which PRs are still open?",
    is_archived: false,
    created_at: "2026-08-26T15:02:00Z",
    updated_at: "2026-08-26T15:09:00Z",
    last_message_at: "2026-08-26T15:09:00Z",
    message_count: 4,
  },
];

/** One finished tool call, as the executor records it. */
function execution(over) {
  return {
    id: "exe_" + Math.random().toString(36).slice(2, 10),
    tool_name: "github_search_issues",
    namespace: "github",
    operation: "read",
    risk_level: "safe",
    status: "success",
    started_at: NOW,
    duration_ms: 312,
    attempts: 1,
    approved_by_user: null,
    error: null,
    ...over,
  };
}

const EXECUTIONS = [
  execution({
    id: "exe_write_denied",
    tool_name: "github_create_issue",
    operation: "write",
    risk_level: "high",
    status: "denied",
    duration_ms: 0,
    approved_by_user: false,
    error: {
      type: "approval_denied",
      message: "The user denied this tool call.",
      recovery_hint:
        "Nothing was changed. Ask again if you still want this done.",
    },
    agent_id: AGENT_ID,
    agent_name: AGENT.name,
    conversation_id: CONVERSATION_ID,
  }),
  execution({
    id: "exe_read_file",
    tool_name: "github_get_file_contents",
    duration_ms: 486,
    agent_id: AGENT_ID,
    agent_name: AGENT.name,
    conversation_id: CONVERSATION_ID,
  }),
  execution({
    id: "exe_search",
    duration_ms: 312,
    agent_id: AGENT_ID,
    agent_name: AGENT.name,
    conversation_id: CONVERSATION_ID,
  }),
  execution({
    id: "exe_slack_post",
    tool_name: "slack_post_message",
    namespace: "slack",
    operation: "write",
    risk_level: "medium",
    duration_ms: 741,
    approved_by_user: true,
    agent_id: "agt_5d2a8e40",
    agent_name: "Inbox Triage",
    conversation_id: "cnv_9988aabb",
  }),
  execution({
    id: "exe_drive_fail",
    tool_name: "drive_get_file",
    namespace: "google_drive",
    status: "failed",
    duration_ms: 2104,
    attempts: 3,
    error: {
      type: "upstream_unavailable",
      message: "Google Drive did not respond.",
      recovery_hint: "The service was unreachable. Try again shortly.",
    },
    agent_id: "agt_9a7c1b33",
    agent_name: "Docs Librarian",
    conversation_id: "cnv_77665544",
  }),
];

const SCOPES = {
  granted: [
    { scope: "github:*:read", granted_at: "2026-08-22T08:16:00Z", granted_by: USER.id },
    { scope: "github:*:write", granted_at: "2026-08-26T16:40:00Z", granted_by: USER.id },
    { scope: "slack:*:read", granted_at: "2026-08-22T08:16:00Z", granted_by: USER.id },
  ],
  available: [
    scope("github", "*", "read", 38, true),
    scope("github", "*", "write", 18, true),
    scope("github", "*", "admin", 4, false),
    scope("slack", "*", "read", 24, true),
    scope("slack", "*", "write", 12, false),
    scope("slack", "*", "admin", 3, false),
  ],
};

function scope(service, resource, action, tool_count, granted) {
  return {
    scope: `${service}:${resource}:${action}`,
    service,
    resource,
    action,
    tool_count,
    granted,
    connected: true,
    on_agent: true,
  };
}

const AUDIT = [
  audit("approval.denied", "Denied github_create_issue", "approvals", NOW),
  audit("scope.granted", "Granted github:*:write", "permissions", "2026-08-26T16:40:00Z"),
  audit("connection.created", "Connected GitHub", "connections", "2026-08-20T11:02:00Z"),
  audit("auth.login", "Signed in", "sign-in", "2026-08-27T09:30:00Z"),
  audit("scope.granted", "Granted slack:*:read", "permissions", "2026-08-22T08:16:00Z"),
];

function audit(action, label, category, occurred_at) {
  return {
    id: "aud_" + Math.random().toString(36).slice(2, 10),
    action,
    label,
    category,
    resource_type: "agent",
    resource_id: AGENT_ID,
    actor_ip: "127.0.0.1",
    request_id: "req_" + Math.random().toString(36).slice(2, 10),
    occurred_at,
    metadata: {},
  };
}

const LIMITS = {
  enabled: true,
  limits: [
    {
      key: "agent_turns",
      label: "Agent turns",
      description: "Messages you can send to an agent.",
      used: 18,
      limit: 100,
      remaining: 82,
      resets_in: 2040,
      window_label: "per hour",
    },
    {
      key: "tool_calls",
      label: "Tool calls",
      description: "Actions your agents take on your accounts.",
      used: 64,
      limit: 500,
      remaining: 436,
      resets_in: 2040,
      window_label: "per hour",
    },
  ],
};

const MESSAGES = [
  {
    id: "msg_1",
    conversation_id: CONVERSATION_ID,
    role: "user",
    content: "Which issues are open on octocat/Hello-World?",
    tool_name: null,
    routing: null,
    token_usage: null,
    created_at: "2026-08-27T09:38:00Z",
    executions: [],
  },
  {
    id: "msg_2",
    conversation_id: CONVERSATION_ID,
    role: "assistant",
    content:
      "There are **3 open issues** on `octocat/Hello-World`:\n\n" +
      "1. **#42** — Docs are out of date for the v2 client\n" +
      "2. **#37** — Rate limiting returns 500 instead of 429\n" +
      "3. **#31** — Add a changelog\n\n" +
      "Nothing was changed — I only read the repository.",
    tool_name: null,
    routing: null,
    token_usage: { total: 1840 },
    created_at: "2026-08-27T09:38:20Z",
    executions: [execution({ id: "exe_hist_1", duration_ms: 298 })],
  },
];

/**
 * The conversation as the DATABASE would hold it.
 *
 * A finished turn is appended here, because the real backend persists
 * one and the UI leans on that: when the stream closes, useChat throws
 * away everything it assembled live and re-reads the conversation from
 * the server (that is what makes a mid-conversation page refresh
 * safe). A mock that never grows would therefore make the whole turn -
 * the tool timeline, the answer, the refusal notice - vanish the
 * instant it completed, and the screenshots taken after it would be of
 * an empty chat.
 */
const history = [...MESSAGES];

// --- The approval a live turn parks on ------------------------------
//
// The turn genuinely blocks here. `resolve` is the function the SSE
// handler is awaiting; POST /api/approvals/{id}/{approve,deny} calls
// it. That is the same shape as the real executor waiting on an
// asyncio.Event, and it is what makes the recorded video honest: the
// stream really is stopped while the prompt is on screen.
const pending = new Map();

function json(res, status, body) {
  const payload = JSON.stringify(body);

  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
    // The refresh cookie the real backend sets. Without it a page
    // reload cannot restore the session and every capture would land
    // back on /login.
    "Set-Cookie": "refresh_token=demo; Path=/; HttpOnly; SameSite=Lax",
  });

  res.end(payload);
}

async function readBody(req) {
  const chunks = [];

  for await (const chunk of req) chunks.push(chunk);

  if (chunks.length === 0) return {};

  try {
    return JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    return {};
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * How many turns this process has streamed.
 *
 * Execution ids must be unique across the whole conversation, not just
 * within one turn: both turns end up in `history`, React keys the tool
 * rows by execution id, and two rows sharing a key is a warning at
 * best and a dropped row at worst.
 */
let turnNumber = 0;

/**
 * The scripted turn.
 *
 * Timings are chosen so the timeline reads as work happening rather
 * than a slideshow: long enough to see each row land, short enough
 * that a capture run is not mostly waiting.
 */
async function streamTurn(res, question) {
  const turn = ++turnNumber;

  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
  });

  const send = (event, data) => {
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };

  await sleep(500);
  send("routing", { services: ["github"], tools: 9, confidence: 0.94 });

  await sleep(700);
  send("round_start", { round: 1 });
  send("tokens", { turn_total: 940 });

  await sleep(500);
  send("tool_start", { tool: "github_search_issues" });
  await sleep(1400);
  send("tool_end", execution({ id: `exe_t${turn}_search`, duration_ms: 312 }));

  await sleep(900);
  send("round_start", { round: 2 });
  send("tokens", { turn_total: 2380 });

  await sleep(400);
  send("tool_start", { tool: "github_get_file_contents" });
  await sleep(1600);
  send(
    "tool_end",
    execution({
      id: `exe_t${turn}_file`,
      tool_name: "github_get_file_contents",
      duration_ms: 486,
    }),
  );

  await sleep(1000);
  send("round_start", { round: 3 });
  send("tokens", { turn_total: 3910 });

  // --- the turn stops here ------------------------------------------
  const approvalId = "apr_" + Math.random().toString(36).slice(2, 10);

  await sleep(600);
  send("approval_required", {
    approval_id: approvalId,
    tool: "github_create_issue",
    operation: "write",
    risk_level: "high",
    arguments: {
      owner: "octocat",
      repo: "Hello-World",
      title: "Release notes for v2.0.0",
      body: "Summary of the changes shipped in v2.0.0.",
      labels: ["release", "documentation"],
    },
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    timeout_seconds: 300,
  });

  const status = await new Promise((resolve) => {
    pending.set(approvalId, resolve);

    // The real backend expires an unanswered approval rather than
    // holding a connection open forever. Same rule here, five minutes.
    setTimeout(() => {
      if (pending.delete(approvalId)) resolve("expired");
    }, 300_000);
  });

  send("approval_resolved", { approval_id: approvalId, status });

  await sleep(800);

  const approved = status === "approved";

  if (approved) {
    send("tool_start", { tool: "github_create_issue" });
    await sleep(1500);
    send(
      "tool_end",
      execution({
        id: `exe_t${turn}_write`,
        tool_name: "github_create_issue",
        operation: "write",
        risk_level: "high",
        duration_ms: 903,
        approved_by_user: true,
      }),
    );
    await sleep(800);
  }

  const answer = approved
    ? "Done. I opened **issue #58** on `octocat/Hello-World` — " +
      "*Release notes for v2.0.0* — with the `release` and " +
      "`documentation` labels.\n\nBefore writing it I read the open " +
      "issues and `CHANGELOG.md`, so the notes cover everything " +
      "merged since v1.9.\n\nThat was the only change I made."
    : "Understood — I did **not** create the issue, and nothing was " +
      "changed on your GitHub account.\n\nHere is the draft I had " +
      "ready, so you can file it yourself if you'd rather:\n\n" +
      "> **Release notes for v2.0.0**\n> Summary of the changes " +
      "shipped in v2.0.0.\n\nI read the open issues and " +
      "`CHANGELOG.md` to write it. Both of those are reads, so they " +
      "went ahead without asking.";

  send("answer_ready", { answer });

  await sleep(600);
  send("done", {
    answer,
    approvals_required: approved
      ? []
      : [
          {
            tool_name: "github_create_issue",
            operation: "write",
            risk_level: "high",
            argument_keys: ["owner", "repo", "title", "body", "labels"],
            status: "denied",
          },
        ],
  });

  // Persist the turn, exactly as the backend would before the stream
  // closes. The browser re-reads the conversation the moment this
  // socket ends, so anything not written here disappears from the
  // screen a second later.
  history.push(
    {
      id: `msg_t${turn}_user`,
      conversation_id: CONVERSATION_ID,
      role: "user",
      content: question,
      tool_name: null,
      routing: null,
      token_usage: null,
      created_at: new Date().toISOString(),
      executions: [],
    },
    {
      id: `msg_t${turn}_assistant`,
      conversation_id: CONVERSATION_ID,
      role: "assistant",
      content: answer,
      tool_name: null,
      routing: null,
      token_usage: { total: 4820 },
      created_at: new Date().toISOString(),

      // The refusal lives HERE, on an execution, not in a separate
      // field. approval-notice.tsx renders "needed permission for..."
      // from stored executions with status `denied`, so a denied turn
      // that persists no denied execution reloads as though the agent
      // had simply chosen not to write anything.
      executions: [
        execution({ id: `exe_t${turn}_search`, duration_ms: 312 }),
        execution({
          id: `exe_t${turn}_file`,
          tool_name: "github_get_file_contents",
          duration_ms: 486,
        }),
        approved
          ? execution({
              id: `exe_t${turn}_write`,
              tool_name: "github_create_issue",
              operation: "write",
              risk_level: "high",
              duration_ms: 903,
              approved_by_user: true,
            })
          : execution({
              id: `exe_t${turn}_write`,
              tool_name: "github_create_issue",
              operation: "write",
              risk_level: "high",
              status: "denied",
              duration_ms: 0,
              approved_by_user: false,
              error: {
                type: "approval_denied",
                message: "The user denied this tool call.",
                recovery_hint:
                  "Nothing was changed. Ask again if you still want " +
                  "this done.",
              },
            }),
      ],
    },
  );

  res.end();
}

// --- Routing --------------------------------------------------------

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  const path = url.pathname;
  const method = req.method ?? "GET";

  // Streaming first: it is the one route that must not be answered
  // with a buffered JSON body.
  if (method === "POST" && /\/messages\/stream$/.test(path)) {
    const body = await readBody(req);
    return streamTurn(res, String(body.content ?? ""));
  }

  const approve = path.match(/^\/api\/approvals\/([^/]+)\/(approve|deny)$/);

  if (method === "POST" && approve) {
    const [, id, verb] = approve;
    const status = verb === "approve" ? "approved" : "denied";
    const resolve = pending.get(id);

    if (resolve) {
      pending.delete(id);
      resolve(status);
    }

    return json(res, 200, { id, status });
  }

  await readBody(req);

  const routes = {
    "POST /api/auth/login": () => ({ ...TOKENS, user: USER }),
    "POST /api/auth/register": () => ({ ...TOKENS, user: USER }),
    "POST /api/auth/refresh": () => TOKENS,
    "POST /api/auth/logout": () => ({}),
    "GET /api/auth/me": () => USER,
    "GET /api/plugins": () => PLUGINS,
    "GET /api/plugins/connections": () => CONNECTIONS,
    "GET /api/agents": () => AGENTS,
    "GET /api/limits": () => LIMITS,
    "GET /api/audit": () => ({ items: AUDIT, next_cursor: null, has_more: false }),
    "GET /api/approvals": () => ({ items: [], next_cursor: null, has_more: false }),
    "GET /api/executions": () => ({
      items: EXECUTIONS,
      next_cursor: null,
      has_more: false,
    }),
    "GET /api/executions/stats": () => ({
      window_days: 7,
      total: 128,
      success: 119,
      failed: 6,
      denied: 3,
      avg_duration_ms: 421,
      by_tool: {
        github_search_issues: 41,
        github_get_file_contents: 33,
        slack_post_message: 19,
        drive_search_files: 14,
      },
    }),
  };

  const handler = routes[`${method} ${path}`];

  if (handler) return json(res, 200, handler());

  // Path-parameterised routes.
  if (method === "GET") {
    if (/^\/api\/agents\/[^/]+$/.test(path)) return json(res, 200, AGENT);
    if (/^\/api\/agents\/[^/]+\/scopes$/.test(path)) return json(res, 200, SCOPES);
    if (/^\/api\/agents\/[^/]+\/services$/.test(path)) return json(res, 200, PLUGINS);
    if (/^\/api\/agents\/[^/]+\/tools$/.test(path)) return json(res, 200, []);

    if (/^\/api\/agents\/[^/]+\/conversations$/.test(path)) {
      return json(res, 200, CONVERSATIONS);
    }

    if (/^\/api\/agents\/[^/]+\/audit$/.test(path)) {
      return json(res, 200, {
        items: [
          {
            id: "pau_1",
            action: "scope.granted",
            actor_user_id: USER.id,
            created_at: "2026-08-26T16:40:00Z",
            scope: "github:*:write",
            tool_name: null,
            ip_address: "127.0.0.1",
            request_id: "req_9f2a",
          },
          {
            id: "pau_2",
            action: "approval.denied",
            actor_user_id: USER.id,
            created_at: NOW,
            scope: null,
            tool_name: "github_create_issue",
            ip_address: "127.0.0.1",
            request_id: "req_c41b",
          },
        ],
        next_cursor: null,
        has_more: false,
      });
    }

    if (/^\/api\/conversations\/[^/]+\/messages$/.test(path)) {
      // Newest first, which is what makes the client's cursor paging
      // work when it scrolls UP.
      return json(res, 200, {
        items: [...history].reverse(),
        next_cursor: null,
        has_more: false,
      });
    }

    if (/^\/api\/conversations\/[^/]+$/.test(path)) {
      return json(res, 200, CONVERSATIONS[0]);
    }
  }

  if (method === "POST" && /^\/api\/agents\/[^/]+\/conversations$/.test(path)) {
    return json(res, 200, CONVERSATIONS[0]);
  }

  if (method === "POST" && /^\/api\/agents\/[^/]+\/scopes$/.test(path)) {
    return json(res, 200, SCOPES);
  }

  json(res, 404, {
    error: { code: "not_found", message: `No mock for ${method} ${path}` },
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`demo mock API listening on http://127.0.0.1:${PORT}`);
});
