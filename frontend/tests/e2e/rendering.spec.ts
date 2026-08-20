import { expect, test } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

/**
 * Two things a user reported, now guarded.
 *
 * 1. The agent's reply appeared ABOVE the question that produced it.
 * 2. Formatting showed as raw characters: **bold** instead of bold.
 *
 * Both are display bugs, so both are tested by looking at what is
 * actually on screen - not at what the API returned.
 */
async function makeAgentAndOpenChat(page: import("@playwright/test").Page) {
  await registerUser(page);
  await connectGithub(page);

  await page.goto("/agents/new");
  await page.getByLabel("Name").fill("Render Agent");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(page).toHaveURL(/\/chat/);
}

test.describe("how messages are displayed", () => {
  test.setTimeout(240_000);

  test("the question always appears above its answer", async ({ page }) => {
    await makeAgentAndOpenChat(page);

    // Two turns, because the bug showed up on a later turn: the pair
    // shares one timestamp, so the order was decided by a random UUID.
    for (const question of ["Say hello", "Say goodbye"]) {
      await page.getByPlaceholder("Ask your agent...").fill(question);
      await page.getByRole("button", { name: "Send" }).click();
      await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
        timeout: 150_000,
      });
    }

    // Reload, so this reads the SORTED history from the server rather
    // than the live turn that was assembled in the browser.
    await page.reload();

    // Scoped to the transcript. The conversation sidebar names each
    // chat after its first message, so "Say hello" is on screen twice -
    // once as a chat title, once as the message. An unscoped locator
    // matches both and fails strict mode.
    await expect(
      page.getByTestId("user-message").filter({ hasText: "Say hello" }),
    ).toBeVisible({ timeout: 30_000 });

    // Every "You" label must come before the "Agent" label that answers
    // it. Reading the rendered order top to bottom is the only way to
    // assert this - the API order is exactly what was wrong.
    //
    // `main` excludes the sidebar and the app navigation, so only the
    // transcript's own role labels are counted.
    const labels = await page
      .locator("main span")
      .filter({ hasText: /^(You|Agent)$/ })
      .allInnerTexts();

    const turns = labels.filter((l) => l === "You" || l === "Agent");

    expect(turns.length).toBeGreaterThanOrEqual(4);

    // The transcript must alternate, starting with the person.
    expect(turns[0]).toBe("You");

    for (let i = 0; i < turns.length - 1; i += 2) {
      expect(turns[i]).toBe("You");
      expect(turns[i + 1]).toBe("Agent");
    }
  });

  test("formatting is rendered, not shown as raw characters", async ({
    page,
  }) => {
    await makeAgentAndOpenChat(page);

    await page
      .getByPlaceholder("Ask your agent...")
      .fill(
        "Reply with exactly this and nothing else: **Bold words** and `some_code`",
      );
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
      timeout: 150_000,
    });

    const answer = page.getByTestId("agent-message").last();

    // A real <strong> element exists inside the agent's bubble...
    await expect(answer.locator("strong").first()).toBeVisible({
      timeout: 30_000,
    });

    // ...and the raw asterisks are gone from THAT bubble.
    //
    // Scoped to the agent's message on purpose: the user's own question
    // contains "**" because they typed it, and it must stay that way.
    // Checking the whole page would fail on the question, not the
    // answer - which is a test bug wearing a product bug's clothes.
    expect(await answer.innerText()).not.toContain("**");
  });

  test("what the USER typed is never reformatted", async ({ page }) => {
    await makeAgentAndOpenChat(page);

    // Someone asking about markdown syntax must see their own
    // asterisks, not bold text.
    const typed = "What does **this** mean?";

    await page.getByPlaceholder("Ask your agent...").fill(typed);
    await page.getByRole("button", { name: "Send" }).click();

    // Shown verbatim, immediately - no waiting for the server.
    await expect(page.getByText(typed, { exact: true })).toBeVisible();
  });
});
