import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';
const profile = { success: true, user_id: uid, username: '找桌玩家', avatar_url: '/character/normal.webp', balance: 1000 };
function listed(room_id: string, game_type = 'texas', extra: Record<string, unknown> = {}) {
  return { room_id, game_type, host_username: '很长的房主名字用于验证列表不挤出屏幕', player_count: 1, max_players: 4, state: 'waiting', can_join: true, room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100, is_member: false, ...extra };
}
type Listed = ReturnType<typeof listed>;
function tableRoom(room_id: string, game_type = 'texas') {
  return { room_id, game_type, host_user_id: uid, state: 'waiting', revision: 1, mode: 'multi', include_yueyue: false, min_players: 2, max_players: 4, stake: 100, buy_in: 100, room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100, settlement_status: 'none', game: null, players: [{ user_id: uid, username: profile.username, avatar_url: profile.avatar_url, is_bot: false, is_ready: false, connected: true }] };
}
function blackjackRoom(room_id: string) {
  return { room_id, host_user_id: uid, max_players: 3, state: 'waiting', current_turn_user_id: null, ready_player_count: 0, all_players_ready: false, dealer: { name: '月月', avatar_path: '/character/normal.webp', expression: 'normal', hand: [], score: 0 }, players: [{ user_id: uid, username: profile.username, avatar_url: profile.avatar_url, seat_index: 0, bet_amount: 0, hand: [], score: 0, status: 'waiting', result: null, payout_amount: 0, is_ready: false, is_current_turn: false, is_bot: false }] };
}
async function setup(page: Page, rooms: Listed[], options: { joinError?: string; listError?: boolean; total?: number } = {}) {
  const calls: { path: string; body: any; query: string }[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    calls.push({ path, body: route.request().postDataJSON(), query: url.search });
    if (path === '/api/profile') return route.fulfill({ json: profile });
    if (path === '/api/rooms') {
      if (options.listError) return route.fulfill({ status: 503, json: { detail: '房间列表服务暂时不可用' } });
      const game = url.searchParams.get('game_type');
      const result = rooms.filter(room => !game || room.game_type === game);
      return route.fulfill({ json: { rooms: result, total: options.total ?? result.length } });
    }
    if (path === '/api/tables/join' || path === '/api/multi/room/join') {
      if (options.joinError) return route.fulfill({ status: 409, json: { detail: options.joinError } });
      const id = route.request().postDataJSON().room_id;
      const game = rooms.find(room => room.room_id === id)?.game_type ?? 'texas';
      return route.fulfill({ json: { success: true, room: game === 'blackjack' ? blackjackRoom(id) : tableRoom(id, game), viewer_balance: profile.balance } });
    }
    if (path.startsWith('/api/tables/') || path.startsWith('/api/multi/room/')) {
      const id = path.split('/').at(-1)!;
      const game = rooms.find(room => room.room_id === id)?.game_type ?? 'texas';
      return route.fulfill({ json: { success: true, room: game === 'blackjack' ? blackjackRoom(id) : tableRoom(id, game), viewer_balance: profile.balance } });
    }
    return route.fulfill({ status: 404, json: { detail: `未模拟接口 ${path}` } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  return calls;
}

test('大厅列表筛选、满员、对局中与灵石不足状态', async ({ page }) => {
  const calls = await setup(page, [listed('OPEN01'), listed('BJ0001', 'blackjack'), listed('FULL01', 'texas', { can_join: false, player_count: 4 }), listed('PLAY01', 'mahjong', { can_join: false, state: 'playing' }), listed('RICH01', 'texas', { entry_min: 5000 })]);
  await page.getByRole('button', { name: '房间列表', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间列表' });
  await expect(dialog.locator('.directory-room')).toHaveCount(5);
  await expect(dialog.getByRole('button', { name: '已满员 FULL01', exact: true })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: '对局中 PLAY01', exact: true })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: '灵石不足 RICH01', exact: true })).toBeDisabled();
  await dialog.getByLabel('筛选房间玩法').selectOption('blackjack');
  await expect(dialog.locator('.directory-room')).toHaveCount(1);
  await expect(dialog).toContainText('BJ0001');
  expect(calls.filter(call => call.path === '/api/rooms').at(-1)!.query).toContain('game_type=blackjack');
  await dialog.getByLabel('筛选房间玩法').selectOption('');
  await expect(dialog.locator('.directory-room')).toHaveCount(5);
  await dialog.getByLabel('只看可加入').check();
  await expect(dialog.locator('[data-room-id="FULL01"]')).toHaveCount(0);
  await expect(dialog.locator('[data-room-id="PLAY01"]')).toHaveCount(0);
  await expect(dialog.locator('[data-room-id="RICH01"]')).toHaveCount(0);
  await page.getByRole('button', { name: '关闭房间列表' }).click();
  await expect(dialog).toHaveCount(0);
  expect(calls.filter(call => call.path.endsWith('/join'))).toHaveLength(0);
});

for (const game of ['blackjack', 'landlord', 'mahjong', 'sichuan_mahjong']) {
  test(`大厅直接入座 ${game}，只提交加入请求`, async ({ page }) => {
    await page.setViewportSize({ width: 568, height: 320 });
    const calls = await setup(page, [listed('JOIN01', game)]);
    await page.getByRole('button', { name: '房间列表', exact: true }).click();
    await page.getByRole('button', { name: '入座 JOIN01', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '房间列表' })).toHaveCount(0);
    await expect(page.locator(game === 'blackjack' ? '.multi-mode-view' : '.tg-scroll')).toBeVisible();
    for (const control of await page.locator('.table-toolbar button, .tg-toolbar button').all()) {
      await expect(control).toBeInViewport({ ratio: 1 });
      await control.click({ trial: true });
    }
    await page.getByRole('button', { name: '招募队友', exact: true }).click();
    const invitation = page.getByRole('dialog', { name: '招募队友' });
    await expect(invitation).toBeInViewport({ ratio: 1 });
    await expect(invitation).toContainText('JOIN01');
    await invitation.getByRole('button', { name: '关闭', exact: true }).click();
    const joins = calls.filter(call => call.path.endsWith('/join'));
    expect(joins).toHaveLength(1);
    expect(joins[0]!.path).toBe(game === 'blackjack' ? '/api/multi/room/join' : '/api/tables/join');
    expect(joins[0]!.body).toEqual({ room_id: 'JOIN01' });
    expect(calls.filter(call => /\/(bet|ready|start|action)$/.test(call.path))).toHaveLength(0);
  });
}

test('入座失败保留列表并显示服务端原因', async ({ page }) => {
  await setup(page, [listed('GONE01', 'blackjack')], { joinError: '房间刚刚已满员，请选择其他房间' });
  await page.getByRole('button', { name: '房间列表', exact: true }).click();
  await page.getByRole('button', { name: '入座 GONE01', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间列表' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('alert')).toContainText('房间刚刚已满员');
  await expect(dialog.getByRole('button', { name: '入座 GONE01', exact: true })).toBeEnabled();
});

test('空列表、刷新失败与分页控件都有明确反馈', async ({ page }) => {
  const options = { listError: false, total: 101 };
  const calls = await setup(page, [], options);
  await page.getByRole('button', { name: '房间列表', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间列表' });
  await expect(dialog.getByRole('status')).toContainText('暂时没有合适的房间');
  await expect(dialog.getByRole('button', { name: '上一页', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(dialog).toContainText('第 2 页');
  await expect(dialog.getByRole('button', { name: '下一页', exact: true })).toBeDisabled();
  expect(calls.filter(call => call.path === '/api/rooms').at(-1)!.query).toContain('offset=100');
  options.listError = true;
  await dialog.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('房间列表服务暂时不可用');
});

test('玩法大厅限定房间类型，窄横屏弹窗可滚动且控件可点击', async ({ page }) => {
  await page.setViewportSize({ width: 568, height: 320 });
  const calls = await setup(page, Array.from({ length: 12 }, (_, index) => listed(`TEX${String(index).padStart(3, '0')}`)));
  await page.getByRole('button', { name: /^德州扑克/ }).click();
  await page.getByRole('button', { name: '房间列表', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间列表' });
  await expect(dialog.locator('.directory-room')).toHaveCount(12);
  await expect(dialog.getByLabel('筛选房间玩法')).toBeDisabled();
  await expect(dialog.getByLabel('筛选房间玩法')).toHaveValue('texas');
  expect(calls.filter(call => call.path === '/api/rooms').at(-1)!.query).toContain('game_type=texas');
  await expect(dialog).toBeInViewport({ ratio: 1 });
  for (const control of await dialog.locator('.directory-heading button, .directory-filters button, .directory-filters input').all()) {
    await expect(control).toBeInViewport({ ratio: 1 });
    await control.click({ trial: true });
  }
  await dialog.locator('.directory-room').last().scrollIntoViewIfNeeded();
  await dialog.getByRole('button', { name: '入座 TEX011', exact: true }).click({ trial: true });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
