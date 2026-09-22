import { expect, test, type Locator, type Page } from '@playwright/test';
import { captureFinalScreenshot, waitForTableMotion } from './animation-helpers';

const viewports = [
  { width: 568, height: 320 },
  { width: 667, height: 375 },
  { width: 844, height: 390 },
  { width: 1024, height: 400 },
  { width: 1024, height: 600 },
  { width: 1440, height: 900 },
];
// 12 张牌用于布局压力验证；本用例不验证这副已爆牌牌组的游戏规则。
const longBlackjackHand = ['ClubA', 'DiamondA', 'HeartA', 'SpadeA', 'Club2', 'Diamond2', 'Heart2', 'Spade2', 'Club3', 'Diamond3', 'Heart3', 'Spade3'];

async function expectReachable(control: Locator) {
  await expect(control).toBeInViewport({ ratio: 1 });
  const bounds = await control.boundingBox();
  expect(bounds?.height, '横屏操作控件触控高度至少36像素').toBeGreaterThanOrEqual(35.5);
  if (await control.isEnabled()) {
    await control.click({ trial: true });
  }
}

async function expectControlsReachable(page: Page) {
  await waitForTableMotion(page);
  const stage = (await page.locator('.game-viewport-stage').boundingBox())!;
  const viewport = page.viewportSize()!;
  const hasMessage = await page.locator('.table-fullscreen > .status-message, .table-fullscreen > .error-message').count();
  expect(stage.x, '牌桌从窗口左边缘开始').toBeCloseTo(0, 0);
  expect(stage.y, '牌桌从窗口顶部开始').toBeCloseTo(0, 0);
  expect(stage.width, '超宽屏牌桌必须铺满宽度').toBeCloseTo(viewport.width, 0);
  expect(stage.height, '仅有实际状态消息时才预留底栏').toBeCloseTo(viewport.height - (hasMessage ? 24 : 0), 0);
  const extreme = page.viewportSize()!.height <= 320;
  await expect(page.locator('.action-dock')).toBeInViewport();
  for (const control of await page.locator('.table-toolbar button, .action-dock button, .action-dock input').all()) {
    await expectReachable(control);
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow, '窗口不应出现横向溢出').toBe(false);
  if (!extreme) {
    const geometry = await page.locator('.table-scroll').evaluate(element => ({
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

async function expectAllBlackjackCardsVisible(page: Page, expectedCount: number) {
  await waitForTableMotion(page);
  const expectedHands = await page.locator('.multi-mode-view').count() ? 4 : 2;
  await expect(page.locator('.playing-card')).toHaveCount(expectedHands * expectedCount);
  await expect.poll(() => page.locator('.playing-card').evaluateAll(elements => elements.every(element => {
    const card = element as HTMLImageElement;
    return card.complete && card.naturalWidth > 0;
  })), { message: '等待全部黑杰克牌图加载完成' }).toBe(true);
  const hands = page.locator('.card-hand');
  let longHandCount = 0;
  for (const hand of await hands.all()) {
    const cards = hand.locator('.playing-card');
    if (!(await cards.count())) continue;
    await expect(cards).toHaveCount(expectedCount);
    longHandCount++;
    const geometry = await hand.evaluate(element => {
      const bounds = element.getBoundingClientRect();
      return {
        scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, scrollLeft: element.scrollLeft,
        left: bounds.left, right: bounds.right,
        cards: Array.from(element.querySelectorAll<HTMLImageElement>('.playing-card')).map(card => {
          const rect = card.getBoundingClientRect();
          const top = document.elementFromPoint(rect.left + Math.min(5, rect.width * .12), rect.top + rect.height * .17);
          return { label: card.alt, x: rect.x, y: rect.y, width: rect.width, height: rect.height, rankVisible: top === card, loaded: card.complete && card.naturalWidth > 0 };
        }),
      };
    });
    expect(geometry.scrollWidth, `${expectedCount} 张黑杰克牌必须全部适配当前牌列`).toBeLessThanOrEqual(geometry.clientWidth + 1);
    expect(geometry.scrollLeft).toBe(0);
    const viewport = page.viewportSize()!;
    for (const card of geometry.cards) {
      expect(card.loaded, `${card.label} 图片必须加载成功`).toBe(true);
      expect(card.x).toBeGreaterThanOrEqual(Math.max(0, geometry.left) - 1);
      expect(card.y).toBeGreaterThanOrEqual(0);
      expect(card.x + card.width, `${card.label} 不得被牌列右边界裁切`).toBeLessThanOrEqual(Math.min(viewport.width, geometry.right) + 1);
      expect(card.y + card.height).toBeLessThanOrEqual(viewport.height + 1);
      expect(card.rankVisible, `${card.label} 左上点数必须露出`).toBe(true);
    }
  }
  expect(longHandCount, '荷官及所有已有玩家都应测试长手牌').toBe(expectedHands);
}

async function expectActionsAboveOwnHand(page: Page) {
  await expect(page.locator('.table-center-mark'), '操作时隐藏重复桌心装饰，避免遮挡回合提示').toHaveCount(0);
  const hit = await page.getByRole('button', { name: '要牌', exact: true }).boundingBox();
  const stand = await page.getByRole('button', { name: '停牌', exact: true }).boundingBox();
  const cards = page.locator('.seat-bottom .playing-card');
  const first = (await cards.first().boundingBox())!;
  const last = (await cards.last().boundingBox())!;
  const row = (await page.locator('.action-dock:not(.betting-dock) > .action-row').boundingBox())!;
  expect(hit).toBeTruthy(); expect(stand).toBeTruthy();
  expect(row.y + row.height, '要牌停牌必须在本人手牌上方').toBeLessThanOrEqual(first.y);
  expect(Math.abs(row.x + row.width / 2 - (first.x + last.x + last.width) / 2), '操作按钮与本人手牌共用中央轴线').toBeLessThanOrEqual(2);
  expect(first.y - row.y - row.height, '按钮紧靠手牌，不能飘到远处').toBeLessThanOrEqual(65);
}

async function expectBettingAwayFromDiscordRail(page: Page) {
  const width = page.viewportSize()!.width;
  const overlay = await page.evaluate(() => {
    const rail = document.createElement('div');
    rail.id = 'discord-test-rail';
    rail.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:8vw;z-index:99999;background:#5558';
    document.body.append(rail);
    return rail.id;
  });
  for (const control of await page.locator('.multi-mode-view .betting-dock button, .multi-mode-view .betting-dock input').all()) {
    const box = (await control.boundingBox())!;
    expect(box.x + box.width, '下注准备按钮完整避开右侧8%覆盖区域').toBeLessThanOrEqual(width * .92);
    await expectReachable(control);
  }
  await page.locator(`#${overlay}`).evaluate(element => element.remove());
}

async function expectBlackjackSeats(page: Page, multiplayer: boolean) {
  await waitForTableMotion(page);
  const viewport = page.viewportSize()!;
  const seats = await page.locator(multiplayer ? '.seat-area:not(.empty-seat-area) .seat-player-avatar' : '.single-player-seat .seat-player-avatar').evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect();
    return { self: Boolean(element.closest('.viewer-seat, .single-player-seat')), x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  }));
  const self = seats.find(seat => seat.self)!;
  expect(self, '当前玩家必须在牌桌有独立座位').toBeTruthy();
  for (const seat of seats) {
    expect(seat.x).toBeGreaterThanOrEqual(0);
    expect(seat.y).toBeGreaterThanOrEqual(0);
    expect(seat.x + seat.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(seat.y + seat.height).toBeLessThanOrEqual(viewport.height + 1);
    if (!seat.self) expect(seat.y, '本人始终处于其他玩家下方').toBeLessThan(self.y);
  }
  const dealer = await page.locator('.table-dealer-image').boundingBox();
  expect(dealer!.y).toBeLessThan(self.y);
  for (let first = 0; first < seats.length; first++) {
    for (const second of seats.slice(first + 1)) {
      const seat = seats[first]!;
      expect(seat.x + seat.width <= second.x || second.x + second.width <= seat.x || seat.y + seat.height <= second.y || second.y + second.height <= seat.y, '玩家头像不能重叠').toBe(true);
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

async function mockApi(page: Page, withOpenSeat = false, withLongHands = false) {
  const userId = '123456789012345678';
  const profile = { success: true, user_id: userId, username: '测试玩家', avatar_url: '/character/normal.webp', balance: 1000 };
  const game = {
    user_id: userId,
    bet_amount: 10,
    game_state: 'player_turn',
    player_hand: withLongHands ? [...longBlackjackHand] : ['Spade2', 'Heart3'],
    dealer_hand: withLongHands ? [...longBlackjackHand] : ['SpadeA', 'Hidden'],
    player_score: 5,
    dealer_score: 11,
  };
  const room = {
    room_id: 'ABC123',
    host_user_id: userId,
    max_players: 3,
    state: 'waiting',
    current_turn_user_id: null as string | null,
    ready_player_count: 2,
    all_players_ready: false,
    dealer: { name: '月月', avatar_path: '/character/normal.webp', expression: 'normal', hand: [] as string[], score: 0 },
    players: (withOpenSeat ? [userId] : [userId, '223456789012345678', '323456789012345678']).map((id, index) => ({
      user_id: id,
      username: index ? `玩家${index}很长的名字用于检查窄窗口换行` : profile.username,
      avatar_url: '/character/normal.webp',
      seat_index: index,
      bet_amount: index ? 10 : 0,
      hand: [] as string[],
      score: 0,
      status: 'waiting',
      result: null as string | null,
      payout_amount: 0,
      is_ready: index > 0,
      is_current_turn: false,
      is_bot: false,
    })),
  };

  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() || {};
    if (path === '/api/profile') return route.fulfill({ json: profile });
    if (path === '/api/game/current') return route.fulfill({ json: { success: true, game: null } });
    if (path.startsWith('/api/game/')) {
      if (path.endsWith('/start')) profile.balance -= game.bet_amount;
      if (path.endsWith('/hit')) game.player_hand.push('Club2');
      if (path.endsWith('/stand')) {
        game.game_state = 'finished_win';
        game.dealer_hand = ['SpadeA', 'Club8'];
      }
      return route.fulfill({ json: { success: true, game, new_balance: profile.balance } });
    }
    if (path.startsWith('/api/multi/room/')) {
      if (path.endsWith('/bot')) {
        room.players = room.players.filter(player => !player.is_bot);
        if (body.include_yueyue) room.players.push({ ...room.players[0]!, user_id: '-1', username: '月月（陪玩）', seat_index: 1, is_bot: true, is_ready: true });
      }
      if (path.endsWith('/bet')) room.players[0]!.bet_amount = body.amount;
      if (path.endsWith('/ready') || path.endsWith('/continue-ready')) {
        room.state = 'waiting';
        room.players[0]!.is_ready = true;
        room.ready_player_count = 3;
        room.all_players_ready = true;
      }
      if (path.endsWith('/start')) {
        room.state = 'playing';
        room.current_turn_user_id = userId;
        room.dealer.hand = withLongHands ? [...longBlackjackHand] : ['SpadeA', 'Hidden'];
        for (const player of room.players) {
          player.hand = withLongHands ? [...longBlackjackHand] : ['Spade2', 'Heart3'];
          player.status = 'playing';
          player.is_current_turn = player.user_id === userId;
        }
      }
      if (path.endsWith('/hit')) room.players[0]!.hand.push('Club2');
      if (path.endsWith('/stand')) {
        room.state = 'finished';
        room.current_turn_user_id = null;
        for (const player of room.players) {
          player.status = 'done';
          player.result = 'win';
          player.is_current_turn = false;
          player.is_ready = false;
        }
      }
      return route.fulfill({ json: { success: true, room, viewer_balance: profile.balance } });
    }
    return route.fulfill({ status: 404, json: { detail: `未模拟接口 ${path}` } });
  });
}

test('黑杰克12张手牌在极小横屏与桌面一次全部显示', async ({ page }) => {
  test.setTimeout(30_000);
  await page.setViewportSize({ width: 667, height: 375 });
  await mockApi(page, false, true);
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: '单人对战 你 vs 月月' }).click();
  await page.getByRole('button', { name: '开始对战', exact: true }).click();
  for (const viewport of [{ width: 568, height: 320 }, { width: 667, height: 375 }, { width: 844, height: 390 }, { width: 1440, height: 900 }, { width: 2400, height: 1080 }]) {
    await page.setViewportSize(viewport);
    await expectControlsReachable(page);
    await expectAllBlackjackCardsVisible(page, 12);
    await expectActionsAboveOwnHand(page);
    await captureFinalScreenshot(page, `landscape-handfit-blackjack-single12-${viewport.width}x${viewport.height}.png`);
  }
  await page.getByRole('button', { name: '停牌', exact: true }).click();
  await page.getByRole('button', { name: '返回', exact: true }).click();
  await page.getByRole('button', { name: '多人对战 最多3人同桌' }).click();
  await page.getByRole('button', { name: '创建房间', exact: true }).click();
  await page.getByLabel('多人下注金额').fill('10');
  await page.getByRole('button', { name: '下注', exact: true }).click();
  await page.getByRole('button', { name: '准备', exact: true }).click();
  await page.getByRole('button', { name: '开始本局', exact: true }).click();
  for (const viewport of [{ width: 568, height: 320 }, { width: 667, height: 375 }, { width: 844, height: 390 }, { width: 1440, height: 900 }, { width: 2400, height: 1080 }]) {
    await page.setViewportSize(viewport);
    await expectControlsReachable(page);
    await expectAllBlackjackCardsVisible(page, 12);
    await expectActionsAboveOwnHand(page);
    await captureFinalScreenshot(page, `landscape-handfit-blackjack-multi12-${viewport.width}x${viewport.height}.png`);
  }
});

test('黑杰克玩法规则与月月陪玩开关', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await mockApi(page, true);
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: '玩法规则', exact: true }).click();
  const rules = page.getByRole('dialog');
  await expect(rules).toBeVisible();
  await expect(rules).toContainText('1.5 倍');
  await expect(rules).toContainText('60 秒自动停牌');
  await expect(rules).toContainText('没收本局下注');
  await page.getByRole('button', { name: '关闭21点规则' }).click();
  await page.getByRole('button', { name: '多人对战 最多3人同桌' }).click();
  await page.getByRole('button', { name: '创建房间', exact: true }).click();
  await expect(page.getByRole('button', { name: '添加月月陪玩', exact: true })).toBeEnabled();
  const addRequest = page.waitForRequest(request => request.url().endsWith('/api/multi/room/bot'));
  await page.getByRole('button', { name: '添加月月陪玩', exact: true }).click();
  expect((await addRequest).postDataJSON()).toEqual({ room_id: 'ABC123', include_yueyue: true });
  await expect(page.locator('.seat-area').filter({ hasText: '月月（陪玩）' })).toContainText('AI');
  await expectControlsReachable(page);
  const removeRequest = page.waitForRequest(request => request.url().endsWith('/api/multi/room/bot'));
  await page.getByRole('button', { name: '移除月月陪玩', exact: true }).click();
  expect((await removeRequest).postDataJSON()).toEqual({ room_id: 'ABC123', include_yueyue: false });
  await expect(page.getByRole('button', { name: '添加月月陪玩', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '玩法规则', exact: true }).click();
  await expect(rules).toBeVisible();
});

for (const viewport of viewports) {
  test(`${viewport.width}×${viewport.height} 单人与多人操作始终可达`, async ({ page }) => {
    test.setTimeout(30_000);
    await page.setViewportSize(viewport);
    await mockApi(page);
    await page.goto('/?dev_user_id=123456789012345678');
    await page.getByRole('button', { name: '21点 立即游玩' }).click();
    await page.getByRole('button', { name: '单人对战 你 vs 月月' }).click();
    await expect(page.getByLabel('单人下注金额')).toHaveValue('100');
    await expect(page.getByRole('button', { name: '小 100', exact: true })).toBeVisible();
    await expectControlsReachable(page);
    await page.getByLabel('单人下注金额').fill('10');
    await page.getByRole('button', { name: '开始对战', exact: true }).click();
    await expect(page.getByRole('button', { name: '要牌', exact: true })).toBeEnabled();
    await expectControlsReachable(page);
    await expectBlackjackSeats(page, false);
    await captureFinalScreenshot(page, `landscape-blackjack-single-${viewport.width}x${viewport.height}.png`);
    await page.getByRole('button', { name: '要牌', exact: true }).click();
    await page.getByRole('button', { name: '停牌', exact: true }).click();
    await expect(page.getByRole('button', { name: '再来一局', exact: true })).toBeEnabled();
    await expectControlsReachable(page);
    await expectBlackjackSeats(page, false);

    await page.getByRole('button', { name: '返回', exact: true }).click();
    await page.getByRole('button', { name: '多人对战 最多3人同桌' }).click();
    await page.getByRole('button', { name: '创建房间', exact: true }).click();
    await expect(page.locator('.seat-area')).toHaveCount(3);
    await expectControlsReachable(page);
    await expectBlackjackSeats(page, true);
    await expectBettingAwayFromDiscordRail(page);
    await captureFinalScreenshot(page, `landscape-blackjack-multi-waiting-${viewport.width}x${viewport.height}.png`);
    await page.getByLabel('多人下注金额').fill('10');
    await page.getByRole('button', { name: '下注', exact: true }).click();
    await page.getByRole('button', { name: '准备', exact: true }).click();
    await page.getByRole('button', { name: '开始本局', exact: true }).click();
    await expect(page.getByRole('button', { name: '要牌', exact: true })).toBeEnabled();
    await expectControlsReachable(page);
    await expectBlackjackSeats(page, true);
    await captureFinalScreenshot(page, `landscape-blackjack-multi-${viewport.width}x${viewport.height}.png`);
    await page.getByRole('button', { name: '要牌', exact: true }).click();
    await page.getByRole('button', { name: '停牌', exact: true }).click();
    await expect(page.getByRole('button', { name: '沿用上局并准备' })).toBeVisible();
    await expectControlsReachable(page);
    await expectBettingAwayFromDiscordRail(page);

    // 竖屏仅提示旋转，不能卸载牌局或清空操作状态。
    await page.setViewportSize({ width: 375, height: 667 });
    await expectPortraitNotice(page);
    await expect(page.locator('.seat-area')).toHaveCount(3);
    await expect(page.locator('.table-heading')).toContainText('ABC123');
    await captureFinalScreenshot(page, `landscape-blackjack-portrait-notice-${viewport.width}.png`);
    await page.setViewportSize(viewport);
    await expect(page.locator('.landscape-notice')).toBeHidden();
    await expect(page.getByRole('button', { name: '沿用上局并准备' })).toBeVisible();
    await expectControlsReachable(page);
  });
}
