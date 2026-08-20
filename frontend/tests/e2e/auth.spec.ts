import { expect, test } from "@playwright/test";
import { PASSWORD, registerUser, uniqueEmail } from "./helpers";

test.describe("authentication", () => {
  test("register, then land on the dashboard", async ({ page }) => {
    await registerUser(page);

    await expect(
      page.getByRole("heading", { name: /no agents yet|welcome back/i }),
    ).toBeVisible();
  });

  test("a signed-out visitor is sent to login", async ({ page }) => {
    await page.goto("/dashboard");

    await expect(page).toHaveURL(/\/login/);
  });

  test("wrong password is refused without saying which field was wrong", async ({
    page,
  }) => {
    const email = await registerUser(page);

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // Deliberately vague: revealing "no such user" would let an
    // attacker enumerate which emails are registered.
    //
    // Matched by TEXT, not by getByRole("alert"): the toast container
    // also carries role="alert", so the role alone is ambiguous.
    await expect(
      page.getByText("Email or password is incorrect."),
    ).toBeVisible();
  });

  test("log in again after signing out", async ({ page }) => {
    const email = await registerUser(page);

    await page.getByRole("button", { name: "Sign out" }).click();

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page).toHaveURL(/\/dashboard/);
  });

  /**
   * The one that proves the token design works.
   *
   * The access token lives in memory and dies on reload. If the session
   * survives a refresh, the httpOnly refresh cookie did its job.
   */
  test("the session survives a page reload", async ({ page }) => {
    await registerUser(page);

    await page.reload();

    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("short passwords are rejected before any request", async ({ page }) => {
    await page.goto("/register");

    await page.getByLabel("Email").fill(uniqueEmail());
    await page.getByLabel("Password").fill("short");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByText("Use at least 8 characters.")).toBeVisible();
    await expect(page).toHaveURL(/\/register/);
  });
});
