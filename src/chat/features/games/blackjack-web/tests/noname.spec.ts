import { expect, test } from '@playwright/test';

test('三国杀入口、许可确认和缺少资源提示', async ({ page }) => {
  await page.setViewportSize({ width: 568, height: 320 });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '牌友', balance: 100, avatar_url: '/character/normal.webp' } });
    if (path === '/api/noname/status') return route.fulfill({ json: { available: false } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: '三国杀 无名杀 · 娱乐试玩' }).click();
  await expect(page.getByRole('button', { name: '单机试玩' })).toBeDisabled();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '单机试玩' }).click();
  await expect(page.getByRole('alert')).toContainText('资源正在准备');
  await expect(page.locator('iframe')).toHaveCount(0);
  await page.getByRole('button', { name: '返回大厅' }).click();
  await expect(page.getByRole('button', { name: '21点 立即游玩' })).toBeVisible();
});

test('隔离嵌入子游戏，只发昵称与按需票据，不传身份令牌', async ({ page }) => {
  let tickets = 0;
  const calls: string[] = [];
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    calls.push(path);
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '牌友', balance: 100, avatar_url: '/character/normal.webp' } });
    if (path === '/api/noname/status') return route.fulfill({ json: { available: true } });
    if (path === '/api/noname/session') { tickets++; return route.fulfill({ json: { ticket: 'one-use', expires_in: 60 } }); }
    return route.fulfill({ json: {} });
  });
  await page.route('**/noname/', route => route.fulfill({ contentType: 'text/html', body: `<script>window.received=[];addEventListener('message',e=>{received.push(e.data);if(e.data.type==='noname-launch')parent.postMessage({type:'noname-ticket',request:'1'},location.origin)});parent.postMessage({type:'noname-ready'},location.origin);</script>` }));
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: '三国杀 无名杀 · 娱乐试玩' }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '好友联机' }).click();
  await expect.poll(() => tickets).toBe(1);
  const frame = page.frames()[1]!;
  const received = await frame.evaluate(() => (window as any).received);
  expect(received[0]).toEqual({ type: 'noname-launch', username: '牌友', mode: 'online' });
  expect(calls.some(path => /\/(bet|start|ready|action)$/.test(path))).toBe(false);
  await frame.evaluate(() => parent.postMessage({ type: 'noname-state', phase: 'lobby', roomId: '', host: false, players: [], capacity: 8 }, location.origin));
  await expect(page.getByRole('button', { name: '创建房间', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '创建房间', exact: true }).click();
  await expect.poll(() => frame.evaluate(() => (window as any).received.at(-1))).toMatchObject({ type: 'noname-command', action: 'create', preset: 'classic', capacity: 8 });
  await frame.evaluate(() => parent.postMessage({ type: 'noname-state', phase: 'waiting', roomId: 'sample-room', host: true, players: ['牌友'], capacity: 8 }, location.origin));
  await expect(page.getByRole('button', { name: '开始游戏', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '复制房间号 sample-room' })).toBeVisible();
  await frame.evaluate(() => parent.postMessage({ type: 'noname-state', phase: 'waiting', roomId: 'sample-room', host: true, players: ['牌友', '朋友'], capacity: 8 }, location.origin));
  await expect(page.getByRole('button', { name: '开始游戏', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '开始游戏', exact: true }).click();
  await expect.poll(() => frame.evaluate(() => (window as any).received.at(-1))).toMatchObject({ action: 'start' });
  await frame.evaluate(() => parent.postMessage({ type: 'noname-state', phase: 'playing', roomId: 'sample-room', host: true, players: [], capacity: 8 }, location.origin));
  await expect(page.locator('iframe')).toBeVisible();
});
