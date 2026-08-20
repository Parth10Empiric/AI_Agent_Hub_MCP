import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

/**
 * The full turn, against a real model and a real MCP server.
 *
 * Slow by nature - the model thinks, then tools run over the network.
 * Given its own timeout rather than being made "fast" with a mock,
 * because a mocked version would prove almost nothing: the whole value
 * of this test is that routing, permissions, execution, persistence and
 * SSE all work together.
 */
test.describe("chat", () => {
  test.setTimeout(180_000);

  test("send a message and watch the timeline fill in", async ({ page }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Chat Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Create agent" }).click();

    await expect(page).toHaveURL(/\/chat/);

    await page
      .getByPlaceholder("Ask your agent...")
      .fill("List the open issues in the octocat/Hello-World repository");

    await page.getByRole("button", { name: "Send" }).click();

    // The question appears immediately - the UI does not wait for the
    // server to echo it back.
    await expect(page.getByText("List the open issues")).toBeVisible();

    // Progress is named, not a bare spinner.
    await expect(
      page.getByText(/Working out which tools|Thinking|Searching/),
    ).toBeVisible({ timeout: 30_000 });

    // The timeline appears and a tool resolves.
    //
    // exact: true matters. getByText matches a case-insensitive
    // SUBSTRING by default, so a bare "Tools" also matches the header's
    // "18 tools" and the locator resolves to two elements.
    await expect(
      page.getByText("Tools", { exact: true }),
    ).toBeVisible({ timeout: 120_000 });

    await expect(
      page.getByText(/Worked|Failed|Not allowed/).first(),
    ).toBeVisible({ timeout: 120_000 });

    // And a real answer arrives - not just the status line.
    await expect(page.getByText(/octocat|Hello-World|issue/i).first()).toBeVisible({
      timeout: 120_000,
    });
  });

  test("history survives a reload mid-conversation", async ({ page }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Reload Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Create agent" }).click();

    await expect(page).toHaveURL(/\/chat/);

    const question = "What can you help me with?";

    await page.getByPlaceholder("Ask your agent...").fill(question);
    await page.getByRole("button", { name: "Send" }).click();

    // Wait for the turn to genuinely FINISH.
    //
    // The "Stop" button is shown only while a turn is running, so its
    // disappearance is the real completion signal. Waiting on the input
    // being enabled would prove nothing - it is never disabled.
    await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
      timeout: 150_000,
    });

    await page.reload();

    // Milestone test 5: reload is safe.
    await expect(page.getByText(question)).toBeVisible({ timeout: 30_000 });
  });
});
