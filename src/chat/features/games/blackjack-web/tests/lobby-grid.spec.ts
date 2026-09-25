import { test, expect } from '@playwright/test';
import { fileURLToPath } from 'node:url';

for (const viewport of [{ width: 1440, height: 900 }, { width: 844, height: 390 }, { width: 390, height: 844 }, { width: 568, height: 320 }]) {
  test(`大厅分行入口可见且无横向溢出 ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route('**/api/config', route => route.fulfill({ json: { discord_client_id: 'test', noname_available: false } }));
    await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '道友', avatar_url: '/ui/player-avatar.svg', balance: 2000 } }));
    await page.goto('/?dev_user_id=123456789012345678');
    const cards = page.locator('.hub-game-grid > .game-card');
    await expect(cards).toHaveCount(6);
    const layout = await cards.evaluateAll(items => items.map(item => ({ top: (item as HTMLElement).offsetTop, width: (item as HTMLElement).offsetWidth, height: (item as HTMLElement).offsetHeight })));
    expect(new Set(layout.map(item => item.top)).size).toBeGreaterThan(1);
    const logicalHeight = Math.min(viewport.width, viewport.height);
    const minimumHeight = logicalHeight <= 320 ? 56 : logicalHeight <= 600 ? 72 : 90;
    expect(layout.every(item => item.width >= 140 && item.height >= minimumHeight), JSON.stringify(layout)).toBe(true);
    expect(await page.locator('.hub-game-grid').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    await expect(page.getByRole('button', { name: /修仙灵圃/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /修仙灵圃/ })).toHaveCount(1);
    await expect(page.locator('.lobby-auxiliary .farm-entry')).toBeVisible();
    await expect(cards.filter({ hasText: '炸金花' })).toHaveCount(1);
    await expect(cards.filter({ hasText: '掼蛋' })).toHaveCount(1);
    await expect.poll(() => cards.locator('.game-card-art img').evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    await expect(page.locator('.yueyue-mascot-sprite')).toBeVisible();
    expect(await page.locator('.yueyue-mascot-preload').evaluate(image => [(image as HTMLImageElement).naturalWidth, (image as HTMLImageElement).naturalHeight])).toEqual([1536, 2288]);
    const mascot = page.getByRole('button', { name: '和月月打招呼' });
    await expect(mascot).toBeInViewport({ ratio: 1 });
    expect(await page.locator('.multi-root').evaluate(element => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
    const background = await page.locator('.multi-root').evaluate(element => getComputedStyle(element).backgroundImage);
    expect(background).toContain('/ui/lobby/yueyue-teahouse.webp');
    expect(background).not.toContain('lobby-world.webp');
    for (const card of await cards.all()) await expect(card).toBeInViewport({ ratio: 1 });
    await mascot.click();
    await expect(page.locator('.yueyue-mascot-sprite')).toHaveAttribute('data-animation', 'waving');
    await expect(page.locator('.yueyue-mascot-speech')).toContainText('我在呢，今天想玩什么？');
    await page.screenshot({ path: fileURLToPath(new URL(`../../../../../../screenshots/lobby-redesign/polished-${viewport.width}x${viewport.height}.png`, import.meta.url)), fullPage: true });
    await page.getByRole('button', { name: /21点.*立即游玩/ }).click();
    await expect(page.getByRole('button', { name: /单人对战/ })).toBeVisible();
    await page.getByRole('button', { name: '返回上一级' }).click();
    await expect(cards).toHaveCount(6);
  });
}
