import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

/**
 * The two ways a write can be stopped, and they are NOT the same.
 *
 *   PERMISSION   the agent was never allowed to do this. Refused
 *                below the model, before anyone is asked. Fixed on
 *                the permissions screen. (Phase 5.1)
 *
 *   APPROVAL     the agent IS allowed, and a human has to say yes to
 *                this particular call. The turn SUSPENDS and waits.
 *                (Phase 5.2)
 *
 * Both must be visible and both must be honest about what happened,
 * because a user who cannot tell them apart cannot fix either.
 */
test.describe("permission and approval", () => {
  test.setTimeout(240_000);

  test("a write tool with no permission is refused and explained", async ({
    page,
  }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Writer Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();

    // Turn ON a tool that changes the account. It is off by default,
    // which is itself the behaviour agents.spec.ts asserts.
    const createIssue = page.locator("li", { hasText: "Create issue" });
    await createIssue.getByRole("checkbox").first().check();

    await page.getByRole("button", { name: "Create agent" }).click();
    await expect(page).toHaveURL(/\/chat/);

    await page
      .getByPlaceholder("Ask your agent...")
      .fill(
        "Create a GitHub issue in octocat/Hello-World titled 'Test' with body 'testing'",
      );
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
      timeout: 200_000,
    });

    // Ticking the tool was NOT enough - the scope was never granted.
    // That is the whole point of the second gate.
    await expect(page.getByText(/needed permission for/i)).toBeVisible({
      timeout: 30_000,
    });

    await expect(
      page.getByText(/has not been allowed to do this/i),
    ).toBeVisible();

    // ...and the message points at the screen that fixes it.
    await expect(
      page.getByRole("link", { name: /review its permissions/i }),
    ).toBeVisible();
  });

  test("granting a scope turns the refusal into a prompt", async ({
    page,
  }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Approver Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();

    const createIssue = page.locator("li", { hasText: "Create issue" });
    await createIssue.getByRole("checkbox").first().check();

    await page.getByRole("button", { name: "Create agent" }).click();
    await expect(page).toHaveURL(/\/chat/);

    const agentId = page.url().match(/agents\/([^/]+)\//)?.[1];

    expect(agentId).toBeTruthy();

    // --- grant the write scope ------------------------------------
    await page.goto(`/agents/${agentId}/permissions`);

    await page
      .getByRole("switch", { name: /Grant github:\*:write/i })
      .click();

    await expect(
      page.getByRole("switch", { name: /Revoke github:\*:write/i }),
    ).toBeVisible();

    // --- now the agent stops and ASKS -----------------------------
    await page.goto(`/agents/${agentId}/chat`);

    await page
      .getByPlaceholder("Ask your agent...")
      .fill(
        "Create a GitHub issue in octocat/Hello-World titled 'Test' with body 'testing'",
      );
    await page.getByRole("button", { name: "Send" }).click();

    const prompt = page.getByTestId("approval-request");

    await expect(prompt).toBeVisible({ timeout: 200_000 });

    // THE ASSERTION THAT MATTERS: the real argument VALUES are on
    // screen. A prompt that shows only argument names trains people to
    // approve without reading, and then a prompt-injected
    // "attacker@evil.com" gets approved too.
    await expect(prompt).toContainText("github_create_issue");
    await expect(prompt).toContainText("octocat");
    await expect(prompt).toContainText("Hello-World");

    // Deny, and nothing happens.
    await prompt.getByRole("button", { name: "Deny" }).click();

    await expect(prompt).toContainText(/Denied/i);

    await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
      timeout: 120_000,
    });
  });
});
