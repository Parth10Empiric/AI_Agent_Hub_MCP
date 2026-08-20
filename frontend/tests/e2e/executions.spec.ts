import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

test.describe("activity", () => {
  test("a new user sees an empty state, not an error", async ({ page }) => {
    await registerUser(page);
    await page.goto("/executions");

    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();
    await expect(page.getByText(/Nothing here yet/)).toBeVisible();
  });

  test("filters do not break the page", async ({ page }) => {
    await registerUser(page);
    await page.goto("/executions");

    await page.getByRole("combobox").first().click();
    await page.getByRole("option", { name: "Failed" }).click();

    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();

    await page.getByRole("combobox").nth(1).click();
    await page.getByRole("option", { name: "30 days" }).click();

    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();
  });
});

/**
 * The bug a user hit within minutes of opening the page.
 *
 * Switching the day filter back to one already visited showed an empty
 * list and made NO request. The cause was `setPages` living inside
 * `queryFn`: on a cache hit `queryFn` never runs, so the rows were
 * cleared and never refilled.
 *
 * This test needs REAL rows, so it runs one agent turn first. That is
 * slow, and it is the only way the bug reproduces - with zero rows the
 * empty state looks identical to the failure.
 */
test.describe("activity filters", () => {
  test.setTimeout(240_000);

  test("switching back to a previous filter still shows the rows", async ({
    page,
  }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Filter Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Create agent" }).click();
    await expect(page).toHaveURL(/\/chat/);

    // One turn, so there is at least one execution to look at.
    await page
      .getByPlaceholder("Ask your agent...")
      .fill("List the open issues in the octocat/Hello-World repository");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
      timeout: 180_000,
    });

    await page.goto("/executions");

    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 30_000 });

    const dayFilter = page.getByRole("combobox").nth(1);

    async function pickDay(label: string) {
      await dayFilter.click();
      await page.getByRole("option", { name: label }).click();
    }

    // The exact sequence from the report: 7 days → Today → 7 days →
    // Today. The second visit to each is the cache hit that broke.
    await pickDay("Today");
    await expect(rows.first()).toBeVisible();

    await pickDay("7 days");
    await expect(rows.first()).toBeVisible();

    await pickDay("Today");
    await expect(rows.first()).toBeVisible();

    await pickDay("7 days");
    await expect(rows.first()).toBeVisible();

    // And the status filter, which shares the same query key.
    const statusFilter = page.getByRole("combobox").first();

    await statusFilter.click();
    await page.getByRole("option", { name: "Worked" }).click();
    await expect(rows.first()).toBeVisible();

    await statusFilter.click();
    // exact: true — getByRole matches the accessible name as a
    // case-insensitive SUBSTRING by default, and "All" is inside
    // "Not allowed".
    await page.getByRole("option", { name: "All", exact: true }).click();
    await expect(rows.first()).toBeVisible();
  });
});
