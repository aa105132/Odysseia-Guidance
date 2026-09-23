import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';
const bot = '-1';

async function mockAudio(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:music', 'true');
    localStorage.setItem('yueyue:sound', 'true');
    (window as any).__tracks = [];
    class TestAudio {
      src: string; original: string; paused = true; volume = 1; loop = false; plays = 0;
      onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = this.original = src; (window as any).__tracks.push(this); }
      play() { this.paused = false; this.plays++; return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    }
    (window as any).Audio = TestAudio;
  });
}

const trackCount = (page: Page, ending: string) => page.evaluate(
  suffix => (window as any).__tracks.filter((track: any) => track.original.endsWith(suffix)).length, ending,
);
const activeTracks = (page: Page, category: string) => page.evaluate(
  prefix => (window as any).__tracks.filter((track: any) => !track.paused && track.original.includes(prefix)).map((track: any) => track.original), category,
);

async function setupTable(page: Page, portrait = false) {
  await mockAudio(page);
  await page.setViewportSize(portrait ? { width: 390, height: 844 } : { width: 844, height: 390 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const room: any = {
    room_id: 'AUDIOT', game_type: 'guandan', host_user_id: uid, mode: 'multi', state: 'playing', revision: 1,
    round_number: 1, last_public_action: { id: '1:0:1', user_id: bot, action: 'pass' }, include_yueyue: true, min_players: 4, max_players: 4,
    room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100, buy_in: 100,
    turn_timeout_seconds: 60, turn_deadline: Date.now() / 1000 + 60, settlement_status: 'reserved', actual_settlement: {},
    players: [uid, bot, '-2', '-3'].map((id, i) => ({ user_id: id, username: i ? `月月${i}` : '测试玩家', avatar_url: '/character/normal.webp', is_bot: i > 0, is_ready: false, connected: true })),
    game: {
      phase: 'playing', finished: false, current_player_id: uid, message: '轮到你出牌', winners: [],
      players: [uid, bot, '-2', '-3'].map((id, i) => ({ user_id: id, hand: i ? [] : ['Club3#0', 'Heart4#0'], hand_count: 2, team: i % 2, stack: 100, score_delta: 0 })),
      legal_actions: ['play', 'pass'], level: '2', team_levels: ['2', '2'], finish_order: [], tribute_events: [],
      match_finished: false, level_gain: 0, last_play: { user_id: bot, cards: ['Club2#1'], kind: 'single', name: '单张' }, play_options: [],
    },
  };
  let polls = 0;
  let eventId = 0;
  const events: any[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const post = route.request().method() === 'POST';
    const body = route.request().postDataJSON() || {};
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 } });
    if (path.startsWith('/api/game-social/')) {
      if (post) events.push({ event_id: ++eventId, user_id: uid, username: '测试玩家', kind: body.kind, item_id: body.item_id, text: body.item_id === 'thanks' ? '谢谢你' : '倒茶', target_id: body.target_id, target_username: '月月1', timestamp: Date.now() / 1000 });
      return route.fulfill({ json: { success: true, cursor: eventId, events, catalog: { chat: [{ id: 'thanks', text: '谢谢你' }], interaction: [{ id: 'tea', text: '倒茶' }] } } });
    }
    if (path === '/api/tables/leave') return route.fulfill({ json: { success: true, room: null, viewer_balance: 2000 } });
    if (path === '/api/tables/AUDIOT') polls++;
    if (path === '/api/tables/action') {
      room.revision++;
      room.last_public_action = { id: `1:0:${room.revision}`, user_id: uid, action: body.action };
    }
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: /^掼蛋/ }).click();
  await page.getByRole('button', { name: '好友同桌', exact: true }).click();
  await expect(page.locator('.game-guandan.has-room')).toBeVisible();
  return { room, polls: () => polls };
}

test('真实桌游操作与机器人轮询去重，减少动态不禁语音，离桌停播', async ({ page }) => {
  const mock = await setupTable(page);
  expect(await trackCount(page, '/pass.mp3')).toBe(0);
  await page.getByRole('button', { name: '不出', exact: true }).click();
  await expect.poll(() => trackCount(page, '/pass.mp3')).toBe(1);
  mock.room.revision++;
  mock.room.last_public_action = { id: '1:0:99', user_id: bot, action: 'pass' };
  await expect.poll(() => trackCount(page, '/pass.mp3')).toBe(2);
  const polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  expect(await trackCount(page, '/pass.mp3')).toBe(2);
  await page.getByRole('button', { name: '离开房间', exact: true }).click();
  await expect.poll(() => activeTracks(page, '/voice/')).toEqual([]);
});

test('真实桌游结算音乐保持到结束，同桌新局立即恢复Normal', async ({ page }) => {
  const { room } = await setupTable(page);
  room.revision++;
  room.state = 'finished'; room.game.phase = 'finished'; room.game.finished = true;
  room.game.winners = [uid, '-2']; room.game.legal_actions = [];
  room.actual_settlement = { [uid]: 3, [bot]: -3, '-2': 3, '-3': -3 }; room.settlement_status = 'settled';
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => trackCount(page, '/win.mp3')).toBe(1);
  await expect.poll(() => activeTracks(page, '/music/')).toEqual(['/audio/music/win.mp3']);
  await page.getByRole('button', { name: '同步', exact: true }).click();
  expect(await trackCount(page, '/win.mp3')).toBe(1);
  expect(await activeTracks(page, '/music/')).toEqual(['/audio/music/win.mp3']);
  room.revision++; room.round_number++;
  room.state = 'playing'; room.game.phase = 'playing'; room.game.finished = false;
  room.game.legal_actions = ['play', 'pass']; room.game.winners = []; room.actual_settlement = {};
  room.settlement_status = 'reserved'; room.last_public_action = null;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => activeTracks(page, '/music/')).toEqual(['/audio/music/Normal.mp3']);
});

test('手机整体旋转后声音弹窗与头像互动可点击，快捷语音不重复且退出停播', async ({ page }) => {
  const mock = await setupTable(page, true);
  await expect(page.locator('html')).toHaveAttribute('data-activity-rotated', 'true');
  await page.getByRole('button', { name: '战绩与声音', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '战绩与声音', exact: true });
  await expect(dialog).toBeInViewport({ ratio: 1 });
  await dialog.getByRole('checkbox').first().uncheck();
  await dialog.getByRole('checkbox').first().check();
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.locator(`[data-game-avatar="${bot}"]`).click();
  const interaction = page.getByRole('dialog', { name: '牌友互动' });
  await expect(interaction).toBeInViewport({ ratio: 1 });
  await interaction.getByRole('button', { name: '倒茶', exact: true }).click();
  await expect(page.locator('.social-notices')).toContainText('倒茶');
  await expect(page.locator('.game-social-animation')).toHaveCount(0);
  await page.getByRole('button', { name: '打开牌桌聊天', exact: true }).click();
  await page.getByRole('button', { name: '谢谢你', exact: true }).click();
  await expect.poll(() => trackCount(page, '/thanks.mp3')).toBe(1);
  const polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls + 1);
  expect(await trackCount(page, '/thanks.mp3')).toBe(1);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '离开房间', exact: true }).click();
  await expect.poll(() => activeTracks(page, '/voice/')).toEqual([]);
});

test('手机旋转后头像互动飞行使用同一坐标系并落在目标头像', async ({ page }) => {
  await setupTable(page, true);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const target = page.locator(`[data-game-avatar="${bot}"]`);
  await target.click();
  await page.getByRole('dialog', { name: '牌友互动' }).getByRole('button', { name: '倒茶', exact: true }).click();
  const animation = page.locator('#app > .game-social-animation');
  await expect(animation).toBeVisible();
  await expect.poll(async () => {
    const avatar = await target.boundingBox();
    const flight = await animation.boundingBox();
    if (!avatar || !flight) return 9999;
    return Math.hypot(avatar.x + avatar.width / 2 - flight.x - flight.width / 2,
      avatar.y + avatar.height / 2 - flight.y - flight.height / 2);
  }, { intervals: [50, 100, 100, 100], timeout: 2000 }).toBeLessThan(8);
});

test('真实21点真人与陪玩语音只播一次，胜负插曲与新局场景衔接', async ({ page }) => {
  await mockAudio(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 844, height: 390 });
  const room: any = {
    room_id: 'AUDIO21', host_user_id: uid, max_players: 3, state: 'playing', round_number: 1,
    current_turn_user_id: uid, ready_player_count: 0, all_players_ready: false,
    dealer: { name: '月月', avatar_path: '/character/normal.webp', hand: ['Club10', 'Hidden'], score: 10 },
    players: [uid, bot].map((id, index) => ({ user_id: id, username: index ? '月月陪玩' : '测试玩家', avatar_url: '/character/normal.webp', seat_index: index, bet_amount: 10, hand: ['Spade5', 'Heart6'], score: 11, status: 'playing', result: null, payout_amount: 0, is_ready: false, is_current_turn: !index, is_bot: !!index })),
  };
  let polls = 0;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 } });
    if (path.startsWith('/api/game-social/')) return route.fulfill({ json: { success: true, cursor: 0, events: [] } });
    if (path === '/api/multi/room/AUDIO21') polls++;
    if (path.endsWith('/hit')) { room.players[0].hand.push('Club4'); room.players[0].score = 15; }
    if (path.endsWith('/leave')) return route.fulfill({ json: { success: true, room: null, viewer_balance: 2000 } });
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: '多人对战 最多3人同桌' }).click();
  await page.getByRole('button', { name: '创建房间', exact: true }).click();
  await page.getByRole('button', { name: '打开牌桌聊天', exact: true }).click();
  await page.getByRole('button', { name: '你是MM还是GG？', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: '你是MM还是GG？', exact: true })).toBeInViewport({ ratio: 1 });
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '要牌', exact: true }).click();
  await expect.poll(() => trackCount(page, '/hit.mp3')).toBe(1);
  room.players[1].hand.push('Diamond4'); room.players[1].score = 15;
  await expect.poll(() => trackCount(page, '/hit.mp3')).toBe(2);
  const before = polls;
  await expect.poll(() => polls).toBeGreaterThan(before);
  expect(await trackCount(page, '/hit.mp3')).toBe(2);
  room.state = 'finished'; room.current_turn_user_id = null;
  room.dealer.hand = ['Club10', 'SpadeK', 'Heart5']; room.dealer.score = 25;
  Object.assign(room.players[0], { result: 'win', payout_amount: 20, status: 'done', is_current_turn: false });
  Object.assign(room.players[1], { result: 'win', payout_amount: 20, status: 'done' });
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => activeTracks(page, '/music/')).toEqual(['/audio/music/win.mp3']);
  room.state = 'playing'; room.round_number++; room.current_turn_user_id = uid;
  room.dealer.hand = ['Club10', 'Hidden']; room.dealer.score = 10;
  for (const player of room.players) Object.assign(player, { hand: ['Spade5', 'Heart6'], score: 11, result: null, payout_amount: 0, status: 'playing', is_current_turn: player.user_id === uid });
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => activeTracks(page, '/music/')).toEqual(['/audio/music/Normal.mp3']);
  await page.getByRole('button', { name: '要牌', exact: true }).click();
  await expect.poll(() => activeTracks(page, '/voice/')).toEqual(['/audio/voice/hit.mp3']);
  await page.getByRole('button', { name: '离开房间', exact: true }).click();
  await expect.poll(() => activeTracks(page, '/voice/')).toEqual([]);
});
