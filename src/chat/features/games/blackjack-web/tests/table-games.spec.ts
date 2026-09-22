import { expect, test, type Page } from '@playwright/test';
import { captureFinalScreenshot, expectIllustrationVisible, waitForTableMotion } from './animation-helpers';

const games = [
  { type: 'texas', title: '德州扑克', action: '过牌', count: 2, hand: ['ClubA', 'Spade10'] },
  { type: 'golden_flower', title: '炸金花', action: '跟注', count: 2, hand: ['ClubA', 'Spade10', 'Heart3'] },
  { type: 'landlord', title: '斗地主', action: '出牌', count: 3, hand: ['Club3', 'Diamond3', 'Heart3', 'Spade3', 'Club4', 'Diamond4', 'Heart5', 'Spade6', 'Club7', 'Diamond8', 'Heart9', 'Spade10', 'ClubJ', 'DiamondQ', 'HeartK', 'SpadeA', 'Club2', 'Diamond2', 'JokerSmall', 'JokerBig'] },
  { type: 'mahjong', title: '四人麻将', action: '弃选中的牌', count: 4, hand: ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3', 's7', 's8', 's9', 'z1', 'z1'] },
];
const landscapeViewports = [
  { width: 568, height: 320 },
  { width: 667, height: 375 },
  { width: 844, height: 390 },
  { width: 1024, height: 600 },
  { width: 1440, height: 900 },
];

async function reachable(page: Page) {
  await waitForTableMotion(page);
  const extreme = page.viewportSize()!.height <= 320;
  for (const control of await page.locator('.tg-toolbar button, .tg-dock button:not(.tg-hand-card), .tg-dock input, .tg-dock select').all()) {
    await expect(control).toBeInViewport({ ratio: 1 });
    const bounds = await control.boundingBox();
    expect(bounds?.height, '横屏操作控件触控高度至少36像素').toBeGreaterThanOrEqual(35.5);
    if (await control.isEnabled()) await control.click({ trial: true });
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  if (!extreme) {
    const geometry = await page.locator('.tg-scroll').evaluate(element => ({
      scrollHeight: element.scrollHeight, height: element.clientHeight,
      top: element.scrollTop, pageTop: window.scrollY,
      documentHeight: document.documentElement.scrollHeight, viewportHeight: window.innerHeight,
    }));
    expect(geometry.scrollHeight, '正常横屏牌桌不应需要纵向滚动').toBeLessThanOrEqual(geometry.height + 1);
    expect(geometry.documentHeight, '正常横屏页面不应需要纵向滚动').toBeLessThanOrEqual(geometry.viewportHeight + 1);
    expect(geometry.top).toBe(0);
    expect(geometry.pageTop).toBe(0);
  }
}

async function expectWholeHand(page: Page, count: number, selectable: boolean) {
  await waitForTableMotion(page);
  const hand = page.locator('.tg-my-hand');
  const cards = hand.locator('.tg-hand-card');
  await expect(cards).toHaveCount(count);
  await expect.poll(() => cards.locator('img').evaluateAll(elements => elements.every(element => {
    const face = element as HTMLImageElement;
    return face.complete && face.naturalWidth > 0;
  })), { message: '等待全部手牌图片加载完成' }).toBe(true);
  const geometry = await hand.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const cardElements = Array.from(element.querySelectorAll<HTMLElement>('.tg-hand-card'));
    return {
      scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, scrollLeft: element.scrollLeft,
      left: bounds.left, right: bounds.right,
      cards: cardElements.map(card => {
        const face = card.querySelector('img');
        const rect = (face ?? card).getBoundingClientRect();
        const clickable = card.getBoundingClientRect();
        const x = rect.left + Math.min(6, rect.width * .12);
        const y = rect.top + rect.height * .17;
        const top = document.elementFromPoint(x, y);
        return {
          label: card.getAttribute('aria-label'), x: rect.x, y: rect.y, width: rect.width, height: rect.height,
          faceLoaded: !face || (face.complete && face.naturalWidth > 0),
          rankVisible: top === card || card.contains(top),
          clickableLeft: clickable.left, clickableRight: clickable.right,
          scrollLeft: card.scrollLeft,
        };
      }),
    };
  });
  expect(geometry.scrollWidth, `${count} 张手牌必须一次适配容器宽度`).toBeLessThanOrEqual(geometry.clientWidth + 1);
  expect(geometry.scrollLeft, '不得预先把牌列滚到中间').toBe(0);
  const viewport = page.viewportSize()!;
  for (const card of geometry.cards) {
    expect(card.faceLoaded, `${card.label} 牌图应加载成功`).toBe(true);
    expect(card.width).toBeGreaterThan(0);
    expect(card.height).toBeGreaterThan(0);
    expect(card.x).toBeGreaterThanOrEqual(Math.max(0, geometry.left) - 1);
    expect(card.y).toBeGreaterThanOrEqual(0);
    expect(card.x + card.width, `${card.label} 右边界不能被裁切`).toBeLessThanOrEqual(Math.min(viewport.width, geometry.right) + 1);
    expect(card.y + card.height).toBeLessThanOrEqual(viewport.height + 1);
    expect(card.rankVisible, `${card.label} 左上点数必须露出，不能被相邻牌/操作区覆盖`).toBe(true);
    expect(card.scrollLeft).toBe(0);
  }
  if (!selectable) return;
  // 用露出的真实点击条操作首、中、末牌；不用滚动、force 或合成点击绕过遮挡。
  for (const index of [0, Math.floor(count / 2), count - 1]) {
    const card = cards.nth(index);
    const bounds = await card.boundingBox();
    await expect(card).toHaveAttribute('aria-pressed', 'false');
    const position = { x: Math.min(6, bounds!.width * .25), y: bounds!.height * .45 };
    await card.click({ position });
    await expect(card).toHaveAttribute('aria-pressed', 'true');
    await card.click({ position });
    await expect(card).toHaveAttribute('aria-pressed', 'false');
  }
  expect(await hand.evaluate(element => element.scrollLeft)).toBe(0);
}

async function expectAroundTable(page: Page, count: number) {
  await waitForTableMotion(page);
  // 检查实际几何关系，防止窄屏退回纵向列表或玩家头像相互遮挡。
  const seats = await page.locator('.tg-seat').evaluateAll(elements => elements.map(element => {
    const avatar = element.querySelector('.tg-avatar')!.getBoundingClientRect();
    return { self: element.classList.contains('tg-self'), x: avatar.x, y: avatar.y, w: avatar.width, h: avatar.height };
  }));
  expect(seats).toHaveLength(count);
  const viewport = page.viewportSize()!;
  for (const seat of seats) {
    expect(seat.x, '所有头像必须同时位于视窗中').toBeGreaterThanOrEqual(0);
    expect(seat.y).toBeGreaterThanOrEqual(0);
    expect(seat.x + seat.w).toBeLessThanOrEqual(viewport.width + 1);
    expect(seat.y + seat.h).toBeLessThanOrEqual(viewport.height + 1);
  }
  const self = seats.find(seat => seat.self)!;
  const others = seats.filter(seat => !seat.self);
  expect(others.every(seat => seat.y < self.y)).toBe(true);
  if (count >= 3) {
    expect(others.some(seat => seat.x + seat.w / 2 < viewport.width / 2), '对手座位应覆盖左半场').toBe(true);
    expect(others.some(seat => seat.x + seat.w / 2 > viewport.width / 2), '对手座位应覆盖右半场').toBe(true);
  }
  for (let i = 0; i < seats.length; i++) {
    for (let j = i + 1; j < seats.length; j++) {
      const a = seats[i]!, b = seats[j]!;
      expect(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y, '头像不能重叠').toBe(true);
    }
  }
}

async function expectPortraitNotice(page: Page) {
  const notice = page.locator('.landscape-notice');
  await expect(notice).toBeVisible();
  await expect(notice).toHaveAttribute('aria-label', '横屏游玩提示');
  await expect(notice).toContainText('旋转手机');
  await expect(notice).toBeInViewport({ ratio: 1 });
  expect(await notice.evaluate(element => element.contains(document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2))), '竖屏提示应覆盖牌桌中央').toBe(true);
}

async function expectSeatPlaysUnobstructed(page: Page) {
  await waitForTableMotion(page);
  // 分别检查牌面和标签，不能与头像、余牌数、倒计时、手牌或操作按钮挤在一起。
  const overlaps = await page.evaluate(() => {
    const visible = (element: Element) => element.getClientRects().length > 0;
    const played = Array.from(document.querySelectorAll('.tg-seat-play img, .tg-play-label')).filter(visible);
    const blockers = Array.from(document.querySelectorAll('.tg-player, .tg-hidden-hand, .tg-countdown, .tg-control-panel button, .tg-my-hand, .tg-table-status')).filter(visible);
    return played.flatMap(element => {
      const a = element.getBoundingClientRect();
      return blockers.filter(blocker => {
        const b = blocker.getBoundingClientRect();
        return Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
      }).map(blocker => `${element.getAttribute('alt') || element.textContent} 与 ${blocker.className} 重叠`);
    });
  });
  expect(overlaps, '出牌应紧邻所属玩家且不遮挡其他信息').toEqual([]);
}

for (const spec of games) {
  test(`${spec.title} 横屏规则、月月房间、操作与旋转刷新恢复`, async ({ page }) => {
    test.setTimeout(30_000);
    await page.setViewportSize({ width: 667, height: 375 });
    const uid = '123456789012345678';
    let calls = 0;
    let rejoined = 0;
    const room = {
      room_id: 'TABLE1', game_type: spec.type, host_user_id: uid, state: 'waiting',
      revision: 1, mode: 'solo', include_yueyue: true, min_players: spec.count, max_players: spec.count,
      stake: 100, buy_in: 100, settlement_status: 'none', turn_deadline: Math.floor(Date.now() / 1000) + 60,
      room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
      players: Array.from({ length: spec.count }, (_, index) => ({
        user_id: index ? `bot:${index}` : uid, username: index ? index === 1 ? '月月' : `陪玩${index}` : '测试玩家',
        avatar_url: '/character/normal.webp', is_bot: index > 0, is_ready: index > 0, connected: true,
      })),
      game: null as any,
    };
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '/character/normal.webp', balance: 2000 } });
      if (path === '/api/tables/leave') return route.fulfill({ json: { success: true, room: null, viewer_balance: 2000 } });
      if (!path.startsWith('/api/tables/')) return route.fulfill({ status: 404, json: { detail: '未模拟接口' } });
      if (path.endsWith('/create')) {
        expect(route.request().postDataJSON()).toEqual({ game_type: spec.type, mode: 'solo', include_yueyue: true, room_tier: 'beginner' });
      }
      if (path.endsWith('/join')) rejoined++;
      if (path.endsWith('/ready')) { room.players[0]!.is_ready = true; room.revision++; }
      if (path.endsWith('/start')) {
        room.state = 'playing'; room.settlement_status = 'reserved'; room.revision++;
        room.game = {
          phase: 'playing', finished: false, current_player_id: uid, winners: [], message: '轮到你了',
          bottom_cards: spec.type === 'landlord' ? ['SpadeA', 'Club2', 'JokerBig'] : undefined,
          last_play: spec.type === 'landlord' ? { user_id: 'bot:1', cards: spec.hand } : undefined,
          seat_actions: spec.type === 'landlord' ? { 'bot:1': { action: 'play', cards: spec.hand, label: '出牌' }, 'bot:2': { action: 'pass', cards: [], label: '不出' } } : undefined,
          legal_actions: spec.type === 'texas' ? ['check', 'raise', 'fold'] : spec.type === 'golden_flower' ? ['call', 'compare', 'fold'] : spec.type === 'landlord' ? ['play', 'pass'] : ['discard'],
          min_raise_to: 4, max_raise_to: 100, call_amount: 1, compare_cost: 2,
          players: room.players.map(member => ({ user_id: member.user_id, hand: member.is_bot ? [] : spec.hand, hand_count: spec.hand.length, stack: 100, score: 0, discards: spec.type === 'mahjong' ? ['z2'] : [] })),
        };
      }
      if (path.endsWith('/action')) {
        calls++;
        const body = route.request().postDataJSON();
        expect(body.expected_revision).toBe(room.revision);
        if (spec.type === 'landlord') expect(body.cards).toEqual(['Club3']);
        if (spec.type === 'mahjong') expect(body.tile).toBe('m1');
        room.revision++;
        room.game.message = '已执行你的操作';
        if (spec.type === 'landlord') room.game.seat_actions[uid] = { action: 'play', cards: ['Club3'], label: '单张' };
      }
      return route.fulfill({ json: { success: true, room, viewer_balance: room.state === 'playing' ? 1900 : 2000 } });
    });

    await page.goto('/?dev_user_id=123456789012345678');
    await page.getByRole('button', { name: new RegExp(`^${spec.title}`) }).click();
    await page.getByRole('button', { name: '玩法规则', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('dialog')).toContainText('灵石');
    await page.getByRole('button', { name: '关闭规则' }).click();
    await page.getByRole('button', { name: /^月月陪玩/ }).click();
    await expect(page.locator('.tg-seat')).toHaveCount(spec.count);
    await reachable(page);
    await page.getByRole('button', { name: '准备', exact: true }).click();
    await page.getByRole('button', { name: '开始本局', exact: true }).click();
    await expect(page.locator('.tg-hidden-hand')).toHaveCount(spec.count - 1);
    if (spec.type === 'mahjong') {
      await expect(page.locator('.tg-my-hand img').first()).toHaveAttribute('src', '/mahjong/faces/m1.svg');
      await expect(page.locator('.tg-tile-row').first()).toContainText('南');
    }
    for (const viewport of landscapeViewports) {
      await page.setViewportSize(viewport);
      await reachable(page);
      await expectAroundTable(page, spec.count);
      if (spec.type === 'landlord') {
        const bottom = await page.getByLabel('地主底牌', { exact: true }).boundingBox();
        const clock = await page.getByLabel('出牌倒计时', { exact: true }).boundingBox();
        const hand = await page.locator('.tg-my-hand').boundingBox();
        expect(Math.abs(bottom!.x + bottom!.width / 2 - viewport.width / 2), '地主底牌须顶部居中').toBeLessThan(2);
        expect(bottom!.y + bottom!.height).toBeLessThan(viewport.height * .4);
        expect(clock!.y, '倒计时靠近手牌，不放在页顶').toBeGreaterThan(viewport.height * .4);
        expect(clock!.y + clock!.height, '倒计时应在手牌上方').toBeLessThanOrEqual(hand!.y + 12);
        await expect(page.locator('.tg-countdown strong')).toBeVisible();
        const played = await page.locator('.tg-played-cards').evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
        expect(played.scroll, '整组出牌也应一次显示').toBeLessThanOrEqual(played.width + 1);
        const west = await page.getByLabel('陪玩2的出牌', { exact: true }).boundingBox();
        const east = await page.getByLabel('月月的出牌', { exact: true }).boundingBox();
        expect(west!.x + west!.width).toBeLessThan(viewport.width / 2);
        expect(east!.x).toBeGreaterThan(viewport.width / 2);
        await expect(page.getByLabel('陪玩2的出牌', { exact: true })).toContainText('不出');
        await expect(page.getByLabel('月月的出牌', { exact: true }).locator('img')).toHaveCount(20);
        await expectSeatPlaysUnobstructed(page);
      }
      await expectWholeHand(page, spec.hand.length, ['landlord', 'mahjong'].includes(spec.type));
      await captureFinalScreenshot(page, `landscape-${spec.type}-${viewport.width}x${viewport.height}.png`);
    }
    if (['landlord', 'mahjong'].includes(spec.type)) await page.locator('.tg-my-hand button').first().click();
    await page.getByRole('button', { name: spec.action, exact: true }).click();
    await expect.poll(() => calls).toBe(1);
    if (spec.type === 'landlord') {
      await expect(page.getByLabel('测试玩家的出牌', { exact: true })).toContainText('单张');
      for (const viewport of landscapeViewports) {
        await page.setViewportSize(viewport);
        await reachable(page);
        await expectSeatPlaysUnobstructed(page);
        await captureFinalScreenshot(page, `landscape-landlord-seats-${viewport.width}x${viewport.height}.png`);
      }
    }

    const handBeforeRotation = await page.locator('.tg-my-hand button').evaluateAll(cards => cards.map(card => card.getAttribute('aria-label')));
    await page.setViewportSize({ width: 375, height: 667 });
    await expectPortraitNotice(page);
    await expect(page.locator('.tg-seat')).toHaveCount(spec.count);
    await expect(page.locator('.tg-title')).toContainText('TABLE1');
    expect(await page.locator('.tg-my-hand button').evaluateAll(cards => cards.map(card => card.getAttribute('aria-label')))).toEqual(handBeforeRotation);
    expect(calls).toBe(1);
    await captureFinalScreenshot(page, `landscape-${spec.type}-portrait-notice.png`);
    await page.setViewportSize({ width: 844, height: 390 });
    await expect(page.locator('.landscape-notice')).toBeHidden();
    await reachable(page);
    await expectAroundTable(page, spec.count);
    expect(calls).toBe(1);

    await page.reload();
    await page.getByRole('button', { name: new RegExp(`^${spec.title}`) }).click();
    await expect(page.locator('.tg-title')).toContainText('TABLE1');
    await expect.poll(() => rejoined).toBe(1);
    await reachable(page);
    await captureFinalScreenshot(page, `landscape-${spec.type}-reconnected.png`);
  });
}

test('德州8人窗口可达，自定义底分上限与实际结算正确显示', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  const uid = '123456789012345678';
  const players = Array.from({ length: 8 }, (_, index) => ({ user_id: index ? `22345678901234567${index}` : uid, username: `玩家${index + 1}`, avatar_url: '/character/normal.webp', is_bot: false, is_ready: true, connected: true }));
  const room = {
    room_id: 'EIGHT8', game_type: 'texas', host_user_id: uid, state: 'waiting', revision: 1,
    mode: 'multi', include_yueyue: false, min_players: 2, max_players: 8, buy_in: 500, stake: 500,
    room_tier: 'custom', base_stake: 5, entry_min: 500, loss_limit: 500,
    settlement_status: 'none', actual_settlement: {} as Record<string, number>, players, game: null as any,
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '玩家1', avatar_url: '/character/normal.webp', balance: 2000 } });
    if (path.endsWith('/create')) expect(route.request().postDataJSON()).toEqual({ game_type: 'texas', room_tier: 'custom', base_stake: 5, loss_limit: 500, mode: 'multi', include_yueyue: false });
    if (path.endsWith('/settings')) {
      expect(route.request().postDataJSON()).toEqual({ room_id: 'EIGHT8', base_stake: 10, loss_limit: 800 });
      room.buy_in = room.stake = 800;
      room.loss_limit = room.entry_min = 800;
      room.base_stake = 10;
      room.players[0]!.is_ready = false;
      room.revision++;
    }
    if (path.endsWith('/ready')) { room.players[0]!.is_ready = true; room.revision++; }
    if (path.endsWith('/start')) {
      room.state = 'playing'; room.revision++;
      room.game = { phase: 'preflop', finished: false, current_player_id: uid, winners: [], message: '轮到你了', legal_actions: ['fold'], players: players.map(player => ({ user_id: player.user_id, hand: player.user_id === uid ? ['SpadeA', 'HeartA'] : [], hand_count: 2, stack: 800 })) };
    }
    if (path.endsWith('/action')) {
      room.state = 'finished'; room.settlement_status = 'settled'; room.actual_settlement = { [uid]: -25 }; room.revision++;
      room.game.finished = true; room.game.legal_actions = []; room.game.players[0].score_delta = -800;
    }
    return route.fulfill({ json: { success: true, room, viewer_balance: room.state === 'playing' ? 1200 : room.state === 'finished' ? 1975 : 2000 } });
  });
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: /^德州扑克/ }).click();
  await page.getByRole('button', { name: '自定义房间', exact: true }).click();
  const custom = page.getByRole('dialog', { name: '自定义房间', exact: true });
  await custom.getByLabel('单局最多输', { exact: true }).fill('99');
  await expect(custom.getByRole('button', { name: '创建自定义房间', exact: true })).toBeDisabled();
  await custom.getByLabel('底分', { exact: true }).fill('5');
  await custom.getByLabel('单局最多输', { exact: true }).fill('500');
  await custom.getByLabel('邀请月月一起玩').uncheck();
  await custom.getByRole('button', { name: '创建自定义房间', exact: true }).click();
  await expect(page.locator('.tg-seat')).toHaveCount(8);
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const settings = page.getByRole('dialog', { name: '房间设置', exact: true });
  await settings.getByLabel('底分', { exact: true }).fill('10');
  await settings.getByLabel('单局最多输', { exact: true }).fill('800');
  await settings.getByRole('button', { name: '保存设置', exact: true }).click();
  await page.getByRole('button', { name: '准备', exact: true }).click();
  await page.getByRole('button', { name: '开始本局', exact: true }).click();
  await expect(page.getByRole('button', { name: '弃牌', exact: true })).toBeEnabled();
  for (const viewport of landscapeViewports) {
    await page.setViewportSize(viewport);
    await expectAroundTable(page, 8);
    await reachable(page);
    await captureFinalScreenshot(page, `landscape-texas-eight-${viewport.width}x${viewport.height}.png`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await expectAroundTable(page, 8);
  await reachable(page);
  await page.getByRole('button', { name: '弃牌', exact: true }).click();
  await expect(page.locator('.tg-seat').first()).toContainText('实际净变动 -25 灵石');
  await expect(page.locator('.tg-dock')).toContainText('余额 1975 灵石');
  await reachable(page);
  await captureFinalScreenshot(page, 'landscape-texas-eight-settled-desktop.png');
});

test('插画大厅与场次入口适配横屏，准入限制和创建参数一致', async ({ page }) => {
  const uid = '123456789012345678';
  let created = false;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '场次玩家', avatar_url: '/character/normal.webp', balance: 2000 } });
    if (path.endsWith('/create')) {
      expect(route.request().postDataJSON()).toEqual({ game_type: 'landlord', mode: 'multi', include_yueyue: true, room_tier: 'intermediate' });
      created = true;
    }
    return route.fulfill({ json: { success: true, viewer_balance: 2000, room: {
      room_id: 'MID123', game_type: 'landlord', host_user_id: uid, state: 'waiting', revision: 1,
      mode: 'multi', room_tier: 'intermediate', base_stake: 5, entry_min: 1000, loss_limit: 500,
      include_yueyue: true, min_players: 3, max_players: 3, game: null,
      players: [{ user_id: uid, username: '场次玩家', avatar_url: '', is_bot: false, is_ready: false, connected: true }],
    } } });
  });
  await page.goto('/?dev_user_id=123456789012345678');
  for (const viewport of [landscapeViewports[0]!, landscapeViewports[2]!, landscapeViewports[4]!]) {
    await page.setViewportSize(viewport);
    for (const name of ['21点', '德州扑克', '斗地主', '四人麻将', '炸金花']) {
      const entry = page.getByRole('button', { name: new RegExp(`^${name}`) });
      await expect(entry).toBeInViewport({ ratio: 1 });
      await entry.click({ trial: true });
      await expectIllustrationVisible(entry);
    }
    await captureFinalScreenshot(page, `lobby-svg-${viewport.width}x${viewport.height}.png`);
  }
  await page.getByRole('button', { name: /^斗地主/ }).click();
  for (const viewport of [landscapeViewports[0]!, landscapeViewports[2]!, landscapeViewports[4]!]) {
    await page.setViewportSize(viewport);
    for (const name of ['初级场', '中级场', '高级场']) {
      const entry = page.getByRole('button', { name: `选择${name}`, exact: true });
      await expect(entry).toBeInViewport({ ratio: 1 });
      await expectIllustrationVisible(entry);
      await entry.click({ trial: true });
    }
    for (const control of await page.locator('.tg-lobby button').all()) await expect(control).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('.tg-lobby')).not.toContainText('带入');
    await captureFinalScreenshot(page, `lobby-tiers-${viewport.width}x${viewport.height}.png`);
  }
  await page.getByRole('button', { name: '选择高级场', exact: true }).click();
  await expect(page.getByRole('button', { name: /^月月陪玩/ })).toBeDisabled();
  await expect(page.getByRole('button', { name: /^好友同桌/ })).toBeDisabled();
  await expect(page.locator('.tg-lobby')).toContainText('还需 3000 灵石');
  await page.getByRole('button', { name: '选择中级场', exact: true }).click();
  await page.getByRole('button', { name: /^好友同桌/ }).click();
  await expect.poll(() => created).toBe(true);
  await expect(page.locator('.tg-title')).toContainText('中级场');
  await page.getByRole('button', { name: '房间设置', exact: true }).click();
  const settings = page.getByRole('dialog', { name: '房间设置', exact: true });
  await expect(settings).toContainText('1000');
  await expect(settings).toContainText('500');
  await expect(settings.getByRole('button', { name: '保存设置', exact: true })).toBeDisabled();
  await expect(settings.getByLabel('全员准备后自动开启游戏')).not.toBeChecked();
});

test('单人房逐个移除陪玩，按选择人数添加且刷新不补回', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  const uid = '123456789012345678';
  const member = (id: string, name: string, bot: boolean) => ({ user_id: id, username: name, avatar_url: '/character/normal.webp', is_bot: bot, is_ready: bot, connected: true });
  const room = {
    room_id: 'BOTS01', game_type: 'landlord', host_user_id: uid, state: 'waiting', revision: 1,
    mode: 'solo', room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
    include_yueyue: true, min_players: 3, max_players: 3, game: null,
    players: [member(uid, '管理玩家', false), member('bot:yueyue', '月月', true), member('bot:companion:1', '陪玩1', true)],
  };
  const operations: unknown[] = [];
  let next = 2;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '管理玩家', avatar_url: '/character/normal.webp', balance: 2000 } });
    if (path.endsWith('/bots')) {
      const body = route.request().postDataJSON();
      operations.push(body);
      if (body.operation === 'remove') room.players = room.players.filter(player => player.user_id !== body.bot_id);
      else {
        expect(body).toEqual({ room_id: room.room_id, operation: 'add', count: 1 });
        const hasYueyue = room.players.some(player => player.user_id === 'bot:yueyue');
        room.players.push(hasYueyue ? member(`bot:companion:${next}`, `陪玩${next++}`, true) : member('bot:yueyue', '月月', true));
      }
      room.include_yueyue = room.players.some(player => player.user_id === 'bot:yueyue');
      room.players[0]!.is_ready = false;
      room.revision++;
    }
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
  });
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: /^斗地主/ }).click();
  await page.getByRole('button', { name: /^月月陪玩/ }).click();
  await page.getByRole('button', { name: '陪玩管理', exact: true }).click();
  const management = page.getByRole('dialog', { name: '陪玩管理', exact: true });
  await expect(management.getByRole('button', { name: '添加陪玩', exact: true })).toBeDisabled();
  await management.getByRole('button', { name: '移除 月月', exact: true }).click();
  await management.getByRole('button', { name: '移除 陪玩1', exact: true }).click();
  await expect(page.locator('.tg-seat')).toHaveCount(1);
  await management.getByLabel('添加人数', { exact: true }).selectOption('1');
  await management.getByRole('button', { name: '添加陪玩', exact: true }).click();
  await expect(page.locator('.tg-seat')).toHaveCount(2);
  await management.getByRole('button', { name: '关闭陪玩管理', exact: true }).click();
  await expect(page.getByRole('button', { name: '开始本局', exact: true })).toBeDisabled();
  await page.reload();
  await page.getByRole('button', { name: /^斗地主/ }).click();
  await expect(page.locator('.tg-seat')).toHaveCount(2);
  await page.getByRole('button', { name: '陪玩管理', exact: true }).click();
  for (const viewport of [landscapeViewports[0]!, landscapeViewports[2]!]) {
    await page.setViewportSize(viewport);
    await expect(management.getByRole('button', { name: '添加陪玩', exact: true })).toBeInViewport({ ratio: 1 });
    await management.getByRole('button', { name: '添加陪玩', exact: true }).click({ trial: true });
    await captureFinalScreenshot(page, `lobby-bot-manager-${viewport.width}x${viewport.height}.png`);
  }
  await management.getByRole('button', { name: '添加陪玩', exact: true }).click();
  await expect(page.locator('.tg-seat')).toHaveCount(3);
  await expect(management.getByRole('button', { name: '添加陪玩', exact: true })).toBeDisabled();
  expect(operations).toEqual([
    { room_id: room.room_id, operation: 'remove', bot_id: 'bot:yueyue' },
    { room_id: room.room_id, operation: 'remove', bot_id: 'bot:companion:1' },
    { room_id: room.room_id, operation: 'add', count: 1 },
    { room_id: room.room_id, operation: 'add', count: 1 },
  ]);
});
