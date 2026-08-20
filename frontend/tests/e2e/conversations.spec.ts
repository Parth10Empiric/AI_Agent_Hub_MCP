import { expect, test, type Page } from "@playwright/test";
import { connectGithub, registerUser } from "./helpers";

/**
 * Going back to an earlier chat.
 *
 * Before the sidebar, "New chat" was a one-way door: the old
 * conversation was still in the database but there was no way to reach
 * it. These tests guard the door.
 */
async function openAgentChat(page: Page, name = "Chat List Agent") {
  await registerUser(page);
  await connectGithub(page);

  await page.goto("/agents/new");
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Create agent" }).click();

  await expect(page).toHaveURL(/\/chat/);
}

/** Send a message and wait for the turn to finish. */
async function say(page: Page, text: string) {
  // The input carries a different placeholder while a chat is being
  // created, so waiting for this one proves the chat is ready to type
  // into - and that the component will not remount underneath us.
  const box = page.getByPlaceholder("Ask your agent...");
  await expect(box).toBeEnabled({ timeout: 30_000 });

  await box.fill(text);
  await expect(page.getByRole("button", { name: "Send" })).toBeEnabled();
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("button", { name: "Stop" })).toBeHidden({
    timeout: 180_000,
  });
}

/**
 * Click "+ New chat" and wait until the URL really points at a new one.
 *
 * Clicking returns immediately; the conversation is created over the
 * network. Typing before the URL changes writes into an input that is
 * about to be remounted.
 */
async function newChat(page: Page) {
  const before = new URL(page.url()).searchParams.get("c");

  await page.getByRole("button", { name: "+ New chat" }).click();

  await expect
    .poll(() => new URL(page.url()).searchParams.get("c"), {
      timeout: 30_000,
    })
    .not.toBe(before);
}

test.describe("conversation list", () => {
  test.setTimeout(300_000);

  test("an old chat is still reachable after starting a new one", async ({
    page,
  }) => {
    await openAgentChat(page);

    await say(page, "Remember the word apricot");

    // Start a second chat. This is the action that used to lose the
    // first one.
    await newChat(page);
    await say(page, "Remember the word zebra");

    const list = page.getByRole("navigation", { name: "Conversations" });

    // Both chats are listed, named from their first message.
    await expect(list.getByText(/apricot/i)).toBeVisible({ timeout: 30_000 });
    await expect(list.getByText(/zebra/i)).toBeVisible();

    // Click back into the first one and its messages are there.
    await list.getByText(/apricot/i).click();

    // Scoped to the TRANSCRIPT, not the page.
    //
    // The sidebar shows each chat's title, and a title is derived from
    // its first message - so "Remember the word zebra" legitimately
    // appears in the chat list while you are reading the apricot chat.
    // Asserting against the whole page would fail on the sidebar and
    // look like a bug in the app.
    const transcript = page.getByTestId("user-message");

    await expect(
      transcript.filter({ hasText: "Remember the word apricot" }),
    ).toBeVisible({ timeout: 30_000 });

    // The other chat's MESSAGE is not in this transcript.
    await expect(
      transcript.filter({ hasText: "Remember the word zebra" }),
    ).toHaveCount(0);
  });

  test("the open chat is in the URL, so a reload keeps it", async ({
    page,
  }) => {
    await openAgentChat(page, "URL Agent");

    await say(page, "First conversation here");
    await newChat(page);
    await say(page, "Second conversation here");

    const list = page.getByRole("navigation", { name: "Conversations" });

    // Scoped to the transcript throughout: a chat's title is derived
    // from its first message, so the same words appear in the sidebar
    // AND in the message. An unscoped locator matches both and fails
    // strict mode - which reads like an app bug and is not one.
    const transcript = page.getByTestId("user-message");
    const firstMessage = transcript.filter({
      hasText: "First conversation here",
    });

    await list.getByText(/First conversation/i).click();
    await expect(firstMessage).toBeVisible({ timeout: 30_000 });

    // The conversation id is in the query string...
    await expect(page).toHaveURL(/\?c=[0-9a-f-]{36}/);

    const urlBefore = page.url();

    // ...so a refresh returns to the SAME chat, not the newest one.
    await page.reload();

    await expect(firstMessage).toBeVisible({ timeout: 30_000 });
    expect(page.url()).toBe(urlBefore);
  });

  test("a chat can be renamed and deleted", async ({ page }) => {
    await openAgentChat(page, "Manage Agent");

    await say(page, "Rename this chat please");

    const list = page.getByRole("navigation", { name: "Conversations" });
    await expect(list.getByText(/Rename this chat/i)).toBeVisible({
      timeout: 30_000,
    });

    await list.getByRole("button", { name: /Options for/ }).first().click();
    await page.getByRole("menuitem", { name: "Rename" }).click();

    await page.getByLabel("Chat name").fill("My renamed chat");
    await page.getByRole("button", { name: "Save" }).click();

    await expect(list.getByText("My renamed chat")).toBeVisible();

    // Delete asks first, and Cancel really cancels.
    await list.getByRole("button", { name: /Options for/ }).first().click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(list.getByText("My renamed chat")).toBeVisible();

    await list.getByRole("button", { name: /Options for/ }).first().click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    await page.getByRole("button", { name: "Delete", exact: true }).click();

    await expect(list.getByText("My renamed chat")).toHaveCount(0, {
      timeout: 30_000,
    });
  });
});
