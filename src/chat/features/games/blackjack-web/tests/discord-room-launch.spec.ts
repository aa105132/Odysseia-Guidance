import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';
async function setup(page: Page, game: string, customId: string, options: { failJoin?: boolean; dm?: boolean } = {}) {
  const calls: { path: string; body: any }[] = [];
  await page.route(/.*(?:@discord_embedded-app-sdk|@discord\/embedded-app-sdk).*\.(?:js|mjs)(?:\?.*)?$/, async route => {
    await route.fulfill({ contentType: 'application/javascript', body: `
      export class DiscordSDK {
        clientId='1463798242981445672'; customId=${JSON.stringify(customId)}; instanceId='instance-test'; channelId='111111111111111111'; guildId=${options.dm ? 'null' : "'222222222222222222'"};
        async ready() {}
        commands={authorize: async()=>({code:'mock-code'}), authenticate:async()=>({user:{id:'${uid}'}}),shareLink:async(args)=>{window.__shared=args; return {success:true,didCopyLink:false,didSendMessage:true}}};
      }` });
  });
  const member = { user_id: uid, username: '受邀玩家', avatar_url: '/character/normal.webp', is_bot: false, is_ready: false, connected: true, seat_index: 0, bet_amount: 0, hand: [], score: 0, status: 'waiting', result: null, payout_amount: 0, is_current_turn: false };
  const room = game === 'blackjack' ? { room_id: 'ROOM01', host_user_id: uid, max_players: 3, state: 'waiting', current_turn_user_id: null, ready_player_count: 0, all_players_ready: false, players: [member], dealer: { name: '月月', hand: [], score: 0, avatar_path: '/character/normal.webp' } } : { room_id: 'ROOM01', game_type: game, mode: 'multi', state: 'waiting', host_user_id: uid, players: [member], game: null, min_players: 4, max_players: 4, room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100, revision: 1, include_yueyue: false };
  await page.addInitScript(() => localStorage.setItem('yueyue.table.mahjong.123456789012345678', 'OLD123'));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    calls.push({ path, body: route.request().postDataJSON() });
    if (path === '/api/config') return route.fulfill({ json: { discord_client_id: '1463798242981445672' } });
    if (path === '/api/token') return route.fulfill({ json: { access_token: 'mock-token' } });
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '受邀玩家', avatar_url: '/character/normal.webp', balance: 1000 } });
    if (options.failJoin && path.endsWith('/join')) return route.fulfill({ status: 400, json: { detail: '房间已关闭' } });
    return route.fulfill({ json: { success: true, room, viewer_balance: 1000 } });
  });
  await page.goto('/?frame_id=test-frame&platform=desktop');
  return calls;
}

for (const game of ['blackjack', 'sichuan_mahjong']) {
  test(`Discord 邀请自动进入 ${game}，优先于旧房间且不下注`, async ({ page }) => {
    const calls = await setup(page, game, `room:${game}:ROOM01`);
    await expect(page.locator(game === 'blackjack' ? '.multi-mode-view' : '.tg-scroll')).toBeVisible();
    const joins = calls.filter(call => call.path.endsWith('/join'));
    expect(joins).toHaveLength(1);
    expect(joins[0]!.body).toEqual({ room_id: 'ROOM01' });
    expect(calls.some(call => /\/(auto-join|create|start|bet|action)$/.test(call.path))).toBe(false);
  });
  test(`Discord ${game} 邀请已失效时回大厅提示`, async ({ page }) => {
    await setup(page, game, `room:${game}:ROOM01`, { failJoin: true });
    await expect(page.locator('.game-hub-panel')).toBeVisible();
    await expect(page.getByRole('alert')).toContainText('房间已关闭');
  });
}

for (const dm of [true, false]) {
  test(`Discord ${dm ? '私聊' : '频道'}招募通过原生分享携带指定房间`, async ({ page }) => {
    const calls = await setup(page, 'blackjack', 'room:blackjack:ROOM01', { dm });
    await page.getByRole('button', { name: '招募队友', exact: true }).click();
    await page.getByRole('button', { name: '选择好友或频道', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('已通过 Discord 分享邀请');
    expect(await page.evaluate(() => (window as any).__shared)).toEqual({ message: '来月月茶楼一起玩！房间号 ROOM01', custom_id: 'room:blackjack:ROOM01' });
    expect(calls.some(call => call.path.endsWith('/recruit'))).toBe(false);
  });
}

test('无效邀请不触发任何自动入座', async ({ page }) => {
  const calls = await setup(page, 'blackjack', 'room:unknown:ROOM01');
  await expect(page.locator('.game-hub-panel')).toBeVisible();
  expect(calls.some(call => call.path.endsWith('/join'))).toBe(false);
});
