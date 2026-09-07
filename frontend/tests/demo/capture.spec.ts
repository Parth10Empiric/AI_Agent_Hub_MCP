import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * Renders the screenshots in docs/media that the READMEs embed.
 *
 * NOT A TEST. Nothing here asserts that the product is correct - the
 * suite next door does that. The `expect` calls below are WAITS: a
 * screenshot taken before the data arrives is a picture of a skeleton
 * loader, and it lands at a different moment on every machine. Waiting
 * for a specific element is what makes the output the same image every
 * run, so a regenerated file is a diff only when the UI really changed.
 *
 *   npm run capture
 */

const MEDIA = path.resolve(__dirname, "../../../docs/media");

const CREDENTIALS = {
  email: "demo@example.com",
  password: "S3curePassw0rd!",
};

// Must match mock-api.mjs.
const AGENT_ID = "agt_3f9b2c71";

const QUESTION =
  "Draft release notes for v2.0.0 and open them as an issue on " +
  "octocat/Hello-World";

test.beforeAll(() => {
  mkdirSync(MEDIA, { recursive: true });
});

/** Sign in and land on the dashboard. */
async function signIn(page: Page) {
  await page.goto("/login");

  await page.getByLabel("Email").fill(CREDENTIALS.email);
  await page.getByLabel("Password").fill(CREDENTIALS.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/dashboard/);
}

/**
 * Take the caret out of the picture.
 *
 * A screenshot catches the text cursor mid-blink about half the time,
 * so two runs of this file produce two different images of an
 * unchanged UI. Blurring whatever has focus makes it deterministic.
 */
async function shot(page: Page, name: string) {
  await page.locator("body").click({ position: { x: 4, y: 4 } });
  await page.waitForTimeout(400);

  await page.screenshot({ path: path.join(MEDIA, `${name}.png`) });
}

/**
 * Ask for something that needs a write, and hand back the prompt it
 * will stop on.
 *
 * The mock backend's stream genuinely blocks at `approval_required`
 * until POST /api/approvals/{id}/{approve,deny} arrives, so every wait
 * after this returns is real waiting, not a timer.
 */
async function askForAnIssue(page: Page) {
  const composer = page.getByPlaceholder("Ask your agent...");

  await expect(composer).toBeVisible();
  await composer.fill(QUESTION);

  await page.getByRole("button", { name: "Send" }).click();

  return page.getByTestId("approval-request");
}

/**
 * Photograph one element rather than the window.
 *
 * The chat viewport is about 450px tall, and both the tool timeline
 * with its detail panel open and the approval prompt are taller than
 * that - a full-page screenshot cuts the bottom off, which on the
 * approval prompt means cropping out the Approve and Deny buttons that
 * are the entire point of the image.
 *
 * An element screenshot scrolls the element into view and captures all
 * of it whatever its height, and the tighter crop reads better in a
 * README column too.
 */
async function shotOf(locator: Locator, name: string) {
  await locator.scrollIntoViewIfNeeded();
  await locator.page().waitForTimeout(300);

  await locator.screenshot({ path: path.join(MEDIA, `${name}.png`) });
}

/** Wait for the turn to finish - the Stop button is the only signal. */
async function turnFinished(page: Page) {
  await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
    timeout: 60_000,
  });
}

test("capture documentation screenshots", async ({ page }) => {
  // --- the product, screen by screen ---------------------------------

  await signIn(page);

  await expect(
    page.getByRole("heading", { name: /welcome back/i }),
  ).toBeVisible();
  await shot(page, "01-dashboard");

  await page.goto("/plugins");
  await expect(page.getByText("GitHub").first()).toBeVisible();
  await shot(page, "02-plugins");

  await page.goto("/agents");
  await expect(page.getByText("Release Assistant")).toBeVisible();
  await shot(page, "03-agents");

  // --- a turn, up to the point it stops and asks ----------------------

  await page.goto(`/agents/${AGENT_ID}/chat`);

  const prompt = await askForAnIssue(page);

  // One row opened, so the detail panel - service, operation, risk,
  // execution id - is in the picture. That panel is the answer to
  // "what did it actually do", and a shot of the collapsed list does
  // not show it.
  //
  // Located by the READABLE name: the timeline deliberately renders
  // "Get file contents", not `github_get_file_contents`.
  const row = page.getByRole("button", { name: /Get file contents/i });

  await expect(row).toBeVisible({ timeout: 60_000 });
  await row.click();
  await expect(page.getByText("Risk")).toBeVisible();

  // .last(): the conversation's earlier message carries a timeline of
  // its own, and the one being built right now is the one worth a
  // picture.
  await shotOf(page.getByTestId("tool-timeline").last(), "04-tool-timeline");

  // Close it again so the approval shot is not half detail panel.
  await row.click();

  // THE SCREEN THIS PROJECT EXISTS FOR: the turn is suspended on the
  // server, and the argument VALUES it wants to write are on screen
  // verbatim, above an Approve and a Deny button.
  await expect(prompt).toBeVisible({ timeout: 60_000 });
  await expect(prompt).toContainText("github_create_issue");
  await shotOf(prompt, "05-approval-request");

  // --- deny, and show that nothing happened --------------------------

  await prompt.getByRole("button", { name: "Deny" }).click();
  await expect(prompt).toContainText(/denied/i);

  // Let the turn finish, so the reply is in shot too. The point of the
  // image is that a denial ends in a useful answer - here is the draft,
  // file it yourself - rather than an error.
  await turnFinished(page);
  await shot(page, "06-approval-denied");

  // --- and again, approved -------------------------------------------
  //
  // Both outcomes are worth a picture. The denial is the one that
  // proves the safety story; the approval is the one that shows the
  // product doing the thing it was asked to do.

  await askForAnIssue(page);

  await expect(prompt).toBeVisible({ timeout: 60_000 });
  await prompt.getByRole("button", { name: "Approve" }).click();

  await turnFinished(page);
  await shot(page, "07-approval-approved");

  // --- the rest of the product ---------------------------------------

  await page.goto(`/agents/${AGENT_ID}/permissions`);
  await expect(page.getByText("github:*:write").first()).toBeVisible();
  await shot(page, "08-permissions");

  await page.goto("/executions");
  await expect(page.getByRole("table")).toBeVisible();
  await shot(page, "09-executions");

  await page.goto("/security");
  await expect(page.getByText(/signed in/i).first()).toBeVisible();
  await shot(page, "10-security");
});
