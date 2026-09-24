import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';
const cardHands: Record<string, string[]> = {
  texas: ['ClubA', 'Spade10'],
  golden_flower: ['ClubA', 'Spade10', 'Heart3'],
  landlord: ['Club3', 'Diamond3', 'Heart3', 'Spade3', 'Club4', 'Diamond4', 'Heart5', 'Spade6', 'Club7', 'Diamond8', 'Heart9', 'Spade10', 'ClubJ', 'DiamondQ', 'HeartK', 'SpadeA', 'Club2', 'Diamond2', 'JokerSmall', 'JokerBig'],
  guandan: ['Heart2#0', 'Heart2#1', 'Club2#0', 'Club3#0', 'Club3#1', 'Diamond3#0', 'Heart4#0', 'Spade4#0', 'Club5#0', 'Club6#0', 'Heart7#0', 'Spade7#0', 'Club8#0', 'Diamond8#0', 'Club9#0', 'Club10#0', 'Heart10#0', 'Spade10#1', 'ClubJ#0', 'DiamondJ#0', 'HeartQ#0', 'SpadeQ#0', 'ClubK#0', 'HeartA#0', 'SpadeA#0', 'JokerSmall#0', 'JokerBig#1'],
  mahjong: ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3', 's7', 's8', 's9', 'z1', 'z1'],
  sichuan_mahjong: ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3', 's7', 's8', 's9', 's1', 's1'],
};

async function mockVoice(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:sound', 'true');
    localStorage.setItem('yueyue:music', 'false');
    (window as any).__voicePlays = [];
    (window as any).Audio = class {
      src: string; paused = true; volume = 1; loop = false;
      onended = null; onerror = null;
      constructor(src: string) { this.src = src; }
      play() { this.paused = false; (window as any).__voicePlays.push(this.src); return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    };
  });
}

async function withDiscordRail(page: Page, width: number, height: number) {
  await page.evaluate(async () => {
    history.replaceState({}, '', `${location.pathname}?frame_id=chat-test`);
    const viewport = await import('/src/activityViewport.ts' as string);
    viewport.updateActivityViewport();
    if (!document.getElementById('test-discord-rail')) {
      const rail = document.createElement('div');
      rail.id = 'test-discord-rail';
      const style = document.createElement('style');
      // 灰条跟随物理方向自动移动，横竖转换后仍能真实阻挡错误位置的按钮。
      style.textContent = '#test-discord-rail{position:fixed;right:0;top:0;bottom:0;width:64px;z-index:99999;background:#555}@media(orientation:portrait){#test-discord-rail{left:0;top:auto;width:auto;height:64px}}';
      document.head.append(style);
      document.body.append(rail);
    }
  });
  await page.setViewportSize({ width, height });
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.activityRotated)).toBe(String(height > width));
}

async function expectChat(page: Page, sends: number, screenshot?: string) {
  const button = page.locator('.table-toolbar, .tg-toolbar').getByRole('button', { name: '打开牌桌聊天', exact: true });
  await expect(page.getByRole('button', { name: '打开牌桌聊天', exact: true })).toHaveCount(1);
  await expect(button).toHaveText('聊天');
  await expect(button).toBeInViewport({ ratio: 1 });
  const box = (await button.boundingBox())!;
  const { width, height } = page.viewportSize()!;
  const portrait = height > width;
  expect(box.x + box.width, '聊天按钮完整避开 Discord 物理安全区').toBeLessThanOrEqual(width - (portrait ? 0 : 64) + 1);
  expect(box.y + box.height, '聊天按钮完整避开 Discord 物理安全区').toBeLessThanOrEqual(height - (portrait ? 64 : 0) + 1);
  const stage = await page.locator('.game-viewport-stage').evaluate(element => ({ width: element.clientWidth, height: element.clientHeight }));
  const hasMessage = await page.locator('.table-fullscreen:has(.blackjack-table) > .status-message, .table-fullscreen:has(.blackjack-table) > .error-message').count();
  expect(stage, '每个模式的共享舞台使用完整安全矩形，只为实际21点提示保留24px').toEqual({ width: portrait ? height - 64 : width - 64, height: (portrait ? width : height) - (hasMessage ? 24 : 0) });
  if (screenshot) await page.screenshot({ path: `../../../../../tmp/jev-ui-${screenshot}-entry.png` });
  await button.click();
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  const panel = page.getByRole('region', { name: '牌桌聊天' });
  await expect(panel).toBeInViewport({ ratio: 1 });
  const line = page.getByRole('button', { name: '你是MM还是GG？', exact: true });
  await line.scrollIntoViewIfNeeded();
  await line.click();
  await expect(page.locator('.social-notices')).toContainText('你是MM还是GG？');
  await expect.poll(() => page.evaluate(() => (window as any).__voicePlays.filter((src: string) => new URL(src, location.origin).pathname.endsWith('/mm_or_gg.mp3')).length)).toBe(sends);
  if (screenshot) await page.screenshot({ path: `../../../../../tmp/jev-ui-${screenshot}.png` });
  await page.keyboard.press('Escape');
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(button).toBeFocused();
}

for (const [kind, title, count] of [
  ['texas', '德州扑克', 8], ['golden_flower', '炸金花', 5],
  ['landlord', '斗地主', 3], ['guandan', '掼蛋', 4],
  ['mahjong', '四人麻将', 4], ['sichuan_mahjong', '四人麻将', 4],
] as const) {
  test(`${kind}等待、对局、结算直接显示聊天，横竖屏均可发送并播放快捷语音`, async ({ page }) => {
    await mockVoice(page);
    await page.setViewportSize({ width: 844, height: 390 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const room: any = {
      room_id: 'CHAT01', game_type: kind, host_user_id: uid, state: 'waiting', revision: 1,
      mode: 'multi', min_players: count, max_players: count,
      room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
      turn_timeout_seconds: 60, turn_deadline: Date.now() / 1000 + 60,
      players: Array.from({ length: count }, (_, index) => ({ user_id: index ? `bot:${index}` : uid, username: index ? `月月${index}` : '测试玩家', avatar_url: '/character/normal.webp', is_bot: !!index, is_ready: true, connected: true })),
      game: null,
    };
    let eventId = 0;
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 } });
      if (path.startsWith('/api/game-social/')) {
        if (route.request().method() === 'POST') {
          expect(path).toBe('/api/game-social/table/CHAT01');
          expect(route.request().postDataJSON()).toEqual({ kind: 'chat', item_id: 'mm_or_gg' });
        }
        const events = route.request().method() === 'POST' ? [{ event_id: ++eventId, kind: 'chat', item_id: 'mm_or_gg', text: '你是MM还是GG？', user_id: uid, username: '测试玩家', timestamp: Date.now() / 1000 }] : [];
        return route.fulfill({ json: { success: true, cursor: eventId, events } });
      }
      return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
    });
    await page.goto(`/?dev_user_id=${uid}`);
    await page.getByRole('button', { name: new RegExp(`^${title}`) }).click();
    if (kind === 'sichuan_mahjong') await page.getByRole('button', { name: '四川血战', exact: true }).click();
    await page.getByRole('button', { name: '好友同桌', exact: true }).click();
    await withDiscordRail(page, 844, 390);
    await expectChat(page, 1, `${kind}-844`);
    room.state = 'playing'; room.revision++;
    const hand = cardHands[kind]!;
    room.game = {
      phase: 'playing', finished: false, current_player_id: uid,
      legal_actions: kind === 'texas' ? ['check', 'raise', 'fold'] : kind === 'golden_flower' ? ['call', 'compare', 'fold'] : ['landlord', 'guandan'].includes(kind) ? ['play', 'pass'] : ['discard'],
      winners: [], message: '轮到你操作', community_cards: kind === 'texas' ? ['HeartA', 'Club9', 'Diamond7'] : [],
      min_raise_to: 4, max_raise_to: 100, call_amount: 1, compare_cost: 2,
      bottom_cards: ['SpadeA', 'Club2', 'JokerBig'], level: '2', team_levels: ['2', '2'], play_options: [],
      players: room.players.map((p: any, index: number) => ({ user_id: p.user_id, hand: index ? [] : hand, hand_count: hand.length, stack: 100, score: 0, team: index % 2, discards: [] })),
    };
    await page.getByRole('button', { name: '同步', exact: true }).click();
    await withDiscordRail(page, 390, 844);
    await expect(page.locator('.tg-my-hand .tg-hand-card')).toHaveCount(hand.length);
    for (const control of await page.locator('.tg-hand-card, .tg-control-panel button, .tg-control-panel input, .tg-control-panel select').all()) {
      await expect(control).toBeInViewport({ ratio: 1 });
      if (await control.isEnabled()) await control.click({ trial: true });
    }
    await expectChat(page, 2, `${kind}-390`);
    room.state = 'finished'; room.revision++; room.game.finished = true;
    await page.getByRole('button', { name: '同步', exact: true }).click();
    await expectChat(page, 3);
    expect(eventId, '等待、对局、结算都向当前房间发送了一条快捷语音').toBe(3);
    await page.getByRole('button', { name: '战绩与声音', exact: true }).click();
    await page.getByRole('dialog', { name: '战绩与声音' }).getByRole('button', { name: '玩法规则', exact: true }).click();
    await expect(page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: /规则/ }) })).toBeVisible();
  });
}

test('单人21点等待、对局、结算均可从顶栏发送并播放快捷语音', async ({ page }) => {
  await mockVoice(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  const game = { user_id: uid, bet_amount: 10, game_state: 'player_turn', player_hand: ['ClubK', 'Heart8'], dealer_hand: ['Club10', 'Hidden'], player_score: 18, dealer_score: 10 };
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 } });
    if (path === '/api/game/current') return route.fulfill({ json: { success: true, game: null } });
    if (path === '/api/game/stand') { game.game_state = 'finished_win'; game.dealer_hand = ['Club10', 'Heart6']; game.dealer_score = 16; }
    return route.fulfill({ json: { success: true, game, new_balance: 2000 } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: /^单人对战/ }).click();
  await withDiscordRail(page, 390, 844);
  await expectChat(page, 1, 'single-390');
  await page.getByRole('button', { name: '开始对战', exact: true }).click();
  await withDiscordRail(page, 844, 390);
  await expectChat(page, 2, 'single-844');
  await page.getByRole('button', { name: '停牌', exact: true }).click();
  await expectChat(page, 3);
  await page.getByRole('button', { name: '战绩与声音', exact: true }).click();
  await page.getByRole('dialog', { name: '战绩与声音' }).getByRole('button', { name: '玩法规则', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '21点玩法规则' })).toBeVisible();
});

test('多人21点等待、对局、结算均可从顶栏发送并播放房间快捷语音', async ({ page }) => {
  await mockVoice(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 844, height: 390 });
  const room: any = {
    room_id: 'CHAT21', host_user_id: uid, max_players: 3, state: 'waiting', round_number: 1,
    current_turn_user_id: null, ready_player_count: 0, all_players_ready: false,
    dealer: { name: '月月', avatar_path: '/character/normal.webp', hand: [], score: 0 },
    players: [uid, '-1'].map((id, index) => ({ user_id: id, username: index ? '月月陪玩' : '测试玩家', avatar_url: '/character/normal.webp', seat_index: index, bet_amount: 10, hand: [], score: 0, status: 'waiting', result: null, payout_amount: 0, is_ready: false, is_current_turn: false, is_bot: !!index })),
  };
  let eventId = 0;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 } });
    if (path.startsWith('/api/game-social/')) {
      if (route.request().method() === 'POST') {
        expect(path).toBe('/api/game-social/blackjack/CHAT21');
        expect(route.request().postDataJSON()).toEqual({ kind: 'chat', item_id: 'mm_or_gg' });
      }
      const events = route.request().method() === 'POST' ? [{ event_id: ++eventId, kind: 'chat', item_id: 'mm_or_gg', text: '你是MM还是GG？', user_id: uid, username: '测试玩家', timestamp: Date.now() / 1000 }] : [];
      return route.fulfill({ json: { success: true, cursor: eventId, events } });
    }
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: '多人对战 最多3人同桌' }).click();
  await page.getByRole('button', { name: '创建房间', exact: true }).click();
  await withDiscordRail(page, 844, 390);
  await expectChat(page, 1, 'blackjack-844');
  room.state = 'playing'; room.current_turn_user_id = uid;
  room.dealer.hand = ['Club10', 'Hidden']; room.dealer.score = 10;
  for (const player of room.players) Object.assign(player, { hand: ['ClubK', 'Heart8'], score: 18, status: 'playing', is_current_turn: player.user_id === uid });
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await withDiscordRail(page, 390, 844);
  await expectChat(page, 2, 'blackjack-390');
  room.state = 'finished'; room.current_turn_user_id = null;
  room.dealer.hand = ['Club10', 'Heart6']; room.dealer.score = 16;
  for (const player of room.players) Object.assign(player, { status: 'done', result: 'win', payout_amount: 20, is_current_turn: false });
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expectChat(page, 3);
  expect(eventId).toBe(3);
  await page.getByRole('button', { name: '战绩与声音', exact: true }).click();
  await page.getByRole('dialog', { name: '战绩与声音' }).getByRole('button', { name: '玩法规则', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '21点玩法规则' })).toBeVisible();
});
