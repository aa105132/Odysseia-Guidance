import { expect, test, type Locator, type Page } from '@playwright/test';

const userId = '123456789012345678';
const games = [
  { type: 'texas', name: '德州扑克' },
  { type: 'golden_flower', name: '炸金花' },
  { type: 'landlord', name: '斗地主' },
  { type: 'guandan', name: '掼蛋' },
  { type: 'mahjong', name: '四人麻将' },
  { type: 'sichuan_mahjong', name: '四人麻将' },
];

async function setup(page: Page, game: typeof games[number], balance = 1_000_000) {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const room = {
    room_id: 'CAPS20', game_type: game.type, host_user_id: userId, state: 'waiting', revision: 1,
    mode: 'multi', include_yueyue: true, min_players: 2, max_players: 4, game: null,
    room_tier: 'custom', base_stake: 1, entry_min: 100, loss_limit: 100,
    turn_timeout_seconds: 60, auto_start_when_ready: false,
    players: [{ user_id: userId, username: '上限测试房主', avatar_url: '', is_bot: false, is_ready: false, connected: true }],
  };
  let created = false;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: userId, username: '上限测试房主', avatar_url: '', balance } });
    if (path.startsWith('/api/game-social/')) return route.fulfill({ json: { success: true, events: [], cursor: 0, catalog: { chat: [], interaction: [] } } });
    if (route.request().method() === 'POST' && (path.endsWith('/create') || path.endsWith('/settings'))) {
      const body = route.request().postDataJSON();
      posts.push({ path, body });
      if (path.endsWith('/create')) created = true;
      if (body.base_stake != null) room.base_stake = body.base_stake;
      if (body.loss_limit != null) room.loss_limit = room.entry_min = body.loss_limit;
      room.revision++;
    }
    return route.fulfill({ json: { success: true, room: created ? room : null, viewer_balance: balance } });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/?dev_user_id=${userId}`);
  await page.getByRole('button', { name: new RegExp(`^${game.name}`) }).click();
  if (game.type === 'sichuan_mahjong') await page.getByRole('button', { name: '四川血战', exact: true }).click();
  await page.getByRole('button', { name: '自定义房间', exact: true }).click();
  return { posts, room };
}

async function checkHardCaps(dialog: Locator, submitName: string) {
  const base = dialog.getByLabel('底分', { exact: true });
  const limit = dialog.getByLabel('单局最多输', { exact: true });
  const submit = dialog.getByRole('button', { name: submitName, exact: true });
  await expect(base).toHaveAttribute('max', '20');
  await expect(limit).toHaveAttribute('max', '2000');
  await expect(dialog).toContainText('底分 1–20 · 每人单局最多输 2000 灵石。');
  await base.fill('21');
  await limit.fill('2000');
  await expect(base).toHaveValue('21');
  await expect(submit).toBeDisabled();
  await base.fill('20');
  await limit.fill('2001');
  await expect(limit).toHaveValue('2001');
  await expect(submit).toBeDisabled();
  await limit.fill('2000');
  await expect(submit).toBeEnabled();
}

for (const game of games) {
  test(`${game.type} 高余额建房和修改仍受20/2000限制，边界值可提交`, async ({ page }) => {
    const { posts } = await setup(page, game);
    const custom = page.getByRole('dialog', { name: '自定义房间', exact: true });
    await checkHardCaps(custom, '创建自定义房间');
    expect(posts).toHaveLength(0);
    await custom.getByRole('button', { name: '创建自定义房间', exact: true }).click();
    await expect(custom).not.toBeVisible();
    expect(posts[0]).toMatchObject({ path: '/api/tables/create', body: { game_type: game.type, base_stake: 20, loss_limit: 2000 } });

    await page.getByRole('button', { name: '房间设置', exact: true }).click();
    const settings = page.getByRole('dialog', { name: '房间设置', exact: true });
    await settings.getByLabel('底分', { exact: true }).fill('19');
    await settings.getByLabel('单局最多输', { exact: true }).fill('1900');
    await settings.getByRole('button', { name: '保存设置', exact: true }).click();
    await expect(settings).not.toBeVisible();
    await page.getByRole('button', { name: '房间设置', exact: true }).click();
    await checkHardCaps(settings, '保存设置');
    expect(posts).toHaveLength(2);
    await settings.getByRole('button', { name: '保存设置', exact: true }).click();
    await expect(settings).not.toBeVisible();
    expect(posts[2]).toEqual({ path: '/api/tables/settings', body: { room_id: 'CAPS20', base_stake: 20, loss_limit: 2000 } });
  });
}

test('自定义房间仍校验整数、最低100、十倍底分和实际余额', async ({ page }) => {
  const { posts } = await setup(page, games[0]!, 150);
  const custom = page.getByRole('dialog', { name: '自定义房间', exact: true });
  const base = custom.getByLabel('底分', { exact: true });
  const limit = custom.getByLabel('单局最多输', { exact: true });
  const submit = custom.getByRole('button', { name: '创建自定义房间', exact: true });
  await expect(limit).toHaveAttribute('max', '150');
  for (const values of [{ base: '1', limit: '99' }, { base: '16', limit: '150' },
    { base: '1', limit: '151' }, { base: '1.5', limit: '150' }, { base: '1', limit: '100.5' }]) {
    await base.fill(values.base);
    await limit.fill(values.limit);
    await expect(submit).toBeDisabled();
    await expect(base).toHaveValue(values.base);
    await expect(limit).toHaveValue(values.limit);
  }
  expect(posts).toHaveLength(0);
  await base.fill('15');
  await limit.fill('150');
  await submit.click();
  await expect(custom).not.toBeVisible();
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const settings = page.getByRole('dialog', { name: '房间设置', exact: true });
  await settings.getByLabel('单局最多输', { exact: true }).fill('151');
  await expect(settings.getByRole('button', { name: '保存设置', exact: true })).toBeDisabled();
  await settings.getByLabel('底分', { exact: true }).fill('16');
  await settings.getByLabel('单局最多输', { exact: true }).fill('150');
  await expect(settings.getByRole('button', { name: '保存设置', exact: true })).toBeDisabled();
  expect(posts).toHaveLength(1);
});
