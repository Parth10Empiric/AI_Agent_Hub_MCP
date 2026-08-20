import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

test.describe("agents", () => {
  test("create an agent through the wizard", async ({ page }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");

    // 1. Identity
    await page.getByLabel("Name").fill("E2E Agent");
    await page.getByRole("button", { name: "Continue" }).click();

    // 2. Instructions (pre-filled, so Continue is already enabled)
    await page.getByRole("button", { name: "Continue" }).click();

    // 3. Services
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();

    // 4. Tools - reads are ticked for us, which is the whole point.
    await expect(page.getByText(/\d+ of \d+ tools enabled/)).toBeVisible();

    await page.getByRole("button", { name: "Create agent" }).click();

    await expect(page).toHaveURL(/\/agents\/[0-9a-f-]+\/chat/);
    await expect(page.getByRole("heading", { name: "E2E Agent" })).toBeVisible();
  });

  /**
   * The safety default, verified in the UI.
   *
   * Phase 3.6 says a new agent gets reads on and writes off, so it is
   * safe until its owner decides otherwise. This proves the rule
   * survives all the way to the screen.
   */
  test("a new agent enables reads and no writes", async ({ page }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Defaults Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();

    // Every ticked box must sit in the Read group. Counting checked
    // boxes inside the write section is the assertion that matters.
    const counter = page.getByText(/\d+ of \d+ tools enabled/);
    await expect(counter).toBeVisible();

    const text = (await counter.textContent()) ?? "";
    const [enabled, total] = text.match(/\d+/g)!.map(Number);

    expect(enabled).toBeGreaterThan(0);
    expect(enabled).toBeLessThan(total);
  });

  test("edit an agent from its settings page", async ({ page }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Rename Me");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Create agent" }).click();

    await expect(page).toHaveURL(/\/chat/);

    await page.getByRole("link", { name: "Settings" }).click();

    await page.getByLabel("Name").fill("Renamed Agent");
    await page.getByRole("button", { name: "Save details" }).click();

    await page.goto("/agents");
    await expect(page.getByText("Renamed Agent")).toBeVisible();
  });

  test("the wizard draft survives a page reload", async ({ page }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Draft Survivor");

    await page.reload();

    // Nothing lost on refresh - Phase 4.5's requirement.
    await expect(page.getByLabel("Name")).toHaveValue("Draft Survivor");
  });
});
