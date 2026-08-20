import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

/**
 * A write tool is stopped, and the UI says so.
 *
 * Phase 4.7's full flow - dialog, Approve, tool runs - is NOT what this
 * tests, because the backend cannot resume a suspended turn yet
 * (api/approvals.py denies and reports; resuming is Phase 5). What is
 * tested is the part that must be true TODAY: a tool needing permission
 * does not silently run, and the user is told.
 */
test.describe("permission", () => {
  test.setTimeout(180_000);

  test("a write tool is refused and explained", async ({ page }) => {
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
    const createIssue = page.locator("li", {
      hasText: "Create issue",
    });
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
      timeout: 150_000,
    });

    // The refusal is visible and is NOT dressed up as a failure.
    await expect(page.getByText(/needed permission for/i)).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole("button", { name: "Details" }).first().click();

    await expect(
      page.getByRole("dialog").getByText("github_create_issue"),
    ).toBeVisible();

    // Plain-language explanation of what the tool would have done -
    // not the word "write", which means nothing to a non-developer.
    await expect(page.getByRole("dialog")).toContainText(
      "Creates or edits things in your account",
    );

    // And it is honest that approving is not possible yet.
    await expect(page.getByRole("dialog")).toContainText(
      "not available yet",
    );
  });
});
