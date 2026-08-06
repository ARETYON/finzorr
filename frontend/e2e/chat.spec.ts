// The journeys the unit suites can't see: real browser, real backend,
// real LLM, real WebSocket — login/chat/stream/reload-persistence + public share.
import { expect, test } from '@playwright/test'

async function devLogin(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByRole('button', { name: /continue as dev user/i }).click()
  await expect(page).toHaveURL(/\/chat/)
}

// The user's own bubble (and later the sidebar title) contain the marker
// too, so a bare text match resolves the instant the message is sent —
// scope to an ASSISTANT bubble so only the echoed reply satisfies the wait.
async function sendAndAwaitEcho(page: import('@playwright/test').Page, marker: string) {
  const input = page.getByLabel('Message finzorr')
  await input.fill(`Reply with exactly: ${marker}`)
  await input.press('Enter')
  await expect(
    page.locator('.msg-assistant', { hasText: marker }).first(),
  ).toBeVisible({ timeout: 120_000 })
  // the echo appears while streaming; give the persist node a beat to commit
  await page.waitForTimeout(2000)
}

test('login → chat → streamed response persists across reload', async ({ page }) => {
  await devLogin(page)
  await sendAndAwaitEcho(page, 'E2E-MARKER-42')

  await page.reload()
  // session selection is not persisted across reloads — reopen from the sidebar
  // (session rows are buttons containing a MessageSquare icon + the title)
  await page.locator('button:has(span.truncate)').first().click()
  // both the user message AND the assistant reply must have survived the reload
  await expect(
    page.locator('.msg-user', { hasText: 'E2E-MARKER-42' }).first(),
  ).toBeVisible({ timeout: 15_000 })
  await expect(
    page.locator('.msg-assistant', { hasText: 'E2E-MARKER-42' }).first(),
  ).toBeVisible({ timeout: 15_000 })
})

test('share link opens logged-out', async ({ page, browser }) => {
  await devLogin(page)
  await sendAndAwaitEcho(page, 'SHARE-MARKER-7')
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.getByRole('button', { name: 'Share conversation' }).click()
  await expect(page.getByText(/Share link copied/)).toBeVisible()
  const url = await page.evaluate(() => navigator.clipboard.readText())
  expect(url).toContain('/share/')

  const loggedOut = await browser.newContext()
  const publicPage = await loggedOut.newPage()
  await publicPage.goto(url)
  await expect(publicPage.getByText(/SHARE-MARKER-7/).first()).toBeVisible({ timeout: 15_000 })
  await loggedOut.close()
})
