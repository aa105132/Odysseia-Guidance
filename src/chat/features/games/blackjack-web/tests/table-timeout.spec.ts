import { expect, test, type Page } from '@playwright/test';

const host = '123456789012345678';
const guest = '223456789012345678';

async function setup(page: Page, viewer = host) {
  const room = {
    room_id: 'TIMEOUT', game_type: 'texas', host_user_id: host, state: 'waiting', revision: 1,
    mode: 'multi', include_yueyue: true, min_players: 2, max_players: 8, game: null,
    room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
    turn_timeout_seconds: 60, auto_start_when_ready: false,
    players: [
      { user_id: host, username: '房主', avatar_url: '', is_bot: false, is_ready: false, connected: true },
      { user_id: guest, username: '访客', avatar_url: '', is_bot: false, is_ready: false, connected: true },
    ],
  };
  const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: viewer, username: '测试玩家', avatar_url: '', balance: 2000 } });
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      requests.push({ path, body });
      if (body.turn_timeout_seconds != null) room.turn_timeout_seconds = body.turn_timeout_seconds;
      room.revision++;
    }
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/?dev_user_id=${viewer}`);
  await page.getByRole('button', { name: /^德州扑克/ }).click();
  return { room, requests };
}

test('建房携带所选等待时长，房主修改后提交秒数', async ({ page }) => {
  const { requests } = await setup(page);
  await page.getByRole('button', { name: '入席设置', exact: true }).click();
  await page.locator('#create-turn-seconds').selectOption('120');
  await page.getByRole('button', { name: '关闭入席设置' }).click();
  await page.getByRole('button', { name: '好友同桌', exact: true }).click();
  expect(requests[0]?.body.turn_timeout_seconds).toBe(120);
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间设置', exact: true });
  await expect(dialog.getByLabel('操作等待时长（秒）')).toHaveValue('120');
  await dialog.getByLabel('操作等待时长（秒）').fill('137');
  await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
  expect(requests.at(-1)).toEqual({ path: '/api/tables/settings', body: { room_id: 'TIMEOUT', turn_timeout_seconds: 137 } });
});

test('单人建房使用相同时长设置', async ({ page }) => {
  const { requests } = await setup(page);
  await page.getByRole('button', { name: '入席设置', exact: true }).click();
  await page.locator('#create-turn-seconds').selectOption('15');
  await page.getByRole('button', { name: '关闭入席设置' }).click();
  await page.getByRole('button', { name: '月月陪玩', exact: true }).click();
  expect(requests[0]?.body).toMatchObject({ mode: 'solo', turn_timeout_seconds: 15 });
});

test('访客只能查看房间操作等待时长', async ({ page }) => {
  await setup(page, guest);
  await page.getByRole('button', { name: '入席设置', exact: true }).click();
  await page.locator('#create-turn-seconds').selectOption('90');
  await page.getByRole('button', { name: '关闭入席设置' }).click();
  await page.getByRole('button', { name: '好友同桌', exact: true }).click();
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '房间设置', exact: true });
  await expect(dialog).toContainText('操作等待时长90 秒');
  await expect(dialog.getByLabel('操作等待时长（秒）')).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: '保存设置', exact: true })).toHaveCount(0);
});
