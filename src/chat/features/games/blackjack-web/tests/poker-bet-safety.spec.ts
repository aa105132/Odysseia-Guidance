import { expect, test, type Page } from '@playwright/test';
import { captureFinalScreenshot } from './animation-helpers';

const uid = '123456789012345678';

function makeRoom(kind: 'texas' | 'golden_flower' = 'texas', count = 2) {
  const ids = Array.from({ length: count }, (_, index) => index ? `player:${index}` : uid);
  return {
    room_id: 'BETSAFE', game_type: kind, host_user_id: uid, state: 'playing', revision: 1, round_number: 1,
    mode: 'multi', min_players: 2, max_players: kind === 'texas' ? 8 : 5, room_tier: 'beginner',
    base_stake: 1, entry_min: 100, loss_limit: 100, settlement_status: 'reserved',
    turn_deadline: Math.floor(Date.now() / 1000) + 60,
    players: ids.map((id, index) => ({ user_id: id, username: index ? `玩家${index}` : '我', avatar_url: '/character/normal.webp', is_bot: !!index, is_ready: true, connected: true })),
    game: {
      phase: kind === 'texas' ? 'preflop' : 'betting', finished: false, current_player_id: uid,
      legal_actions: kind === 'texas' ? ['fold', 'call', 'raise', 'all_in'] : ['fold', 'call', 'raise', 'compare'],
      winners: [] as string[], message: '等待你的操作', pot: 3, current_bet: 2, call_amount: 1,
      min_raise_to: 4, max_raise_to: 100, compare_cost: 4, community_cards: ['Club2', 'Heart7', 'DiamondQ'],
      players: ids.map((id, index) => ({ user_id: id, hand: index ? [] : kind === 'texas' ? ['SpadeA', 'ClubK'] : ['SpadeA', 'ClubK', 'Heart9'],
        hand_count: kind === 'texas' ? 2 : 3, stack: index ? 98 : 99, round_bet: index ? 2 : 1, seen: kind === 'golden_flower',
        is_dealer: index === 0, is_small_blind: index === (count === 2 ? 0 : 1), is_big_blind: index === (count === 2 ? 1 : 2) })),
    },
  };
}

async function openTable(page: Page, room = makeRoom()) {
  const posts: Record<string, unknown>[] = [];
  let getCount = 0;
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '我', avatar_url: '/character/normal.webp', balance: 20000 } });
    if (path === '/api/tables/action') posts.push(route.request().postDataJSON());
    if (path === `/api/tables/${room.room_id}` && route.request().method() === 'GET') getCount++;
    return route.fulfill({ json: { success: true, room, viewer_balance: 19900, events: [], chat_catalog: [], interaction_catalog: [] } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: room.game_type === 'texas' ? /^德州扑克/ : /^炸金花/ }).click();
  await page.getByRole('button', { name: /^月月陪玩/ }).click();
  await expect(page.locator('#table-raise-amount')).toHaveValue('4');
  return { room, posts, reads: () => getCount };
}

test('全下默认取消、取消零请求、确认仅提交一次且携带原 revision', async ({ page }) => {
  const { posts } = await openTable(page);
  await page.getByRole('button', { name: '全下', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '确认全下？' });
  await expect(dialog).toContainText('99 灵石');
  await expect(dialog).toContainText('剩余 0 灵石');
  await expect(dialog.getByRole('button', { name: '取消', exact: true })).toBeFocused();
  expect(posts).toEqual([]);
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(posts).toEqual([]);
  await page.getByRole('button', { name: '全下', exact: true }).click();
  await dialog.getByRole('button', { name: '确认全下', exact: true }).evaluate(button => {
    (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click();
  });
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ action: 'all_in', expected_revision: 1, room_id: 'BETSAFE' });
});

test('滑条数值同步，最大加注和用完筹码的跟注都先确认', async ({ page }) => {
  const { room, posts } = await openTable(page);
  const amount = page.locator('#table-raise-amount');
  await amount.fill('30');
  await expect(page.getByRole('slider', { name: '加注金额滑条' })).toHaveValue('30');
  await page.getByRole('slider', { name: '加注金额滑条' }).fill('100');
  await expect(amount).toHaveValue('100');
  await page.getByRole('button', { name: '加注', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '确认全下？' })).toBeVisible();
  expect(posts).toHaveLength(0);
  await page.getByRole('button', { name: '确认全下', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ action: 'raise', amount: 100 });
  room.revision++;
  room.game.call_amount = 99;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await page.getByRole('button', { name: '跟注', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '确认全下？' })).toBeVisible();
  expect(posts).toHaveLength(1);
});

test('炸金花看牌后的实际双倍费用用完筹码也确认', async ({ page }) => {
  const room = makeRoom('golden_flower');
  room.game.players[0]!.stack = 98;
  room.game.max_raise_to = 49;
  const { posts } = await openTable(page, room);
  await page.locator('#table-raise-amount').fill('49');
  await expect(page.locator('.tg-raise-cost')).toHaveText('已看牌 · 实付 98');
  await page.getByRole('button', { name: '加注', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '确认全下？' })).toContainText('98 灵石');
  expect(posts).toHaveLength(0);
  await page.getByRole('button', { name: '取消', exact: true }).click();
});

test('新局换街新回合重置最低加注，同回合同步保留输入', async ({ page }) => {
  const { room } = await openTable(page);
  const input = page.locator('#table-raise-amount');
  await input.fill('90');
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(input).toHaveValue('90');
  room.round_number++;
  room.revision++;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(input).toHaveValue('4');
  await input.fill('70');
  room.game.phase = 'flop';
  room.game.min_raise_to = 2;
  room.game.current_bet = 0;
  room.revision++;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(input).toHaveValue('2');
  await input.fill('60');
  room.game.current_player_id = 'player:1';
  room.game.legal_actions = [];
  room.revision++;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  room.game.current_player_id = uid;
  room.game.legal_actions = ['fold', 'call', 'raise', 'all_in'];
  room.revision++;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(input).toHaveValue('2');
  await input.fill('50');
  room.room_id = 'SECOND';
  room.revision = 1;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(input).toHaveValue('2');
});

test('同revision轮询保留全下弹窗，局面变更使旧确认自动失效', async ({ page }) => {
  const { room, posts, reads } = await openTable(page);
  await page.getByRole('button', { name: '全下', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '确认全下？' });
  const priorReads = reads();
  await expect.poll(reads).toBeGreaterThan(priorReads);
  await expect(dialog).toBeVisible();
  room.game.phase = 'flop';
  room.revision++;
  await expect(dialog).not.toBeVisible({ timeout: 4000 });
  expect(posts).toHaveLength(0);
});

test('庄小盲大盲标记清楚，两人庄兼小盲，下一局轮转', async ({ page }) => {
  const { room } = await openTable(page);
  const mine = page.getByLabel('我的德州位置', { exact: true });
  const other = page.getByLabel('玩家1的德州位置', { exact: true });
  await expect(mine).toHaveText('庄小盲');
  await expect(other).toHaveText('大盲');
  room.round_number++;
  room.revision++;
  room.game.players[0]!.is_dealer = room.game.players[0]!.is_small_blind = false;
  room.game.players[0]!.is_big_blind = true;
  room.game.players[1]!.is_dealer = room.game.players[1]!.is_small_blind = true;
  room.game.players[1]!.is_big_blind = false;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(mine).toHaveText('大盲');
  await expect(other).toHaveText('庄小盲');
});

test('快捷倍数及上限夹取不绕过全下确认，普通加注成功归最低', async ({ page }) => {
  const { room, posts } = await openTable(page);
  await page.locator('#table-raise-amount').fill('20');
  await page.getByRole('button', { name: '加注', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  await expect(page.locator('#table-raise-amount')).toHaveValue('4');
  room.game.players[0]!.stack = 7;
  room.game.max_raise_to = 8;
  room.revision++;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await page.getByRole('button', { name: '填入4倍当前桌注', exact: true }).click();
  await expect(page.locator('#table-raise-amount')).toHaveValue('8');
  await page.getByRole('button', { name: '加注', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '确认全下？' })).toContainText('7 灵石');
  expect(posts).toHaveLength(1);
  await page.getByRole('button', { name: '取消', exact: true }).click();
  room.game.max_raise_to = 6;
  room.game.players[0]!.stack = 5;
  room.revision++;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect(page.locator('#table-raise-amount')).toHaveValue('6');
  await page.getByRole('button', { name: '加注', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '确认全下？' })).toContainText('5 灵石');
  expect(posts).toHaveLength(1);
});

test('八人桌滑条与全下确认在手机横屏及自动旋转后可点击且避开Discord灰条', async ({ page }) => {
  await openTable(page, makeRoom('texas', 8));
  await page.evaluate(async () => {
    history.replaceState({}, '', `${location.pathname}?frame_id=bet-safe`);
    const viewportModule = await import('/src/activityViewport.ts' as string);
    viewportModule.updateActivityViewport();
  });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 568, height: 320 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const slider = page.getByRole('slider', { name: '加注金额滑条' });
    await expect(slider).toBeInViewport({ ratio: 1 });
    await slider.click({ trial: true });
    for (const label of await page.locator('.tg-poker-positions > span').all()) await expect(label).toBeInViewport({ ratio: 1 });
    await captureFinalScreenshot(page, `poker-bet-controls-${viewport.width}x${viewport.height}.png`);
    await page.getByRole('button', { name: '全下', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '确认全下？' });
    await expect(dialog).toBeInViewport({ ratio: 1 });
    const box = (await dialog.boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width - 64 + 1);
    await dialog.getByRole('button', { name: '确认全下', exact: true }).click({ trial: true });
    await captureFinalScreenshot(page, `poker-all-in-confirm-${viewport.width}x${viewport.height}.png`);
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
  }
});
