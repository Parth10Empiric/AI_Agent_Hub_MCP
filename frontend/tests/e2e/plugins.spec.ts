import { expect, test } from "@playwright/test";
import { registerUser } from "./helpers";

test.describe("services", () => {
  test("the catalogue comes from the live tool registry", async ({ page }) => {
    await registerUser(page);
    await page.goto("/plugins");

    // These are not hardcoded in the frontend - they exist because the
    // MCP server registered them.
    await expect(page.getByText("GitHub").first()).toBeVisible();
    await expect(page.getByText("Slack").first()).toBeVisible();

    // A tool count proves the registry was actually read.
    await expect(page.getByText(/\d+ tools/).first()).toBeVisible();
  });

  test("connect and then disconnect a service", async ({ page }) => {
    await registerUser(page);
    await page.goto("/plugins");

    await page.getByRole("button", { name: "Connect" }).first().click();
    await page.getByLabel("Access token").fill("ghp_e2e_fake_token_value");
    await page
      .getByRole("button", { name: "Connect", exact: true })
      .last()
      .click();

    await expect(page.getByText("Connected").first()).toBeVisible();

    await page.getByRole("button", { name: "Disconnect" }).first().click();
    await expect(page.getByText("Not connected").first()).toBeVisible();
  });

  test("tools are grouped by what they do, not listed flat", async ({
    page,
  }) => {
    await registerUser(page);
    await page.goto("/plugins/github");

    // The grouping is driven by `operation` on every tool, classified
    // once in Phase 2.
    await expect(
      page.getByRole("heading", { name: "Read", exact: true }),
    ).toBeVisible();

    // And a risk word, never colour alone.
    await expect(page.getByText(/safe|medium risk|high risk/i).first()).toBeVisible();
  });
});
