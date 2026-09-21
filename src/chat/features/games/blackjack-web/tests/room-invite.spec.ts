import { expect, test, type Page } from '@playwright/test';
import { parseRoomInvite, roomInviteUrl } from '../src/roomInvites';

const uid = '123456789012345678';
const clientId = '987654321098765432';
const roomId = 'TEST01';

async function openRoom(page: Page, clipboardAllowed = true, configured = true) {
  await page.addInitScript(({ allowed }) => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async (value: string) => {
        if (!allowed) throw new Error('客户端禁止剪贴板');
        (window as unknown as { copiedText: string }).copiedText = value;
      },
    } });
  }, { allowed: clipboardAllowed });
  const actions: string[] = [];
  const room = {
    room_id: roomId, host_user_id: uid, max_players: 3, state: 'waiting', current_turn_user_id: null,
    ready_player_count: 0, all_players_ready: false,
    dealer: { name: '月月', avatar_path: '/character/normal.webp', expression: 'normal', hand: [], score: 0 },
    players: [{ user_id: uid, username: '邀请玩家', avatar_url: '/character/normal.webp', seat_index: 0,
      bet_amount: 0, hand: [], score: 0, status: 'waiting', result: null, payout_amount: 0,
      is_ready: false, is_current_turn: false, is_bot: false }],
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    actions.push(path);
    if (path === '/api/config') return route.fulfill({ json: { discord_client_id: configured ? clientId : '' } });
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '邀请玩家', avatar_url: '/character/normal.webp', balance: 1000 } });
    if (path === '/api/rooms') return route.fulfill({ json: { rooms: [{ room_id: roomId, game_type: 'blackjack', host_username: '邀请玩家', player_count: 1, max_players: 3, state: 'waiting', can_join: true, room_tier: 'custom', entry_min: 0, is_member: false }], total: 1 } });
    if (path.startsWith('/api/multi/room/')) return route.fulfill({ json: { success: true, room, viewer_balance: 1000 } });
    return route.fulfill({ status: 404, json: { detail: '未模拟接口' } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '房间列表', exact: true }).click();
  await page.getByRole('button', { name: `入座 ${roomId}`, exact: true }).click();
  await expect(page.locator('.multi-mode-view')).toBeVisible();
  return actions;
}

test('邀请载荷严格校验并生成 Discord 官方活动链接', () => {
  const room = { game_type: 'sichuan_mahjong', room_id: roomId };
  expect(parseRoomInvite('room:sichuan_mahjong:TEST01')).toEqual(room);
  expect(new URL(roomInviteUrl(clientId, room)).searchParams.get('custom_id')).toBe('room:sichuan_mahjong:TEST01');
  expect(roomInviteUrl(clientId, room)).toMatch(/^https:\/\/discord\.com\/activities\//);
  for (const value of [null, 'room:unknown:TEST01', 'room:blackjack:12345', 'room:blackjack:TEST01&evil=1', 'room:blackjack:ABCDEF\n']) expect(parseRoomInvite(value)).toBeNull();
  expect(() => roomInviteUrl('invalid', room)).toThrow('无法获取 Discord 活动信息');
});

test('房间号与招募链接可复制，邀请操作不下注', async ({ page }) => {
  const actions = await openRoom(page);
  await page.getByRole('button', { name: `复制房间号 ${roomId}`, exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { copiedText: string }).copiedText)).toBe(roomId);
  await page.getByRole('button', { name: '招募队友', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '招募队友' });
  await dialog.getByRole('button', { name: '复制邀请链接', exact: true }).click();
  await expect(dialog.getByRole('status')).toContainText('已复制邀请链接');
  await expect.poll(() => page.evaluate(() => (window as unknown as { copiedText: string }).copiedText)).toBe(roomInviteUrl(clientId, { game_type: 'blackjack', room_id: roomId }));
  await expect(dialog.getByRole('button', { name: '选择好友或频道' })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(actions.filter(path => /\/(bet|ready|start|action)$/.test(path))).toHaveLength(0);
});

test('剪贴板被拒绝时提供可选择的房间号与邀请链接', async ({ page }) => {
  await openRoom(page, false);
  await page.getByRole('button', { name: `复制房间号 ${roomId}`, exact: true }).click();
  await expect(page.getByRole('textbox', { name: '长按复制房间号' })).toHaveValue(roomId);
  await page.getByRole('button', { name: '招募队友', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '招募队友' });
  await dialog.getByRole('button', { name: '复制邀请链接', exact: true }).click();
  const field = dialog.getByRole('textbox', { name: '手动复制内容' });
  await expect(field).toHaveValue(roomInviteUrl(clientId, { game_type: 'blackjack', room_id: roomId }));
  await field.click();
  expect(await field.evaluate((element: HTMLInputElement) => element.selectionEnd! - element.selectionStart!)).toBe((await field.inputValue()).length);
});

test('活动 ID 缺失时说明原因且仍可复制房间号', async ({ page }) => {
  await openRoom(page, true, false);
  await page.getByRole('button', { name: '招募队友', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '招募队友' });
  await dialog.getByRole('button', { name: '复制邀请链接', exact: true }).click();
  await expect(dialog.getByRole('status')).toContainText('请复制房间号邀请');
  await dialog.getByRole('button', { name: '复制房间号', exact: true }).click();
  await expect(dialog.getByRole('status')).toContainText('已复制房间号');
});

test('窄横屏招募弹窗无横向溢出，所有按钮可点击', async ({ page }) => {
  await page.setViewportSize({ width: 568, height: 320 });
  await openRoom(page);
  await page.getByRole('button', { name: '招募队友', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '招募队友' });
  await expect(dialog).toBeInViewport({ ratio: 1 });
  for (const button of await dialog.getByRole('button').all()) await button.click({ trial: true });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
