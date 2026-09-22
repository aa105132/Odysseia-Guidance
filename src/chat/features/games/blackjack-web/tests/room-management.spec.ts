import { expect, test, type Page } from '@playwright/test';

const hostId = '123456789012345678';
const guestId = '223456789012345678';

function finishedRoom() {
  const players = [
    { user_id: hostId, username: '房主甲', is_bot: false, is_ready: false, connected: true, avatar_url: '/character/normal.webp' },
    { user_id: guestId, username: '玩家乙', is_bot: false, is_ready: true, connected: true, avatar_url: '/character/normal.webp' },
    { user_id: 'bot:1', username: '月月', is_bot: true, is_ready: true, connected: true, avatar_url: '/character/normal.webp' },
  ];
  return {
    room_id: 'MANAGE', game_type: 'texas', host_user_id: hostId, state: 'finished', revision: 1,
    mode: 'multi', include_yueyue: true, min_players: 2, max_players: 8,
    room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
    auto_start_when_ready: false, settlement_status: 'settled', actual_settlement: {}, players,
    game: {
      phase: 'showdown', finished: true, current_player_id: null, legal_actions: [], winners: [guestId],
      message: '本局结束', community_cards: ['Club2', 'Heart7', 'DiamondQ', 'SpadeA', 'ClubK'],
      players: players.map(player => ({ user_id: player.user_id, hand: [], hand_count: 2, stack: 100, folded: true })),
    },
  };
}

async function enter(page: Page, uid = hostId) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: /^德州扑克/ }).click();
  await page.getByRole('button', { name: /^好友同桌/ }).click();
}

test('结算后显示每位玩家准备状态，公共场房主可启用自动开局并踢出真人', async ({ page }) => {
  const room = finishedRoom();
  const requests: Array<{ path: string; body: unknown }> = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: hostId, username: '房主甲', avatar_url: '/character/normal.webp', balance: 2000 } });
    if (path.endsWith('/settings')) {
      const body = route.request().postDataJSON();
      requests.push({ path, body });
      room.auto_start_when_ready = body.auto_start_when_ready;
      room.revision++;
    }
    if (path.endsWith('/kick')) {
      const body = route.request().postDataJSON();
      requests.push({ path, body });
      room.players = room.players.filter(player => player.user_id !== body.target_user_id);
      room.revision++;
    }
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
  });
  await enter(page);
  await expect(page.getByLabel('房主甲的准备状态')).toHaveText('未准备');
  await expect(page.getByLabel('玩家乙的准备状态')).toHaveText('已准备');
  await expect(page.locator('.tg-folded')).toHaveCount(0);
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间设置', exact: true });
  await dialog.getByLabel('全员准备后自动开启游戏').check();
  await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
  expect(requests[0]).toEqual({ path: '/api/tables/settings', body: { room_id: 'MANAGE', auto_start_when_ready: true } });
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  await expect(dialog.getByLabel('全员准备后自动开启游戏')).toBeChecked();
  await expect(dialog.getByRole('button', { name: '踢出 房主甲', exact: true })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: '踢出 月月', exact: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: '踢出 玩家乙', exact: true }).click();
  expect(requests[1]).toEqual({ path: '/api/tables/kick', body: { room_id: 'MANAGE', target_user_id: guestId } });
  await expect(dialog.getByLabel('房间成员')).not.toContainText('玩家乙');
  await dialog.getByRole('button', { name: '关闭房间设置' }).click();
  await expect(page.locator('.tg-seat')).toHaveCount(2);
});

test('普通成员只能查看设置，被踢出后清理房间并返回大厅', async ({ page }) => {
  const room = finishedRoom();
  let removed = false;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: guestId, username: '玩家乙', avatar_url: '/character/normal.webp', balance: 2000 } });
    if (removed && route.request().method() === 'GET') return route.fulfill({ json: { success: true, room: null, room_exit_reason: 'kicked', viewer_balance: 2000 } });
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
  });
  await enter(page, guestId);
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间设置', exact: true });
  await expect(dialog.getByRole('button', { name: /^踢出/ })).toHaveCount(0);
  await expect(dialog.getByLabel('全员准备后自动开启游戏')).toHaveCount(0);
  await expect(dialog).toContainText('全员准备后自动开局：已关闭');
  await dialog.getByRole('button', { name: '关闭房间设置' }).click();
  removed = true;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(page.locator('.tg-lobby')).toBeVisible();
  await expect(page.locator('.table-games')).toContainText('你已被房主移出房间，已返回大厅');
  expect(await page.evaluate(() => localStorage.getItem('yueyue.table.texas.223456789012345678'))).toBeNull();
  // 模拟被踢通知抵达前刷新：旧房间号只用于同步，不能自动重新加入。
  await page.evaluate(() => localStorage.setItem('yueyue.table.texas.223456789012345678', 'MANAGE'));
  let joins = 0;
  page.on('request', request => { if (request.url().endsWith('/api/tables/join')) joins++; });
  await page.reload();
  await page.getByRole('button', { name: /^德州扑克/ }).click();
  await expect(page.locator('.tg-lobby')).toBeVisible();
  await expect(page.locator('.table-games')).toContainText('你已被房主移出房间，已返回大厅');
  expect(joins).toBe(0);
});
