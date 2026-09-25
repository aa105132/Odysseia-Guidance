import { expect, test, type Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
const uid = '123456789012345678';
const names = ['21点', '德州扑克', '斗地主', '四川麻将', '炸金花', '掼蛋', '修仙灵圃', '排行榜'];
async function openWorld(page: Page, noname = false, embedded = false) {
  await page.route(/.*(?:@discord_embedded-app-sdk|@discord\/embedded-app-sdk).*\.(?:js|mjs)(?:\?.*)?$/, route => route.fulfill({ contentType: 'application/javascript', body: `export class DiscordSDK { clientId='1463798242981445672';customId='';instanceId='lobby-world';channelId='111111111111111111';guildId='222222222222222222';async ready(){};commands={authorize:async()=>({code:'mock'}),authenticate:async()=>({user:{id:'${uid}'}})}; }` }));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const data = path === '/api/config' ? { discord_client_id: '1463798242981445672', noname_available: noname }
      : path === '/api/token' ? { access_token: 'mock' }
      : path === '/api/profile' ? { success: true, user_id: uid, username: '云间道友', avatar_url: '/ui/player-avatar.svg', balance: 2048 }
      : path === '/api/tables/leaderboard' ? { entries: [], self: null, timezone: 'Asia/Shanghai', period: 'today', game_type: 'all' }
      : path === '/api/rooms' ? { rooms: [], total: 0 }
      : path === '/api/tables/history' ? { entries: [], total: 0, has_more: false }
      : path === '/api/tables/stats' ? { stats: { rounds: 0, wins: 0, losses: 0, draws: 0, net_profit: 0, win_rate: 0, games: [] }, timezone: 'Asia/Shanghai' }
      : { success: true };
    return route.fulfill({ json: data });
  });
  await page.goto(embedded ? '/?frame_id=lobby-world' : `/?dev_user_id=${uid}`);
  await expect(page.locator('.lobby-world-place')).toHaveCount(8);
  await expect(page.locator('.lobby-world')).toHaveClass(/lobby-world-art-ready/);
  await expect.poll(() => page.locator('.lobby-world-token img').evaluateAll(images => images.length === 8 && images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
}
for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 600 }, { width: 844, height: 390 }, { width: 390, height: 844 }]) {
  test(`洞府大厅入口与安全区 ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport); await openWorld(page, false, true);
    for (const name of names) await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
    const safeWidth = viewport.width > viewport.height ? viewport.width - 64 : viewport.width;
    const safeHeight = viewport.width > viewport.height ? viewport.height : viewport.height - 64;
    const metrics = await page.evaluate(() => {
      const bound = (element: Element) => { const r = element.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; };
      const world = document.querySelector('.lobby-world') as HTMLElement;
      return { root: bound(world), buttons: Array.from(world.querySelectorAll<HTMLButtonElement>('button')).map(button => ({ name: button.getAttribute('aria-label') || button.textContent, ...bound(button) })),
        places: Array.from(world.querySelectorAll<HTMLButtonElement>('.lobby-world-place')).map(place => { const r = place.getBoundingClientRect(); const at = document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); return Boolean(at && (at===place || place.contains(at))); }) };
    });
    expect(metrics.root.width).toBe(safeWidth); expect(metrics.root.height).toBe(safeHeight);
    for (const button of metrics.buttons) {
      expect(button.x, `${button.name}左侧`).toBeGreaterThanOrEqual(-1); expect(button.y, `${button.name}顶部`).toBeGreaterThanOrEqual(-1);
      expect(button.right, `${button.name}右侧灰条`).toBeLessThanOrEqual(safeWidth+1); expect(button.bottom, `${button.name}底部灰条`).toBeLessThanOrEqual(safeHeight+1);
    }
    expect(metrics.places.every(Boolean)).toBe(true);
    await page.getByRole('button', { name: '排行榜', exact: true }).click();
    await expect(page.getByRole('heading', { name: '盈利排行榜', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '关闭统计面板' }).click();
    await page.screenshot({ path: fileURLToPath(new URL(`../../../../../../output/farm-v2/lobby-world-${viewport.width}x${viewport.height}.png`, import.meta.url)) });
  });
}
test('场景保留头像统计、声音、房间列表和可选三国杀', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 }); await openWorld(page, true, true);
  await expect(page.getByRole('button', { name: /三国杀/ })).toBeVisible();
  await page.getByRole('button', { name: '查看个人信息与统计' }).click();
  await expect(page.getByRole('heading', { name: '个人统计', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '关闭统计面板' }).click();
  await page.getByRole('button', { name: '声音设置', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '声音设置' })).toBeVisible();
  await page.getByRole('dialog', { name: '声音设置' }).getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '房间列表', exact: true }).click();
  await expect(page.getByRole('heading', { name: '房间列表', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '关闭房间列表' }).click();
  await page.getByRole('button', { name: '21点', exact: true }).click();
  await expect(page.getByRole('heading', { name: '21点 · 选个座位' })).toBeVisible();
});
test('场景入口进入五种牌桌选择页', async ({ page }) => {
  await openWorld(page);
  for (const name of ['德州扑克', '斗地主', '四川麻将', '炸金花', '掼蛋']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.locator('.table-games')).toBeVisible();
    await page.getByRole('button', { name: /返回大厅/ }).click();
    await expect(page.locator('.lobby-world')).toBeVisible();
  }
});
test('场景减少动态偏好和后台停动画', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await openWorld(page);
  expect(await page.locator('.lobby-world-qi i').first().evaluate(element => getComputedStyle(element).animationName)).toBe('none');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect(page.locator('.lobby-world')).toHaveClass(/lobby-world-paused/);
  expect(await page.locator('.lobby-world-qi i').first().evaluate(element => getComputedStyle(element).animationPlayState)).toBe('paused');
});


test('窄横屏可选三国杀入口避开顶部控件', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 }); await openWorld(page, true, true);
  const entry = page.getByRole('button', { name: /三国杀/ });
  await expect(entry).toBeVisible();
  const accessible = await entry.evaluate(element => { const r = element.getBoundingClientRect(); const at = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return Boolean(at && (at === element || element.contains(at))); });
  expect(accessible).toBe(true);
  await page.screenshot({ path: fileURLToPath(new URL('../../../../../../output/farm-v2/lobby-world-noname-844x390.png', import.meta.url)) });
});
