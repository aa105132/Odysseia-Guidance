import { expect, test, type Locator, type Page } from '@playwright/test';

const uid = '123456789012345678';
async function setup(page: Page, roomLaunch = false) {
  await page.route(/.*(?:@discord_embedded-app-sdk|@discord\/embedded-app-sdk).*\.(?:js|mjs)(?:\?.*)?$/, route => route.fulfill({ contentType: 'application/javascript', body: `export class DiscordSDK { clientId='1463798242981445672';customId='${roomLaunch ? 'room:texas:VIEW01' : ''}';instanceId='view';channelId='111111111111111111';guildId='222222222222222222';async ready(){};commands={authorize:async()=>({code:'mock'}),authenticate:async()=>({user:{id:'${uid}'}})}; }` }));
  const room = { room_id: 'VIEW01', game_type: 'texas', mode: 'multi', state: 'waiting', host_user_id: uid, players: [{ user_id: uid, username: '手机牌友', avatar_url: '/character/normal.webp', is_bot: false, is_ready: false, connected: true }], game: null, min_players: 2, max_players: 8, room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100, revision: 1, include_yueyue: false, auto_start_when_ready: false, turn_timeout_seconds: 60 };
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/config') return route.fulfill({ json: { discord_client_id: '1463798242981445672' } });
    if (path === '/api/token') return route.fulfill({ json: { access_token: 'mock' } });
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '手机牌友', avatar_url: '/character/normal.webp', balance: 1000 } });
    if (path === '/api/tables/leaderboard') return route.fulfill({ json: { entries: [], self: null, legacy_rounds: 0, timezone: 'Asia/Shanghai' } });
    if (path.startsWith('/api/game-social')) return route.fulfill({ json: { cursor: 0, events: [] } });
    return route.fulfill({ json: { success: true, room, viewer_balance: 1000 } });
  });
  await page.goto('/?frame_id=viewport-test&platform=mobile');
}

async function insideContent(locator: Locator, width: number, height: number) {
  await expect(locator).toBeVisible();
  const box = (await locator.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(-1);
  expect(box.y).toBeGreaterThanOrEqual(-1);
  expect(box.x + box.width).toBeLessThanOrEqual(width - 64 + 1);
  expect(box.y + box.height).toBeLessThanOrEqual(height + 1);
}

for (const [width, height] of [[390, 844], [844, 390], [568, 320]]) {
  test(`嵌入${width}×${height}大厅自动适配，排行榜和声音弹窗可真实点击`, async ({ page }) => {
    await page.setViewportSize({ width: width!, height: height! });
    await setup(page);
    const rotated = height! > width!;
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.activityRotated)).toBe(String(rotated));
    const expected = { width: rotated ? height : width! - 64, height: rotated ? width! - 64 : height };
    expect(await page.locator('#app').evaluate(element => ({ width: element.clientWidth, height: element.clientHeight }))).toEqual(expected);
    await expect(page.getByText(/请.*横屏|请.*旋转/)).toHaveCount(0);
    const board = page.getByRole('button', { name: /排行榜.*当日盈利/ });
    await board.scrollIntoViewIfNeeded();
    await insideContent(board, width!, height!);
    await board.click();
    const ranking = page.getByRole('dialog', { name: '盈利排行榜' });
    await insideContent(ranking, width!, height!);
    await ranking.getByRole('button', { name: '总计盈利', exact: true }).click();
    await ranking.getByRole('button', { name: '关闭统计面板' }).click();
    await page.getByRole('button', { name: '声音设置', exact: true }).click();
    const sound = page.getByRole('dialog', { name: '声音设置' });
    await insideContent(sound, width!, height!);
    const checkbox = sound.getByRole('checkbox', { name: /背景音乐/ });
    await checkbox.check();
    await expect(checkbox).toBeChecked();
    await sound.getByRole('button', { name: '关闭', exact: true }).click();
    await page.screenshot({ path: `../../../../../tmp/activity-lobby-${width}.png` });
  });
}

test('竖屏房间设置独立旋转可操作，横竖resize保留同一房间', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, true);
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const settings = page.getByRole('dialog', { name: '房间设置' });
  await insideContent(settings, 390, 844);
  const timeout = settings.getByRole('spinbutton', { name: /操作等待时长/ });
  await timeout.fill('120');
  await expect(timeout).toHaveValue('120');
  await settings.getByRole('button', { name: '关闭房间设置' }).click();
  await page.setViewportSize({ width: 844, height: 390 });
  await expect(page.getByRole('button', { name: '复制房间号 VIEW01' })).toBeVisible();
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  await insideContent(settings, 844, 390);
  await settings.getByRole('button', { name: '关闭房间设置' }).click();
  const mapped = await page.evaluate(async () => {
    const viewport = await import('/src/activityViewport.ts' as string);
    return viewport.clientPointToActivity(100, 50);
  });
  expect(mapped).toEqual({ x: 100, y: 50 });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.activityRotated)).toBe('true');
  const rotated = await page.evaluate(async () => {
    const viewport = await import('/src/activityViewport.ts' as string);
    return viewport.clientPointToActivity(100, 50);
  });
  expect(rotated).toEqual({ x: 50, y: 226 });
  await page.screenshot({ path: '../../../../../tmp/activity-room-portrait.png' });
});
