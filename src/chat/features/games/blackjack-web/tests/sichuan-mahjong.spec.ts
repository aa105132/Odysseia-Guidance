import { expect, test, type Locator, type Page } from '@playwright/test';
import {
  captureFinalScreenshot, expectRealMotion, expectResultReadable,
  recordCardAnimations, waitForTableMotion,
} from './animation-helpers';

const uid = '123456789012345678';
const ids = [uid, '223456789012345678', '323456789012345678', '423456789012345678'];
const hand = ['m1', 'm1', 'm2', 'm3', 'm4', 'p2', 'p3', 'p4', 'p5', 'p5', 's3', 's4', 's5', 's6'];
const viewports = [{ width: 568, height: 320 }, { width: 844, height: 390 }, { width: 1440, height: 900 }];
type ApiRequest = { path: string; body: Record<string, any> };

async function mockSichuan(page: Page) {
  const requests: ApiRequest[] = [];
  const errors: string[] = [];
  let polls = 0;
  page.on('pageerror', error => errors.push(error.message));
  const room: any = {
    room_id: 'SC1234', game_type: 'sichuan_mahjong', host_user_id: uid,
    state: 'waiting', revision: 1, mode: 'solo', include_yueyue: true,
    min_players: 4, max_players: 4, room_tier: 'beginner', base_stake: 1,
    entry_min: 100, loss_limit: 100, buy_in: 100, round_number: 0,
    settlement_status: 'none', actual_settlement: {}, game: null,
    players: ids.map((user_id, index) => ({ user_id, username: index ? `牌友${index}` : '验收玩家',
      avatar_url: '/character/normal.webp', is_bot: index > 0, is_ready: index > 0, connected: true })),
  };
  function begin() {
    room.state = 'playing'; room.settlement_status = 'reserved'; room.round_number++;
    room.actual_settlement = {};
    room.game = {
      mahjong_variant: 'sichuan', phase: 'dingque', finished: false, current_player_id: uid,
      players: ids.map((user_id, index) => ({ user_id, hand: index ? [] : [...hand],
        hand_count: index ? 13 : 14, score_delta: 0, stack: 100,
        missing_suit: null, has_won: false, discards: [], melds: [] })),
      legal_actions: ['dingque'], message: '选择缺万、缺筒或缺条', winners: [], win_events: [],
      wall_count: 55, missing_suit_options: ['m', 'p', 's'],
    };
  }
  function addWin(index: number) {
    const game = room.game;
    const winner = ids[index]!;
    expect(game.winners).not.toContain(winner);
    const player = game.players[index];
    Object.assign(player, { has_won: true, win_order: game.winners.length + 1,
      win_fan: 3, win_label: '清一色', winning_tile: 'p5' });
    const source = index ? game.players.find((member: any) => member.user_id !== winner && !member.has_won) : null;
    const payers = source ? [source] : game.players.filter((member: any) => member.user_id !== uid && !member.has_won);
    for (const payer of payers) payer.score_delta -= 4;
    player.score_delta += 4 * payers.length;
    for (const member of game.players) member.stack = 100 + member.score_delta;
    game.winners.push(winner);
    game.win_events.push({ id: game.winners.length, user_id: winner, source_id: source?.user_id ?? null,
      kind: index ? 'discard' : 'self_draw', fan: 3, label: '清一色', amount: 4 * payers.length, tile: 'p5' });
    game.current_player_id = ids.find(id => !game.winners.includes(id));
    game.legal_actions = game.winners.includes(uid) ? [] : ['discard'];
    room.revision++;
  }
  function finish(settled: boolean) {
    room.state = 'finished';
    Object.assign(room.game, { finished: true, phase: 'finished', current_player_id: null, legal_actions: [] });
    // 真实服务会在终局公开所有暗牌，终局布局也必须覆盖摊牌后的高度。
    for (const player of room.game.players.slice(1)) player.hand = hand.slice(0, 13);
    for (const member of room.players) member.is_ready = member.is_bot;
    room.settlement_status = settled ? 'settled' : 'reserved';
    // 故意与理论得分不同，证明界面显示服务端实际到账，而不是自行推算。
    room.actual_settlement = settled ? { [uid]: 12, [ids[1]!]: 12, [ids[2]!]: 12, [ids[3]!]: -36 } : {};
    room.revision++;
  }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() || {};
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid,
      username: '验收玩家', avatar_url: '/character/normal.webp', balance: 1000 } });
    if (path === '/api/game/current') return route.fulfill({ json: { success: true, game: null } });
    if (!path.startsWith('/api/tables/')) return route.fulfill({ status: 404 });
    if (route.request().method() === 'GET') polls++;
    else requests.push({ path, body });
    if (path.endsWith('/create')) {
      room.game_type = body.game_type;
      room.mode = body.mode;
      room.state = 'waiting'; room.game = null; room.settlement_status = 'none';
      room.actual_settlement = {};
    }
    if (path.endsWith('/ready')) room.players[0].is_ready = body.ready;
    if (path.endsWith('/start')) begin();
    if (path.endsWith('/action')) {
      expect(body.expected_revision).toBe(room.revision);
      if (body.action === 'dingque') {
        expect(body.suit).toBe('s');
        Object.assign(room.game, { phase: 'playing', legal_actions: ['discard'], missing_suit_options: [], message: '请先打出缺门牌' });
        for (const player of room.game.players) player.missing_suit = 's';
      } else if (body.action === 'discard') {
        expect(body.tile).toMatch(/^s/);
        const player = room.game.players[0];
        const index = player.hand.indexOf(body.tile);
        expect(index).toBeGreaterThanOrEqual(0);
        player.hand.splice(index, 1); player.hand_count--;
        player.discards.push(body.tile);
        room.game.current_player_id = ids[1]; room.game.legal_actions = [];
      } else throw new Error(`未模拟的四川操作：${body.action}`);
    }
    if (route.request().method() === 'POST') room.revision++;
    if (path.endsWith('/leave')) return route.fulfill({ json: { success: true, room: null, viewer_balance: 1012 } });
    return route.fulfill({ json: { success: true, room,
      viewer_balance: room.settlement_status === 'settled' ? 1012 : room.state === 'waiting' ? 1000 : 900 } });
  });
  return { room, requests, errors, addWin, finish, polls: () => polls };
}

async function enterSichuan(page: Page, start = true) {
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '四人麻将 单人挑战 / 多人同桌' }).click();
  await page.getByRole('button', { name: '四川血战', exact: true }).click();
  if (start) {
    await page.getByRole('button', { name: '月月陪玩', exact: true }).click();
    await page.getByRole('button', { name: '准备', exact: true }).click();
    await page.getByRole('button', { name: '开始本局', exact: true }).click();
  }
}

async function stageBounds(page: Page) {
  const bounds = await page.locator('.table-games').evaluate(element => {
    let stage = element.parentElement;
    while (stage && !getComputedStyle(stage).containerName.split(' ').includes('game-viewport')) stage = stage.parentElement;
    if (!stage) return null;
    const box = stage.getBoundingClientRect();
    return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height };
  });
  expect(bounds, '桌游必须位于自适应的game-viewport舞台内').not.toBeNull();
  const viewport = page.viewportSize()!;
  expect(bounds!.width, '牌桌铺满窗口宽度').toBeCloseTo(viewport.width, 0);
  expect(bounds!.height, '牌桌铺满窗口高度').toBeCloseTo(viewport.height, 0);
  expect(bounds!.left).toBeGreaterThanOrEqual(-1);
  expect(bounds!.top).toBeGreaterThanOrEqual(-1);
  expect(bounds!.right).toBeLessThanOrEqual(viewport.width + 1);
  expect(bounds!.bottom).toBeLessThanOrEqual(viewport.height + 1);
  expect(Math.abs((bounds!.left + bounds!.right) / 2 - viewport.width / 2)).toBeLessThanOrEqual(1);
  return bounds!;
}

async function controlsInStage(page: Page, selector: string) {
  const stage = await stageBounds(page);
  for (const control of await page.locator(selector).all()) {
    await expect(control).toBeInViewport({ ratio: 1 });
    const box = (await control.boundingBox())!;
    expect(box.height, '按钮与输入框不得通过整体缩放小于36px').toBeGreaterThanOrEqual(35.5);
    expect(box.x).toBeGreaterThanOrEqual(stage.left - 1);
    expect(box.y).toBeGreaterThanOrEqual(stage.top - 1);
    expect(box.x + box.width).toBeLessThanOrEqual(stage.right + 1);
    expect(box.y + box.height).toBeLessThanOrEqual(stage.bottom + 1);
    if (await control.isEnabled()) await control.click({ trial: true });
  }
}

async function fullHandInStage(page: Page, count = 14) {
  await waitForTableMotion(page);
  const stage = await stageBounds(page);
  const cards = page.locator('.tg-hand-card');
  await expect(cards).toHaveCount(count);
  const data = await page.locator('.tg-my-hand').evaluate(element => ({
    scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, scrollLeft: element.scrollLeft,
    cards: Array.from(element.querySelectorAll<HTMLImageElement>('.tg-hand-card img')).map(image => {
      const box = image.getBoundingClientRect();
      const tile = image.closest('.tg-hand-card')!.getBoundingClientRect();
      const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      return { label: image.alt, loaded: image.complete && image.naturalWidth > 0,
        x: box.x, y: box.y, w: box.width, h: box.height,
        faceWidthRatio: box.width / tile.width, faceHeightRatio: box.height / tile.height,
        readable: image.closest('.tg-hand-card')!.contains(top) };
    }),
  }));
  expect(data.scrollWidth, '全部手牌一次适配舞台，不能依赖横向滚动').toBeLessThanOrEqual(data.clientWidth + 1);
  expect(data.scrollLeft).toBe(0);
  for (const card of data.cards) {
    expect(card.loaded, `${card.label}牌图必须加载成功`).toBe(true);
    expect(card.readable, `${card.label}牌面不可被相邻牌或操作区域盖住`).toBe(true);
    expect(card.faceWidthRatio, '花色应铺满牌面，不能被通用按钮内边距挤成小图标').toBeGreaterThan(.9);
    expect(card.faceHeightRatio, '花色高度仅扣除牌壳边缘').toBeGreaterThan(.8);
    expect(card.w).toBeGreaterThan(0);
    expect(card.h).toBeGreaterThan(0);
    expect(card.x).toBeGreaterThanOrEqual(stage.left - 1);
    expect(card.y).toBeGreaterThanOrEqual(stage.top - 1);
    expect(card.x + card.w).toBeLessThanOrEqual(stage.right + 1);
    expect(card.y + card.h).toBeLessThanOrEqual(stage.bottom + 1);
  }
  await expect(page.locator('.tg-seat')).toHaveCount(4);
  expect(await page.locator('.tg-seat').evaluateAll(elements => elements.map(element => element.getAttribute('data-position'))))
    .toEqual(['south', 'east', 'north', 'west']);
  const seats = await page.locator('.tg-seat').evaluateAll(elements => elements.map(element => {
    const box = element.querySelector('.tg-avatar')!.getBoundingClientRect();
    return { position: element.getAttribute('data-position'), x: box.left + box.width / 2, y: box.top + box.height / 2 };
  }));
  expect(seats.find(seat => seat.position === 'east')!.x).toBeGreaterThan((stage.left + stage.right) / 2);
  expect(seats.find(seat => seat.position === 'west')!.x).toBeLessThan((stage.left + stage.right) / 2);
  expect(seats.find(seat => seat.position === 'north')!.y).toBeLessThan(seats.find(seat => seat.position === 'south')!.y);
  const panels = await page.locator('.tg-player').evaluateAll(elements => elements.map(element => {
    const box = element.getBoundingClientRect();
    const table = element.closest('.tg-scroll')!.getBoundingClientRect();
    const avatar = element.querySelector('.tg-avatar')!;
    const face = avatar.getBoundingClientRect();
    const corners = [[face.left + 3, face.top + 3], [face.right - 3, face.top + 3],
      [face.left + 3, face.bottom - 3], [face.right - 3, face.bottom - 3]];
    return { position: element.closest('.tg-seat')!.getAttribute('data-position'),
      top: box.top, bottom: box.bottom, tableTop: table.top, tableBottom: table.bottom,
      avatarVisible: corners.every(([x, y]) => avatar.contains(document.elementFromPoint(x!, y!))) };
  }));
  for (const panel of panels) {
    expect(panel.top, `${panel.position}玩家信息不能被顶部栏或牌桌裁切`).toBeGreaterThanOrEqual(panel.tableTop - 1);
    expect(panel.bottom, `${panel.position}玩家信息不能被底栏裁切`).toBeLessThanOrEqual(panel.tableBottom + 1);
    expect(panel.avatarVisible, `${panel.position}玩家头像四角必须完整可见`).toBe(true);
  }
}

async function noticeUnobstructed(page: Page, notice: Locator) {
  await expect(notice).toBeInViewport({ ratio: 1 });
  const collisions = await notice.evaluate(element => {
    const box = element.getBoundingClientRect();
    return Array.from(document.querySelectorAll('.tg-hand-card, .tg-player, .tg-control-panel button, .tg-toolbar button, .tg-river-tile'))
      .filter(target => {
        if (!target.getClientRects().length) return false;
        const other = target.getBoundingClientRect();
        return Math.min(box.right, other.right) - Math.max(box.left, other.left) > 1
          && Math.min(box.bottom, other.bottom) - Math.max(box.top, other.top) > 1;
      }).map(target => target.className);
  });
  expect(collisions, '中途胡牌横幅不可遮挡头像、手牌、弃牌和真实控件').toEqual([]);
}

async function sync(page: Page) {
  const request = page.waitForResponse(response => response.url().endsWith('/api/tables/SC1234'));
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await request;
}

for (const viewport of viewports) {
  test(`四川血战 ${viewport.width}×${viewport.height} 舞台、定缺、完整手牌及中途胡牌`, async ({ page }) => {
    test.setTimeout(25_000);
    await page.setViewportSize(viewport);
    const mock = await mockSichuan(page);
    await enterSichuan(page, false);
    await controlsInStage(page, '.tg-variant-tabs button, .tg-tier-card, .tg-mode-card, .tg-lobby-footer button, .tg-toolbar button');
    await page.getByRole('button', { name: '玩法规则', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('108');
    await expect(page.getByRole('dialog')).toContainText('换三张');
    await page.getByRole('button', { name: '关闭规则', exact: true }).click();
    await page.getByRole('button', { name: '月月陪玩', exact: true }).click();
    expect(mock.requests.find(request => request.path.endsWith('/create'))?.body)
      .toEqual({ game_type: 'sichuan_mahjong', mode: 'solo', include_yueyue: true, room_tier: 'beginner' });
    await page.getByRole('button', { name: '准备', exact: true }).click();
    await page.getByRole('button', { name: '开始本局', exact: true }).click();
    await expectRealMotion(page.locator('.tg-hand-card'));
    await fullHandInStage(page);
    await controlsInStage(page, '.tg-dingque-actions button, .tg-toolbar button');
    await expect(page.locator('.tg-hand-card:enabled')).toHaveCount(0);
    await captureFinalScreenshot(page, `sichuan-stage-dingque-${viewport.width}x${viewport.height}.png`);
    const revision = mock.room.revision;
    await page.getByRole('button', { name: '缺条', exact: true }).click();
    expect(mock.requests.find(request => request.body.action === 'dingque')?.body)
      .toEqual({ room_id: 'SC1234', action: 'dingque', suit: 's', expected_revision: revision });
    await page.getByRole('button', { name: '1万，第1张', exact: true }).click();
    await expect(page.getByRole('button', { name: '弃选中的牌', exact: true })).toBeDisabled();
    expect(mock.requests.filter(request => request.body.action === 'discard')).toHaveLength(0);
    await page.getByRole('button', { name: '3条，第11张', exact: true }).click();
    await expect(page.getByRole('button', { name: '弃选中的牌', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '清空选择', exact: true }).click();
    mock.addWin(1);
    await sync(page);
    const notice = page.getByLabel('血战途中胡牌', { exact: true });
    await expect(notice).toContainText('灵石待结算');
    await expect(notice).toContainText('胡5筒');
    await expectRealMotion(notice);
    await waitForTableMotion(page);
    await noticeUnobstructed(page, page.locator('.tg-win-notice'));
    await expect(page.locator('.tg-seat[data-has-won="true"]')).toContainText('第1胡');
    await expect(page.locator('.tg-dock')).toContainText('余额 900');
    await expect(page.locator('.tg-round-result')).toHaveCount(0);
    await captureFinalScreenshot(page, `sichuan-stage-midwin-${viewport.width}x${viewport.height}.png`);
    await controlsInStage(page, '.tg-toolbar button, .tg-control-panel button');
    expect(mock.errors).toEqual([]);
  });
}

test('四川血战新胡牌排队，普通轮询与刷新恢复不重复播放', async ({ page }) => {
  test.setTimeout(25_000);
  await page.setViewportSize(viewports[1]!);
  await recordCardAnimations(page);
  const mock = await mockSichuan(page);
  await enterSichuan(page);
  await waitForTableMotion(page);
  await page.getByRole('button', { name: '缺条', exact: true }).click();
  mock.addWin(1); mock.addWin(2);
  await sync(page);
  const notice = page.locator('.tg-win-notice');
  await expect(notice).toHaveAttribute('data-win-event-id', '1');
  await expectRealMotion(notice);
  const polls = mock.polls();
  await expect.poll(mock.polls).toBeGreaterThan(polls);
  await expect(notice).toHaveAttribute('data-win-event-id', '1');
  await expect(notice).toHaveAttribute('data-win-event-id', '2', { timeout: 5000 });
  await expectRealMotion(notice);
  await expect(notice).toHaveCount(0, { timeout: 5000 });
  const events = await page.evaluate(() => (window as any).__resultAnimations.length);
  expect(events).toBeGreaterThan(0);
  await sync(page);
  await expect(notice).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__resultAnimations.length)).toBe(events);
  await page.reload();
  await page.getByRole('button', { name: '四人麻将 单人挑战 / 多人同桌' }).click();
  await expect(page.locator('.game-sichuan_mahjong')).toBeVisible();
  await expect(page.locator('.tg-seat[data-has-won="true"]')).toHaveCount(2);
  await expect(notice).toHaveCount(0);
  await waitForTableMotion(page);
  expect(await page.evaluate(() => (window as any).__resultAnimations)).toEqual([]);
  expect(await page.evaluate(() => (window as any).__cardAnimations)).toEqual([]);
  expect(mock.requests.filter(request => request.path.endsWith('/join'))).toHaveLength(1);
  expect(mock.errors).toEqual([]);
});

test('四川血战本人已胡只读，结算到账采用实际净额，退出可切回基础麻将', async ({ page }) => {
  test.setTimeout(25_000);
  await page.setViewportSize(viewports[1]!);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await recordCardAnimations(page);
  const mock = await mockSichuan(page);
  await enterSichuan(page);
  await page.getByRole('button', { name: '缺条', exact: true }).click();
  mock.addWin(0);
  await sync(page);
  await expect(page.locator('.tg-blood-progress')).toContainText('1/3');
  await expect(page.locator('.tg-hand-card:enabled')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '弃选中的牌', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('血战途中胡牌', { exact: true })).toHaveAttribute('data-animated', 'false');
  expect(await page.locator('.tg-win-notice').evaluate(element => element.getAnimations({ subtree: true }).filter(animation => animation.playState === 'running').length)).toBe(0);
  await expect(page.locator('.tg-dock')).toContainText('余额 900');
  await expect(page.locator('.tg-round-result')).toHaveCount(0);
  mock.addWin(1); mock.addWin(2); mock.finish(false);
  mock.room.game.players[0].score_delta = 999;
  await sync(page);
  const result = page.locator('.tg-round-result .round-feedback');
  await expect(result).toContainText('血战结算');
  await expect(result).toContainText('结算处理中');
  await expect(page.locator('.tg-win-notice')).toHaveCount(0);
  mock.finish(true);
  await sync(page);
  await expect(result).toContainText('本局实际净赢 12');
  expect(mock.room.game.players[0].score_delta).not.toBe(12);
  await expect(page.locator('.tg-dock')).toContainText('余额 1012');
  await expectResultReadable(page, result);
  await fullHandInStage(page);
  await captureFinalScreenshot(page, 'sichuan-stage-final-settlement.png');
  await page.getByRole('button', { name: '离开房间', exact: true }).click();
  await expect(page.getByRole('button', { name: '四川血战', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '基础麻将', exact: true }).click();
  await page.getByRole('button', { name: '月月陪玩', exact: true }).click();
  expect(mock.requests.filter(request => request.path.endsWith('/create')).map(request => request.body.game_type))
    .toEqual(['sichuan_mahjong', 'mahjong']);
  expect(await page.evaluate(() => (window as any).__cardAnimations)).toEqual([]);
  expect(mock.errors).toEqual([]);
});
