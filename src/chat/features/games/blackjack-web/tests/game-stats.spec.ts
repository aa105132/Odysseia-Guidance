import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';
const personal = { rounds: 12, wins: 7, losses: 4, draws: 1, win_rate: 58.3333, net_profit: 456, today_profit: -24, legacy_rounds: 2 };

async function mockProfile(page: Page) {
  await page.route('**/api/tables/history?**', route => route.fulfill({ json: { success: true, entries: [], total: 0, has_more: false, timezone: 'Asia/Shanghai' } }));
  await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: uid, username: '统计牌友', avatar_url: '/character/normal.webp', balance: 5000 } }));
}

test('个人面板展示净盈利与胜率，并支持21点筛选及空态', async ({ page }) => {
  await mockProfile(page);
  await page.route('**/api/tables/stats?**', route => route.fulfill({ json: { success: true, timezone: 'Asia/Shanghai', stats: new URL(route.request().url()).searchParams.get('game_type') === 'blackjack' ? { ...personal, rounds: 0, wins: 0, losses: 0, draws: 0, win_rate: 0, net_profit: 0, today_profit: 0, legacy_rounds: 0 } : personal } }));
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '查看个人信息与统计', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '个人统计', exact: true });
  await expect(panel.getByText('58.3')).toBeVisible();
  await expect(panel.getByText('+456', { exact: false })).toBeVisible();
  await expect(panel.getByText(/含 2 场旧结算记录/)).toBeVisible();
  await panel.getByRole('combobox', { name: '统计玩法' }).selectOption('blackjack');
  await expect(panel.getByRole('status')).toHaveText('还没有该玩法的已结算记录，完成一局后就会显示在这里。');
  await expect(panel.getByText(/含 2 场旧结算记录/)).toHaveCount(0);
  await panel.getByRole('button', { name: '关闭统计面板' }).click();
  await expect(panel).toHaveCount(0);
});

test('排行榜切换当日和累计，展示自己的排名', async ({ page }) => {
  await mockProfile(page);
  const periods: string[] = [];
  await page.route('**/api/tables/leaderboard?**', route => {
    const period = new URL(route.request().url()).searchParams.get('period')!;
    periods.push(period);
    return route.fulfill({ json: { success: true, timezone: 'Asia/Shanghai', legacy_rounds: 0,
      entries: [{ rank: 1, user_id: 'other', username: '当日榜首', avatar_url: '', net_profit: period === 'today' ? 100 : 2000, rounds: 5 }],
      self: { rank: 25, user_id: uid, username: '统计牌友', avatar_url: '', net_profit: 16, rounds: 2 } } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: /排行榜.*当日盈利/ }).click();
  const panel = page.getByRole('dialog', { name: '盈利排行榜' });
  await expect(panel.getByText('第 25 名')).toBeVisible();
  await expect(panel.locator('.stats-ranking')).toContainText('+100');
  await panel.getByRole('button', { name: '总计盈利', exact: true }).click();
  await expect(panel.locator('.stats-ranking')).toContainText('+2,000');
  expect(periods).toEqual(['today', 'all']);
});

test('历史玩家缺少资料时使用通用头像，头像加载失败仍可查看排名', async ({ page }) => {
  await mockProfile(page);
  const legacyId = '1438147017091055740';
  await page.route('**/api/tables/leaderboard?**', route => route.fulfill({ json: {
    entries: [
      { rank: 1, user_id: legacyId, username: legacyId, avatar_url: '', net_profit: 90, rounds: 5 },
      { rank: 2, user_id: uid, username: '头像失效的牌友', avatar_url: '/missing-player-avatar.png', net_profit: 20, rounds: 2 },
    ], self: null, timezone: 'Asia/Shanghai', legacy_rounds: 5,
  } }));
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: /排行榜.*当日盈利/ }).click();
  const panel = page.getByRole('dialog', { name: '盈利排行榜' });
  await expect(panel.getByText('牌友 · 055740')).toBeVisible();
  for (const avatar of await panel.locator('.stats-ranking img').all()) {
    await expect(avatar).toHaveAttribute('src', '/ui/player-avatar.svg');
    await expect.poll(() => avatar.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  }
});

test('快速切换玩法时旧请求不能覆盖新结果，失败后可重试', async ({ page }) => {
  await mockProfile(page);
  let fail = true;
  let resolveOld: (() => void) | undefined;
  await page.route('**/api/tables/stats?**', async route => {
    const game = new URL(route.request().url()).searchParams.get('game_type');
    if (game === 'texas') await new Promise<void>(resolve => { resolveOld = resolve; });
    if (game === 'blackjack' && fail) return route.fulfill({ status: 503, json: { detail: '统计服务暂时不可用' } });
    return route.fulfill({ json: { success: true, timezone: 'Asia/Shanghai', stats: { ...personal, rounds: game === 'blackjack' ? 99 : 12 } } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '查看个人信息与统计', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '个人统计', exact: true });
  await expect(panel.locator('.stats-metrics')).toBeVisible();
  await panel.getByRole('combobox', { name: '统计玩法' }).selectOption('texas');
  await expect.poll(() => Boolean(resolveOld)).toBe(true);
  await panel.getByRole('combobox', { name: '统计玩法' }).selectOption('blackjack');
  await expect(panel.getByRole('alert')).toContainText('统计服务暂时不可用');
  fail = false;
  await panel.getByRole('button', { name: '重试', exact: true }).click();
  await expect(panel.locator('.stats-metrics > div').first()).toContainText('99');
  const oldResponse = page.waitForResponse(response => response.url().includes('game_type=texas'));
  resolveOld?.();
  await oldResponse;
  await expect(panel.locator('.stats-metrics > div').first()).toContainText('99');
});

test('568×320统计面板可滚动，关闭及筛选保持可触达', async ({ page }) => {
  await page.setViewportSize({ width: 568, height: 320 });
  await mockProfile(page);
  await page.route('**/api/tables/stats?**', route => route.fulfill({ json: { success: true, timezone: 'Asia/Shanghai', stats: personal } }));
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '查看个人信息与统计', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '个人统计', exact: true });
  await expect(panel.locator('.stats-metrics')).toBeAttached();
  for (const control of await panel.locator('.stats-heading button,.stats-controls button,.stats-controls select').all()) {
    await expect(control).toBeInViewport({ ratio: 1 });
    expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(36);
  }
  expect(await panel.locator('.stats-content').evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  await panel.locator('.stats-notes').scrollIntoViewIfNeeded();
  await expect(panel.locator('.stats-notes')).toBeInViewport();
  await expect(panel.getByRole('button', { name: '关闭统计面板' })).toBeInViewport({ ratio: 1 });
  const bounds = (await panel.boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(568);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(320);
});
