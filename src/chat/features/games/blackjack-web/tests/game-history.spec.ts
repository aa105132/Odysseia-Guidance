import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';
const stats = { rounds: 22, wins: 14, losses: 7, draws: 1, win_rate: 63.6, net_profit: 2000, today_profit: 100, legacy_rounds: 1, max_win: 1200, max_loss: 700, total_won: 5000, total_lost: 3000 };
function round(index: number, game = 'texas') { return { round_key: `round-${index}`, game_type: game, profit: index === 2 ? -70 : 120, settled_at: '2026-09-22 20:00:00', legacy: false, stake: 500, payout: index === 2 ? 430 : 620, has_details: index !== 2 }; }

async function mocks(page: Page) {
  await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: uid, username: '战绩牌友', avatar_url: '/character/normal.webp', balance: 5000 } }));
  await page.route('**/api/tables/stats?**', route => route.fulfill({ json: { stats, timezone: 'Asia/Shanghai' } }));
  await page.route('**/api/tables/history?**', route => {
    const query = new URL(route.request().url()).searchParams;
    const offset = Number(query.get('offset'));
    const game = query.get('game_type') === 'blackjack' ? 'blackjack' : 'texas';
    return route.fulfill({ json: { entries: offset ? [round(21, game), round(22, game)] : Array.from({ length: 20 }, (_, index) => round(index + 1, game)), total: 22, has_more: offset === 0, timezone: 'Asia/Shanghai' } });
  });
  await page.route('**/api/tables/history/round-*', route => {
    const index = Number(route.request().url().split('round-')[1]);
    return route.fulfill({ json: { round: { ...round(index), details: index === 2 ? {} : { players: [{ user_id: uid, username: '战绩牌友' }, { user_id: 'bot', username: '月月', is_bot: true }], final_state: { community_cards: ['HeartA', 'ClubK', 'DiamondQ'], players: [{ user_id: uid, hand: ['SpadeA', 'DiamondA'], hand_name: '三条' }] }, actions: [{ user_id: uid, action: 'raise', amount: 20, time: 1790107200, pot: 30 }, { user_id: 'bot', action: 'fold', time: 1790107201, pot: 30 }] } } } });
  });
  await page.route('**/api/tables/transactions?**', route => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
    return route.fulfill({ json: { entries: offset ? [{ id: 21, amount: -30, reason: '兑换商品', timestamp: null }] : Array.from({ length: 20 }, (_, index) => ({ id: index + 1, amount: index ? -100 : 300, reason: index ? '牌局托管' : '每日签到', timestamp: '2026-09-22 20:00:00' })), total: 21, has_more: offset === 0, balance: 5300, timezone: 'Asia/Shanghai' } });
  });
}

async function openStats(page: Page) {
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '查看个人信息与统计', exact: true }).click();
  return page.getByRole('dialog');
}

test('个人极值、牌局分页与详情按中文展示，缺失历史不杜撰', async ({ page }) => {
  await mocks(page);
  const panel = await openStats(page);
  await expect(panel.locator('.stats-extremes')).toContainText('单局最高赢1,200');
  await expect(panel.locator('.stats-extremes')).toContainText('累计亏3,000');
  await expect(panel.locator('.history-list li')).toHaveCount(20);
  await panel.getByRole('button', { name: '加载更多牌局' }).click();
  await expect(panel.locator('.history-list li')).toHaveCount(22);
  await panel.locator('.history-round').first().click();
  await expect(panel.locator('.round-detail-summary')).toContainText('+120');
  await expect(panel.locator('.round-funds')).toContainText('500 灵石');
  await expect(panel.getByRole('list', { name: '牌局行动记录' })).toContainText('加注至 20 灵石');
  await expect(panel.getByRole('list', { name: '牌局行动记录' })).toContainText('月月');
  await expect(panel.locator('.recorded-hand').first()).toContainText('♥A');
  await panel.getByRole('button', { name: '返回最近牌局' }).click();
  await expect(panel.locator('.history-list li')).toHaveCount(22);
  await panel.locator('.history-round').nth(1).click();
  await expect(panel.getByText('该历史牌局仅有结算数据，未保存出牌过程。')).toBeVisible();
  await expect(panel.getByRole('list', { name: '牌局行动记录' })).toHaveCount(0);
});

test('灵石明细包含非游戏来源、余额与分页，排行榜请求前100', async ({ page }) => {
  await mocks(page);
  let boardLimit = '';
  await page.route('**/api/tables/leaderboard?**', route => {
    boardLimit = new URL(route.request().url()).searchParams.get('limit') || '';
    return route.fulfill({ json: { entries: [], self: null, legacy_rounds: 0, timezone: 'Asia/Shanghai' } });
  });
  const panel = await openStats(page);
  await panel.getByRole('button', { name: '灵石明细', exact: true }).click();
  await expect(panel.locator('.transactions-heading')).toContainText('5,300');
  await expect(panel.getByText('每日签到', { exact: true })).toBeVisible();
  await expect(panel.getByRole('combobox', { name: '统计玩法' })).toHaveCount(0);
  await panel.getByRole('button', { name: '加载更多流水' }).click();
  await expect(panel.locator('.transaction-list li')).toHaveCount(21);
  await expect(panel.getByText('兑换商品', { exact: true })).toBeVisible();
  await expect(panel.getByText('时间未保存', { exact: true })).toBeVisible();
  await panel.getByRole('button', { name: '盈利排行', exact: true }).click();
  await expect(panel.getByText('按净盈利排序 · 前 100 名')).toBeVisible();
  expect(boardLimit).toBe('100');
});

test('历史详情延迟返回不会覆盖切换后的玩法，流水失败可重试', async ({ page }) => {
  await mocks(page);
  let resolveDetail: (() => void) | undefined;
  await page.route('**/api/tables/history/round-1', async route => {
    await new Promise<void>(resolve => { resolveDetail = resolve; });
    await route.fulfill({ json: { round: { ...round(1), details: {} } } });
  });
  let fail = true;
  await page.route('**/api/tables/transactions?**', route => fail ? route.fulfill({ status: 503, json: { detail: '流水暂时不可用' } }) : route.fulfill({ json: { entries: [], total: 0, has_more: false, balance: 5000, timezone: 'Asia/Shanghai' } }));
  const panel = await openStats(page);
  await panel.locator('.history-round').first().click();
  await expect.poll(() => Boolean(resolveDetail)).toBe(true);
  await panel.getByRole('combobox', { name: '统计玩法' }).selectOption('blackjack');
  await expect(panel.locator('.history-round').first()).toContainText('21点');
  resolveDetail?.();
  await expect(panel.getByRole('button', { name: '返回最近牌局' })).toHaveCount(0);
  await panel.getByRole('button', { name: '灵石明细', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('流水暂时不可用');
  fail = false;
  await panel.getByRole('button', { name: '重试', exact: true }).click();
  await expect(panel.getByText('暂无灵石流水。')).toBeVisible();
});

for (const width of [568, 1440]) {
  test(`${width}宽度历史详情与流水可滚动，关闭和栏目始终可用`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 568 ? 320 : 900 });
    await mocks(page);
    const panel = await openStats(page);
    await panel.locator('.history-round').first().click();
    await expect(panel.locator('.round-detail-summary')).toBeVisible();
    await page.screenshot({ path: `../../../../../tmp/game-history-detail-top-${width}.png` });
    await panel.locator('.round-actions li').last().scrollIntoViewIfNeeded();
    await expect(panel.getByRole('button', { name: '关闭统计面板' })).toBeInViewport({ ratio: 1 });
    await expect(panel.getByRole('button', { name: '灵石明细', exact: true })).toBeInViewport({ ratio: 1 });
    await page.screenshot({ path: `../../../../../tmp/game-history-detail-${width}.png` });
    await panel.getByRole('button', { name: '灵石明细', exact: true }).click();
    await expect(panel.locator('.transactions-heading')).toBeVisible();
    await page.screenshot({ path: `../../../../../tmp/game-history-transactions-top-${width}.png` });
    await panel.getByRole('button', { name: '加载更多流水' }).scrollIntoViewIfNeeded();
    await expect(panel.getByRole('button', { name: '关闭统计面板' })).toBeInViewport({ ratio: 1 });
    expect(await panel.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: `../../../../../tmp/game-history-transactions-${width}.png` });
  });
}
