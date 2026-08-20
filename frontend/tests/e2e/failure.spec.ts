import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

/**
 * Phase 4 milestone test 4: failure is legible.
 *
 * "Disconnect the network mid-turn. The UI must show a clear error and
 * keep the conversation, not a blank screen or an infinite spinner."
 *
 * Playwright can fail a specific request, which is more precise than
 * pulling a cable: it fails ONLY the stream, leaving the rest of the
 * app working, so the test proves the error path rather than proving
 * that a dead browser shows nothing.
 */
test.describe("failure handling", () => {
  test.setTimeout(120_000);

  test("a dropped stream shows an error and keeps the page", async ({
    page,
  }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Offline Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Create agent" }).click();
    await expect(page).toHaveURL(/\/chat/);

    // Kill the streaming request only.
    await page.route("**/messages/stream", (route) => route.abort());

    await page.getByPlaceholder("Ask your agent...").fill("Are you there?");
    await page.getByRole("button", { name: "Send" }).click();

    // An error is shown.
    //
    // Matched by TEXT rather than getByRole("alert"): the toast
    // container also carries role="alert", so the role is ambiguous.
    await expect(
      page.getByText(/connection was lost|failed to fetch/i),
    ).toBeVisible({ timeout: 30_000 });

    // ...the question is still on screen, not a blank page...
    await expect(page.getByText("Are you there?")).toBeVisible();

    // ...and the app is still usable: no infinite spinner, and the
    // input is ready for another attempt.
    await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
    await expect(page.getByPlaceholder("Ask your agent...")).toBeEnabled();
  });

  test("a 503 from the tool server is explained, not swallowed", async ({
    page,
  }) => {
    await registerUser(page);
    await connectGithub(page);

    await page.goto("/agents/new");
    await page.getByLabel("Name").fill("Down Agent");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Create agent" }).click();
    await expect(page).toHaveURL(/\/chat/);

    // Pretend the MCP subprocess is down - the real 503 from
    // api/routers/chat.py.
    await page.route("**/messages/stream", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "The tool server is not available right now.",
        }),
      }),
    );

    await page.getByPlaceholder("Ask your agent...").fill("Hello?");
    await page.getByRole("button", { name: "Send" }).click();

    // The server's own words reach the user, not "something went wrong".
    await expect(
      page.getByText("The tool server is not available right now."),
    ).toBeVisible({ timeout: 30_000 });
  });
});
