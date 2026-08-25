import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

/**
 * Granting a write PERMISSION must be the whole story.
 *
 * The failure this covers has changed shape. It used to be two gates
 * with one UI: a tool checkbox and a permission scope, where only the
 * checkbox was visible, so a user ticked eight green boxes and their
 * agent still reported itself read-only.
 *
 * The checkbox grid is gone. The risk now is the mirror image - a scope
 * switched on that STILL does nothing, because the invisible half
 * (agent_tools.enabled) defaulted to read-only underneath it. So this
 * test asserts the new contract end to end:
 *
 *   1. a new agent starts with no write permission, and says so
 *   2. switching one on is a single action, on one screen
 *   3. the tools it covers become available, with nothing else to tick
 */
test.describe("granting a write permission", () => {
  test.setTimeout(180_000);

  test("is one switch, and makes the write tools available", async ({
    page,
  }) => {
    await registerUser(page);
    await connectGithub(page);

    // --- create an agent: identity, instructions, services ---------
    //
    // Three steps now. There is no tool-picking step to walk past.
    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Write Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();

    await page.getByRole("button", { name: "Create agent" }).click();
    await expect(page).toHaveURL(/\/chat/);

    const agentId = page.url().match(/agents\/([^/]+)\//)?.[1];
    expect(agentId).toBeTruthy();

    // --- settings shows the permissions, and no checkbox grid ------
    await page.goto(`/agents/${agentId}/settings`);

    const summary = page.getByTestId("permissions-summary");
    await expect(summary).toBeVisible();

    // Read seeded, write not. The agent can look and cannot touch.
    await expect(summary).toContainText(/Read anything on GitHub/i);
    await expect(summary).not.toContainText(/Create and change/i);

    // --- one switch, on the permissions screen ---------------------
    await page.getByRole("link", { name: "Manage permissions" }).click();
    await expect(page).toHaveURL(/\/permissions/);

    const grant = page.getByRole("switch", {
      name: /Grant github:issue:write/i,
    });

    await expect(grant).toBeVisible();
    await grant.click();

    // The switch flips to its revoke state - the grant landed.
    await expect(
      page.getByRole("switch", { name: /Revoke github:issue:write/i }),
    ).toBeVisible({ timeout: 20_000 });

    // --- and that is all it took -----------------------------------
    //
    // Back on settings the permission is listed, and the tool count it
    // unlocks has gone up. If agent_tools.enabled were still defaulting
    // to read-only, the scope would appear here and the count would not
    // move - which is exactly the silent half-grant this asserts is
    // gone.
    await page.goto(`/agents/${agentId}/settings`);

    await expect(summary).toContainText(/Create and change issue on GitHub/i);
    await expect(summary).toContainText(/tools are available to this agent/i);
  });
});
