import { expect, test, type Page } from '@playwright/test';
import { captureFinalScreenshot, animationEvents, expectBustSpriteMotion, expectRealMotion, expectResultReadable, recordCardAnimations, waitForTableMotion } from './animation-helpers';

const uid = '123456789012345678';
const longName = '这是一位名字特别特别长的测试玩家用于确保点数始终独立清晰显示';
const profile = { success: true, user_id: uid, username: longName, avatar_url: '/character/normal.webp', balance: 1000 };
const sizes = [{ width: 568, height: 320 }, { width: 844, height: 390 }, { width: 876, height: 1158 }, { width: 1440, height: 900 }];

async function enterBlackjack(page: Page, multi = false) {
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: multi ? '多人对战 最多3人同桌' : '单人对战 你 vs 月月' }).click();
}

async function expectScore(page: Page, score: number, bust = false) {
  const badge = page.getByLabel('你的点数', { exact: true });
  await expect(badge).toContainText(String(score));
  if (bust) await expect(badge).toContainText('爆牌');
  await expect(badge).toBeInViewport({ ratio: 1 });
  const geometry = await badge.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const top = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return { clipped: element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1,
      unobstructed: top === element || element.contains(top),
      font: Math.max(parseFloat(getComputedStyle(element).fontSize), ...Array.from(element.querySelectorAll('*')).map(child => parseFloat(getComputedStyle(child).fontSize))) };
  });
  expect(geometry.clipped, '长用户名不能裁掉独立点数徽章').toBe(false);
  expect(geometry.unobstructed, '点数不能被卡牌或结果遮挡').toBe(true);
  expect(geometry.font).toBeGreaterThanOrEqual(20);
}

async function mockBlackjack(page: Page, outcome = 'win', multi = false, immediate = false) {
  let active = false;
  let polls = 0;
  let hitBust = false;
  const game: any = { user_id: uid, bet_amount: 11, game_state: 'player_turn', player_hand: ['Spade10', 'Heart6'], dealer_hand: ['Club10', 'Hidden'], player_score: 16, dealer_score: 10 };
  const room: any = {
    room_id: 'ANIM21', host_user_id: uid, max_players: 3, state: 'waiting', round_number: 0,
    current_turn_user_id: null, ready_player_count: 1, all_players_ready: false,
    dealer: { name: '月月', avatar_path: '/character/normal.webp', expression: 'normal', hand: [], score: 0 },
    players: [uid, '223456789012345678'].map((id, index) => ({
      user_id: id, username: index ? '同桌玩家' : longName, avatar_url: '/character/normal.webp', seat_index: index,
      bet_amount: index ? 11 : 0, hand: [], score: 0, status: 'waiting', result: null,
      payout_amount: 0, is_ready: index > 0, is_current_turn: false, is_bot: false,
    })),
  };
  const finish = () => {
    game.game_state = `finished_${outcome}`;
    game.player_hand = outcome === 'blackjack' ? ['SpadeA', 'HeartK'] : outcome === 'loss' ? ['Spade10', 'Heart6', 'Club8'] : ['Spade10', 'Heart6', 'Club5'];
    game.player_score = outcome === 'loss' ? 24 : 21;
    game.dealer_hand = outcome === 'push' ? ['Club10', 'DiamondA'] : ['Club10', 'Diamond8'];
    game.dealer_score = outcome === 'push' ? 21 : 18;
    room.state = 'finished'; room.current_turn_user_id = null;
    room.dealer.hand = [...game.dealer_hand]; room.dealer.score = game.dealer_score;
    Object.assign(room.players[0], { hand: [...game.player_hand], score: game.player_score, result: outcome,
      payout_amount: ({ win: 22, loss: 0, push: 11, blackjack: 27 } as any)[outcome], status: 'done', is_current_turn: false, is_ready: false });
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() || {};
    if (path === '/api/profile') return route.fulfill({ json: profile });
    if (path === '/api/game/current') return route.fulfill({ json: { success: true, game: active ? game : null, new_balance: 743 } });
    if (path.startsWith('/api/game/')) {
      if (path.endsWith('/start')) { active = true; if (immediate) finish(); }
      if (path.endsWith('/hit')) {
        if (hitBust) finish();
        else { game.player_hand.push('Club5'); game.player_score = 21; }
      }
      if (path.endsWith('/stand')) finish();
      return route.fulfill({ json: { success: true, game, new_balance: 743 } });
    }
    if (path.startsWith('/api/multi/room/')) {
      if (route.request().method() === 'GET') polls++;
      if (path.endsWith('/bet')) room.players[0].bet_amount = body.amount;
      if (path.endsWith('/ready')) { room.players[0].is_ready = true; room.ready_player_count = 2; room.all_players_ready = true; }
      if (path.endsWith('/start')) {
        room.state = 'playing'; room.current_turn_user_id = uid; room.round_number++;
        room.dealer.hand = [...game.dealer_hand]; room.dealer.score = 10;
        for (const player of room.players) Object.assign(player, { hand: [...game.player_hand], score: 16, status: 'playing', is_current_turn: player.user_id === uid });
      }
      if (path.endsWith('/hit')) { room.players[0].hand.push('Club5'); room.players[0].score = 21; }
      if (path.endsWith('/stand')) finish();
      return route.fulfill({ json: { success: true, room, viewer_balance: 743 } });
    }
    return route.fulfill({ status: 404, json: { detail: `未模拟接口 ${path}` } });
  });
  return { room, game, polls: () => polls, multi, bustOnHit: () => { hitBust = true; } };
}

async function startBlackjack(page: Page, multi = false) {
  if (multi) {
    await page.getByRole('button', { name: '创建房间', exact: true }).click();
    await page.getByLabel('多人下注金额').fill('11');
    await page.getByRole('button', { name: '下注', exact: true }).click();
    await page.getByRole('button', { name: '准备', exact: true }).click();
    await page.getByRole('button', { name: '开始本局', exact: true }).click();
  } else {
    await page.getByLabel('单人下注金额').fill('11');
    await page.getByRole('button', { name: '开始对战', exact: true }).click();
  }
}

for (const multi of [false, true]) {
  test(`21点${multi ? '多人' : '单人'}真实发牌补牌、独立点数和静态恢复`, async ({ page }) => {
    test.setTimeout(25_000);
    await page.setViewportSize(sizes[1]!);
    await recordCardAnimations(page);
    const mock = await mockBlackjack(page, 'win', multi);
    await enterBlackjack(page, multi);
    await startBlackjack(page, multi);
    await expectRealMotion(page.locator('.playing-card'));
    await waitForTableMotion(page);
    for (const size of sizes) { await page.setViewportSize(size); await expectScore(page, 16); }
    const old = await animationEvents(page);
    expect(old.length).toBeGreaterThan(0);
    await page.getByRole('button', { name: '要牌', exact: true }).click();
    await expectRealMotion(page.locator('.playing-card[alt="Club5"]'));
    await waitForTableMotion(page);
    await expectScore(page, 21);
    const fresh = (await animationEvents(page)).slice(old.length);
    expect(fresh.length).toBeGreaterThan(0);
    expect(fresh.every(event => event.label === 'Club5'), '补牌不能让已有手牌重新发一遍').toBe(true);
    if (multi) {
      const polls = mock.polls();
      await expect.poll(mock.polls).toBeGreaterThan(polls);
      expect((await animationEvents(page)).length).toBe(old.length + fresh.length);
    }
    await page.reload();
    await page.getByRole('button', { name: '21点 立即游玩' }).click();
    await page.getByRole('button', { name: multi ? '多人对战 最多3人同桌' : '单人对战 你 vs 月月' }).click();
    await expectScore(page, 21);
    await waitForTableMotion(page);
    expect(await animationEvents(page), '刷新恢复已有牌局必须静态显示').toEqual([]);
    await page.getByRole('button', { name: '停牌', exact: true }).click();
    await expectRealMotion(page.locator('.dealer-hand'));
    const result = page.locator('.round-feedback');
    await expectRealMotion(result);
    await expect(result).toContainText('胜利');
    await expect(result).toContainText('+11');
    for (const size of sizes) {
      await page.setViewportSize(size);
      await expectScore(page, 21);
      await expectResultReadable(page, result);
      await captureFinalScreenshot(page, `animation-blackjack-${multi ? 'multi' : 'single'}-${size.width}x${size.height}.png`);
    }
    if (multi) {
      const resultAnimations = await page.evaluate(() => (window as any).__resultAnimations.length);
      const polls = mock.polls();
      await expect.poll(mock.polls).toBeGreaterThan(polls);
      expect(await page.evaluate(() => (window as any).__resultAnimations.length), '相同结算轮询不得再次播放结果动画').toBe(resultAnimations);
    }
  });
}

for (const outcome of [
  { result: 'loss', title: '失败', tone: 'loss', delta: /净输\s*-?11/, score: 24 },
  { result: 'push', title: '平局', tone: 'push', delta: '0', score: 21 },
  { result: 'blackjack', title: '21点', tone: 'win', delta: '+16', score: 21 },
]) {
  test(`21点${outcome.title}结果、奇数下注派彩和重连不重复动画`, async ({ page }) => {
    await page.setViewportSize(sizes[1]!);
    await recordCardAnimations(page);
    await mockBlackjack(page, outcome.result, false, outcome.result === 'blackjack');
    await enterBlackjack(page);
    await startBlackjack(page);
    if (outcome.result === 'blackjack') {
      await expectRealMotion(page.locator('.playing-card'));
      // 开局天然21点也必须先实际发牌，不能跳过发牌直接展示结算。
      await expect(page.locator('[data-dealing="true"]')).not.toHaveCount(0);
      await expect(page.locator('.round-feedback')).toHaveCount(0);
    } else {
      await waitForTableMotion(page);
      await page.getByRole('button', { name: '停牌', exact: true }).click();
    }
    const result = page.locator('.round-feedback');
    await expect(result).toHaveAttribute('data-tone', outcome.tone);
    await expect(result).toContainText(outcome.title);
    await expect(result).toContainText(outcome.delta);
    await expectRealMotion(result);
    await expectResultReadable(page, result);
    await expectScore(page, outcome.score, outcome.result === 'loss');
    await page.reload();
    await page.getByRole('button', { name: '21点 立即游玩' }).click();
    await page.getByRole('button', { name: '单人对战 你 vs 月月' }).click();
    await expect(result).toHaveAttribute('data-animated', 'false');
    await expect(result).toContainText(outcome.delta);
    await waitForTableMotion(page);
    expect(await animationEvents(page)).toEqual([]);
  });
}

const tableSpecs = [
  { type: 'texas', title: '德州扑克', count: 2, hand: ['SpadeA', 'HeartK'], actions: ['check'], button: '过牌' },
  { type: 'golden_flower', title: '炸金花', count: 2, hand: ['SpadeA', 'HeartK', 'Club8'], actions: ['look', 'fold'], button: '看牌' },
  { type: 'landlord', title: '斗地主', count: 3, hand: ['Club3', 'Diamond4', 'Heart5', 'Spade6', 'Club7'], actions: ['play', 'pass'], button: '出牌' },
  { type: 'mahjong', title: '四人麻将', count: 4, hand: ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3', 's7', 's8', 's9', 'z1', 'z1'], actions: ['discard', 'win'], button: '弃选中的牌' },
];

async function mockTable(page: Page, type: string) {
  const spec = tableSpecs.find(item => item.type === type)!;
  let polls = 0;
  let joins = 0;
  const room: any = {
    room_id: 'ANIMTG', game_type: type, host_user_id: uid, state: 'waiting', revision: 1, round_number: 0,
    mode: 'solo', room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
    include_yueyue: true, min_players: spec.count, max_players: spec.count, buy_in: 100,
    settlement_status: 'none', actual_settlement: {}, game: null,
    players: Array.from({ length: spec.count }, (_, index) => ({ user_id: index ? `bot:${index}` : uid,
      username: index ? index === 1 ? '月月' : `陪玩${index}` : '测试玩家', avatar_url: '/character/normal.webp', is_bot: index > 0, is_ready: index > 0, connected: true })),
  };
  let onAction = (body: any) => {
    if (type === 'texas') room.game.community_cards = ['Club2', 'Heart7', 'DiamondQ'];
    if (type === 'golden_flower') { room.game.players[0].hand = [...spec.hand]; room.game.legal_actions = ['fold', 'call']; }
    if (type === 'landlord') room.game.players[0].hand = room.game.players[0].hand.filter((card: string) => !body.cards.includes(card));
    if (type === 'mahjong') {
      const hand = room.game.players[0].hand as string[];
      hand.splice(hand.indexOf(body.tile), 1);
      hand.splice(3, 0, 'm7');
      room.game.players[0].discards.push(body.tile);
    }
  };
  const finish = (winners: string[]) => {
    room.state = 'finished'; room.game.finished = true; room.game.phase = 'finished'; room.game.winners = [...winners];
    for (const player of room.players) player.is_ready = player.is_bot;
    room.game.current_player_id = null; room.game.legal_actions = []; room.settlement_status = 'settled';
    if (type === 'mahjong') for (const player of room.game.players) player.hand = [...spec.hand];
    room.actual_settlement = Object.fromEntries(room.players.map((player: any) => [player.user_id, !winners.length ? 0 : winners.includes(player.user_id) ? 7 : -7]));
    room.game.players[0].score_delta = winners.includes(uid) ? 999 : -999;
    room.revision++;
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() || {};
    if (path === '/api/profile') return route.fulfill({ json: { ...profile, username: '测试玩家' } });
    if (!path.startsWith('/api/tables/')) return route.fulfill({ status: 404 });
    if (route.request().method() === 'GET') polls++;
    if (path.endsWith('/join')) joins++;
    if (path.endsWith('/ready')) room.players[0].is_ready = true;
    if (path.endsWith('/start')) {
      room.state = 'playing'; room.round_number++; room.settlement_status = 'reserved'; room.actual_settlement = {};
      room.game = { phase: type === 'texas' ? 'preflop' : 'playing', deal_count: 1, finished: false,
        current_player_id: uid, winners: [], message: '轮到你了', legal_actions: [...spec.actions],
        community_cards: [], pot: 3, wall_count: 83, bottom_cards: type === 'landlord' ? ['SpadeA', 'Club2', 'JokerBig'] : [],
        players: room.players.map((player: any) => ({ user_id: player.user_id, hand: player.is_bot || type === 'golden_flower' ? [] : [...spec.hand], hand_count: spec.hand.length, score_delta: 0, stack: 100, discards: [] })) };
    }
    if (path.endsWith('/action')) { onAction(body); room.revision++; }
    return route.fulfill({ json: { success: true, room, viewer_balance: 900 } });
  });
  return { room, spec, finish, polls: () => polls, joins: () => joins, action: (handler: (body: any) => void) => { onAction = handler; } };
}

async function enterTable(page: Page, title: string, start = true) {
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: new RegExp(`^${title}`) }).click();
  if (start) {
    await page.getByRole('button', { name: /^月月陪玩/ }).click();
    await page.getByRole('button', { name: '准备', exact: true }).click();
    await page.getByRole('button', { name: '开始本局', exact: true }).click();
  }
}

for (const spec of tableSpecs) {
  test(`${spec.title}真实分座发牌、追加牌不重播和轮询恢复稳定`, async ({ page }) => {
    test.setTimeout(20_000);
    await page.setViewportSize(sizes[1]!);
    await recordCardAnimations(page);
    const mock = await mockTable(page, spec.type);
    await enterTable(page, spec.title);
    await expectRealMotion(page.locator('.tg-deal-card'));
    const destinations = await page.locator('.tg-deal-card').evaluateAll(cards => [...new Set(cards.map(card => card.getAttribute('data-seat-id')))]);
    expect(destinations).toEqual(expect.arrayContaining(mock.room.players.map((player: any) => player.user_id)));
    expect(await page.locator('.tg-deal-layer img').evaluateAll(images => images.every(image => !/\/cards\/(Club|Diamond|Heart|Spade)|\/mahjong\/[mpsz][1-9]/.test(image.getAttribute('src') || ''))), '发牌飞行只用牌背').toBe(true);
    await waitForTableMotion(page);
    await expect(page.locator('.tg-seat:not(.tg-self) .tg-hidden-hand')).toHaveCount(spec.count - 1);
    const before = await animationEvents(page);
    if (['mahjong', 'landlord'].includes(spec.type)) await page.locator('.tg-hand-card').first().click();
    expect((await animationEvents(page)).length, '选牌不应重播发牌').toBe(before.length);
    await page.getByRole('button', { name: spec.button, exact: true }).click();
    if (spec.type === 'texas') await expectRealMotion(page.getByLabel('公共牌', { exact: true }));
    if (spec.type === 'mahjong') await expectRealMotion(page.locator('.tg-hand-card').filter({ has: page.locator('img[src="/mahjong/faces/m7.svg"]') }));
    await waitForTableMotion(page);
    const after = await animationEvents(page);
    const originalTargets = new Set(before.map(event => event.targetId));
    expect(after.slice(before.length).filter(event => originalTargets.has(event.targetId)), '新牌或看牌不能重播已有牌实例动画').toEqual([]);
    if (spec.type === 'landlord') expect(after.length, '移除已出的牌不应重新发手牌').toBe(before.length);
    if (spec.type === 'mahjong') {
      const extra = after.slice(before.length).filter(event => event.label.includes('万') || event.label.includes('条') || event.label.includes('筒') || event.label.includes('东'));
      expect(extra.length).toBeGreaterThan(0);
      expect(extra.every(event => event.label.startsWith('7万')), '插入摸牌仅新牌播放，已有重复牌和排序变动不能重播').toBe(true);
    }
    const polls = mock.polls();
    await expect.poll(mock.polls).toBeGreaterThan(polls);
    expect((await animationEvents(page)).length).toBe(after.length);
    await page.reload();
    await page.getByRole('button', { name: new RegExp(`^${spec.title}`) }).click();
    await expect.poll(mock.joins).toBe(1);
    await expect(page.locator('.table-games')).toHaveAttribute('data-dealing', 'false');
    await waitForTableMotion(page);
    expect(await animationEvents(page), '重新加入已有牌局不能重新播放发牌').toEqual([]);
  });
}

test('麻将胡牌区分本人、对手和流局，使用实际结算且恢复静态', async ({ page }) => {
  test.setTimeout(25_000);
  await page.setViewportSize(sizes[1]!);
  await recordCardAnimations(page);
  const mock = await mockTable(page, 'mahjong');
  await enterTable(page, '四人麻将');
  for (const [index, outcome] of [
    { winners: [uid], title: '胡牌', tone: 'mahjong', delta: /净赢\s*(\+)?7/ },
    { winners: ['bot:1'], title: '月月 胡牌', tone: 'mahjong', delta: /净输\s*(-)?7/ },
    { winners: [], title: '流局', tone: 'push', delta: /净变动\s*0/ },
  ].entries()) {
    if (index) {
      await page.getByRole('button', { name: '准备下一局', exact: true }).click();
      await page.getByRole('button', { name: '开始本局', exact: true }).click();
    }
    await waitForTableMotion(page);
    mock.action(() => mock.finish(outcome.winners));
    await page.getByRole('button', { name: '胡牌', exact: true }).click();
    const result = page.locator('.round-feedback');
    await expectRealMotion(result);
    await expect(result).toHaveAttribute('data-tone', outcome.tone);
    await expect(result).toContainText(outcome.title);
    await expect(result).toContainText(outcome.delta);
    await expect(result).not.toContainText('999');
    if (!outcome.winners.length) await expect(result).not.toContainText('胡牌');
    await expectResultReadable(page, result);
    const resultAnimations = await page.evaluate(() => (window as any).__resultAnimations.length);
    const polls = mock.polls();
    await expect.poll(mock.polls).toBeGreaterThan(polls);
    expect(await page.evaluate(() => (window as any).__resultAnimations.length), '胡牌/流局普通轮询不得重新播放').toBe(resultAnimations);
    await captureFinalScreenshot(page, `animation-mahjong-result-${index}.png`);
    await page.reload();
    await page.getByRole('button', { name: /^四人麻将/ }).click();
    await expect(result).toHaveAttribute('data-animated', 'false');
    await expect(result).toContainText(outcome.title);
  }
});

test('斗地主农民队友获胜判同队胜，重新发牌有动画且不重复轮询', async ({ page }) => {
  await page.setViewportSize(sizes[1]!);
  await recordCardAnimations(page);
  const mock = await mockTable(page, 'landlord');
  await enterTable(page, '斗地主');
  await waitForTableMotion(page);
  mock.room.game.deal_count++;
  mock.room.revision++;
  const polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  await expectRealMotion(page.locator('.tg-deal-card'));
  await waitForTableMotion(page);
  const beforeBottomCards = await animationEvents(page);
  mock.room.game.players[0].hand.push('SpadeA', 'Club2', 'JokerBig');
  mock.room.game.players[0].hand_count += 3;
  mock.room.revision++;
  const beforePoll = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(beforePoll);
  await expectRealMotion(page.locator('.tg-hand-card[data-card-dealing="true"]'));
  await waitForTableMotion(page);
  const previousCards = new Set(beforeBottomCards.map(event => event.targetId));
  expect((await animationEvents(page)).slice(beforeBottomCards.length).filter(event => previousCards.has(event.targetId)), '地主获得底牌只动画新加的三张牌').toEqual([]);
  mock.action(() => mock.finish([uid, 'bot:2']));
  await page.locator('.tg-hand-card').first().click();
  await page.getByRole('button', { name: '出牌', exact: true }).click();
  const result = page.locator('.round-feedback');
  await expectRealMotion(result);
  await expect(result).toHaveAttribute('data-tone', 'win');
  await expect(result).toContainText('胜利');
  await expect(result).toContainText('陪玩2');
  await expect(result).toContainText(/净赢\s*(\+)?7/);
  await expect(result).not.toContainText('999');
});

test('结算到账前不冒充实际净额，到账更新金额不重播结果', async ({ page }) => {
  await page.setViewportSize(sizes[1]!);
  await recordCardAnimations(page);
  const mock = await mockTable(page, 'texas');
  await enterTable(page, '德州扑克');
  await waitForTableMotion(page);
  mock.action(() => { mock.finish([uid]); mock.room.settlement_status = 'reserved'; mock.room.actual_settlement = {}; });
  await page.getByRole('button', { name: '过牌', exact: true }).click();
  const result = page.locator('.round-feedback');
  await expectRealMotion(result);
  await expect(result).toContainText('结算处理中');
  await expect(result).not.toContainText('999');
  await waitForTableMotion(page);
  const animations = await page.evaluate(() => (window as any).__resultAnimations.length);
  mock.room.settlement_status = 'settled'; mock.room.actual_settlement = { [uid]: 7 };
  const polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  await expect(result).toContainText(/净赢\s*(\+)?7/);
  expect(await page.evaluate(() => (window as any).__resultAnimations.length)).toBe(animations);
});

test('减少动态模式发牌和胡牌直接稳定显示且按钮不受阻', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize(sizes[0]!);
  await recordCardAnimations(page);
  const mock = await mockTable(page, 'mahjong');
  await enterTable(page, '四人麻将');
  await expect(page.locator('.tg-hand-card')).toHaveCount(14);
  await expect(page.locator('.tg-deal-layer')).toHaveCount(0);
  expect(await animationEvents(page)).toEqual([]);
  mock.action(() => mock.finish([uid]));
  await page.getByRole('button', { name: '胡牌', exact: true }).click();
  const result = page.locator('.round-feedback');
  await expectResultReadable(page, result);
  expect(await result.evaluate(element => element.getAnimations({ subtree: true }).filter(animation => animation.playState === 'running').length)).toBe(0);
  await expect(page.getByRole('button', { name: '准备下一局', exact: true })).toBeInViewport({ ratio: 1 });
  await page.getByRole('button', { name: '准备下一局', exact: true }).click({ trial: true });
});

test('21点实时要牌爆牌播放爆裂，刷新不重播且减少动态禁用', async ({ page }) => {
  await page.setViewportSize(sizes[1]!);
  await recordCardAnimations(page);
  const mock = await mockBlackjack(page, 'loss');
  mock.bustOnHit();
  await enterBlackjack(page);
  await startBlackjack(page);
  await waitForTableMotion(page);
  await page.getByRole('button', { name: '要牌', exact: true }).click();
  await expectRealMotion(page.locator('[data-bust-burst]'));
  await expectBustSpriteMotion(page.locator('[data-bust-burst]'));
  await waitForTableMotion(page);
  await expectScore(page, 24, true);
  const result = page.locator('.round-feedback');
  await expect(result).toContainText('失败');
  expect(await page.evaluate(() => (window as any).__bustAnimations.length)).toBeGreaterThan(0);
  await page.reload();
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: '单人对战 你 vs 月月' }).click();
  await expectScore(page, 24, true);
  expect(await page.evaluate(() => (window as any).__bustAnimations)).toEqual([]);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  Object.assign(mock.game, { game_state: 'player_turn', player_hand: ['Spade10', 'Heart6'], player_score: 16 });
  await page.getByRole('button', { name: '再来一局', exact: true }).click();
  await page.getByRole('button', { name: '要牌', exact: true }).click();
  await expectScore(page, 24, true);
  await expect(result).toContainText('失败');
  expect(await page.evaluate(() => (window as any).__bustAnimations)).toEqual([]);
  expect(await page.locator('[data-bust-burst], .round-feedback, .playing-card').evaluateAll(elements => elements.flatMap(element => element.getAnimations({ subtree: true })).filter(animation => animation.playState === 'running').length)).toBe(0);
});

test('21点多人对手与荷官爆牌各触发一次，重复同步不再次爆炸', async ({ page }) => {
  await page.setViewportSize(sizes[1]!);
  await recordCardAnimations(page);
  const mock = await mockBlackjack(page, 'win', true);
  await enterBlackjack(page, true);
  await startBlackjack(page, true);
  await waitForTableMotion(page);
  Object.assign(mock.room.players[1], { score: 24, status: 'bust', hand: ['Club10', 'Diamond6', 'Heart8'] });
  let polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  const opponent = page.locator('.seat-area:not(.viewer-seat)').filter({ hasText: '同桌玩家' });
  await expectRealMotion(opponent.locator('[data-bust-burst]'));
  await waitForTableMotion(page);
  const first = await page.evaluate(() => (window as any).__bustAnimations.length);
  expect(first).toBeGreaterThan(0);
  polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  expect(await page.evaluate(() => (window as any).__bustAnimations.length)).toBe(first);
  Object.assign(mock.room.dealer, { score: 25, hand: ['Club10', 'Diamond7', 'Heart8'] });
  polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  await expectRealMotion(page.locator('.dealer-hand [data-bust-burst], .dealer-seat [data-bust-burst]'));
  await waitForTableMotion(page);
  const second = await page.evaluate(() => (window as any).__bustAnimations.length);
  expect(second).toBeGreaterThan(first);
  polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  expect(await page.evaluate(() => (window as any).__bustAnimations.length)).toBe(second);
  await page.reload();
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: '多人对战 最多3人同桌' }).click();
  await waitForTableMotion(page);
  expect(await page.evaluate(() => (window as any).__bustAnimations)).toEqual([]);
});
