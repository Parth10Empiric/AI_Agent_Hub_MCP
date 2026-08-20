import { expect, type Page } from "@playwright/test";

/**
 * A fresh user per test.
 *
 * Tests share one real database, so a fixed email would make the second
 * run of the suite fail on a duplicate. A random suffix keeps every run
 * independent without needing a cleanup step.
 */
export function uniqueEmail(prefix = "e2e"): string {
  const random = Math.random().toString(36).slice(2, 10);

  return `${prefix}_${Date.now()}_${random}@example.com`;
}

export const PASSWORD = "S3curePassw0rd!";

/** Register through the UI and land on the dashboard. */
export async function registerUser(page: Page, email = uniqueEmail()) {
  await page.goto("/register");

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/dashboard/);

  return email;
}

/** Connect a service by pasting a token. */
export async function connectGithub(page: Page) {
  await page.goto("/plugins");

  const card = page.locator("div").filter({ hasText: /^GitHub/ }).first();

  await page.getByRole("button", { name: "Connect" }).first().click();

  await page.getByLabel("Access token").fill("ghp_e2e_fake_token_value");
  await page.getByRole("button", { name: "Connect", exact: true }).last().click();

  await expect(page.getByText("Connected").first()).toBeVisible();

  return card;
}
