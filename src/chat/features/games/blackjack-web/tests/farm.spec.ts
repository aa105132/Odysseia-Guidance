import { expect, test, type Page } from '@playwright/test';
import type { FarmActionBody, FarmCrop, FarmPlot, FarmState } from '../src/farmTypes';

const uid = '123456789012345678';
const neighborId = '223456789012345678';
const time = 1_790_000_000;
const crops: FarmCrop[] = [
  { id: 'huangjing', name: '黄精芝', tier: '凡品', unlock_level: 1, grow_seconds: 1800, seed_price: 24, sale_price: 10, base_yield: 4, xp: 12, description: '常见灵草，适合初入灵圃练手。', source_url: 'https://fanren-wiki.pages.dev/', icon_key: 'huangjing' },
  { id: 'zihou', name: '紫猴花', tier: '灵品', unlock_level: 2, grow_seconds: 5400, seed_price: 60, sale_price: 26, base_yield: 4, xp: 24, description: '筑基丹主药之一，灵种为洞天玩法改编。', source_url: 'https://fanren-wiki.pages.dev/', icon_key: 'zihou' },
  { id: 'jinlei', name: '金雷竹', tier: '地品', unlock_level: 8, grow_seconds: 86400, seed_price: 1500, sale_price: 685, base_yield: 4, xp: 220, description: '三大神木之一。', source_url: 'https://fanren-wiki.pages.dev/', icon_key: 'jinlei' },
];
function plot(id: number, values: Partial<FarmPlot> = {}): FarmPlot {
  return { plot_id: id, status: 'empty', crop_id: null, crop_name: null, planted_at: null, mature_at: null, progress: 0, watered: false, has_pest: false, pest_cleared: false, quality: null, yield_total: 0, yield_remaining: 0, stolen_count: 0, can_steal: false, mutation_chance: .04, ...values };
}
function farm(): FarmState {
  return { server_time: time, is_owner: true, owner: { user_id: uid, username: '灵圃主人', avatar_url: '/ui/player-avatar.svg' }, farm: { level: 3, xp: 300, next_level_xp: 540, unlocked_plots: 3, aura_level: 0, growth_multiplier: 1, mutation_bonus: 0, created_at: time - 90000 }, balance: 5000,
    plots: [plot(1), plot(2, { status: 'growing', crop_id: 'huangjing', crop_name: '黄精芝', planted_at: time - 400, mature_at: time + 800, has_pest: true, yield_total: 3, yield_remaining: 3 }), plot(3, { status: 'mature', crop_id: 'zihou', crop_name: '紫猴花', planted_at: time - 6000, mature_at: time - 100, quality: 'spirit', yield_total: 4, yield_remaining: 4, watered: true })],
    inventory: { seeds: [{ crop_id: 'huangjing', quantity: 6 }, { crop_id: 'zihou', quantity: 2 }], produce: [] },
    catalog: { crops, land_levels: [{ plot_count: 4, required_level: 2, cost: 180 }, { plot_count: 5, required_level: 3, cost: 420 }], aura_levels: [{ level: 1, required_level: 2, cost: 300, growth_multiplier: 1.1, mutation_bonus: .01 }], rules: {} }, activity: [],
  };
}

async function mountFarm(page: Page, initial = farm(), options: { failFirst?: boolean; delay?: number; integrated?: boolean } = {}) {
  let own = structuredClone(initial);
  let neighbor = { ...farm(), is_owner: false, owner: { user_id: neighborId, username: '邻居道友', avatar_url: '/ui/player-avatar.svg' }, balance: null, inventory: { seeds: [], produce: [] } };
  neighbor.plots[2]!.can_steal = true;
  const calls: FarmActionBody[] = [];
  const receipts = new Map<string, FarmState>();
  await page.route('**/farm-test', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="farm-test-app"></div><script type="module" src="/tests/farm-harness.ts"></script></body></html>` }));
  await page.route('**/api/farm**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/visits')) return route.fulfill({ json: { entries: [{ user_id: neighborId, username: '邻居道友', avatar_url: '/ui/player-avatar.svg', level: 3, mature_plots: 1 }] } });
    if (route.request().method() === 'GET') return route.fulfill({ json: url.searchParams.has('owner_id') ? neighbor : own });
    const body = route.request().postDataJSON() as FarmActionBody;
    calls.push(body);
    if (options.delay) await new Promise(resolve => setTimeout(resolve, options.delay));
    const receipt = receipts.get(body.request_id);
    if (receipt) return route.fulfill({ json: receipt });
    const current = body.action === 'steal' ? neighbor : own;
    if (body.action === 'buy_seed') {
      current.balance! -= crops.find(crop => crop.id === body.crop_id)!.seed_price * body.quantity!;
      const seed = current.inventory.seeds.find(item => item.crop_id === body.crop_id)!;
      seed.quantity += body.quantity!;
    }
    if (body.action === 'plant') {
      current.inventory.seeds.find(item => item.crop_id === body.crop_id)!.quantity--;
      current.plots[body.plot_id! - 1] = plot(body.plot_id!, { status: 'growing', crop_id: body.crop_id!, crop_name: crops.find(item => item.id === body.crop_id)!.name, planted_at: time, mature_at: time + 1800, yield_total: 4, yield_remaining: 4 });
    }
    if (body.action === 'water') { current.plots[body.plot_id! - 1]!.watered = true; current.plots[body.plot_id! - 1]!.mature_at! -= 180; }
    if (body.action === 'pest') { current.plots[body.plot_id! - 1]!.pest_cleared = true; current.plots[body.plot_id! - 1]!.has_pest = false; }
    const harvestedQuality = body.action === 'harvest' ? current.plots[body.plot_id! - 1]!.quality : null;
    if (body.action === 'harvest') { const harvested = current.plots[body.plot_id! - 1]!; current.inventory.produce.push({ crop_id: harvested.crop_id!, quality: harvested.quality!, quantity: harvested.yield_remaining, unit_price: 52 }); current.plots[body.plot_id! - 1] = plot(body.plot_id!); }
    if (body.action === 'sell') { current.balance! += 52 * body.quantity!; current.inventory.produce = []; }
    if (body.action === 'expand') { current.balance! -= 180; current.farm.unlocked_plots++; current.plots.push(plot(4)); }
    if (body.action === 'upgrade_aura') { current.balance! -= 300; current.farm.aura_level = 1; current.farm.growth_multiplier = 1.1; }
    if (body.action === 'steal') { const stolen = current.plots[body.plot_id! - 1]!; stolen.can_steal = false; stolen.yield_remaining--; stolen.stolen_count++; }
    const response = { ...current, result: { action: body.action, message: '操作已完成', ...(harvestedQuality ? { quality: harvestedQuality } : {}) } };
    receipts.set(body.request_id, structuredClone(response));
    if (options.failFirst && calls.length === 1) return route.abort('failed');
    return route.fulfill({ json: response });
  });
  if (options.integrated) {
    await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: uid, username: '灵圃主人', avatar_url: '/ui/player-avatar.svg', balance: 5000 } }));
    await page.route('**/api/config', route => route.fulfill({ json: { noname_available: false } }));
    await page.goto(`/?dev_user_id=${uid}`);
    await page.getByRole('button', { name: /^修仙灵圃/ }).click();
  } else await page.goto('/farm-test');
  await expect(page.getByRole('heading', { name: '灵草洞天', exact: true })).toBeVisible();
  await expect(page.getByRole('article', { name: /1号灵田/ })).toBeVisible();
  return { calls, own, neighbor };
}

test('灵田完成播种、浇水、除虫、收获与出售的经营循环', async ({ page }) => {
  const { calls } = await mountFarm(page);
  await page.getByRole('button', { name: /^选择1号灵田/ }).click();
  await page.getByRole('article', { name: /1号灵田/ }).getByRole('button', { name: '播种', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '选一粒灵种' });
  await dialog.getByRole('button', { name: /黄精芝.*播种/ }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('article', { name: /1号灵田 黄精芝/ })).toBeVisible();
  await page.getByRole('button', { name: /^选择2号灵田/ }).click();
  await page.getByRole('article', { name: /2号灵田/ }).getByRole('button', { name: '浇水', exact: true }).click();
  await expect(page.getByRole('article', { name: /2号灵田/ }).getByText('已浇水')).toHaveCount(0);
  await expect(page.getByRole('article', { name: /2号灵田/ }).getByRole('button', { name: '浇水', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /^选择2号灵田/ }).click();
  await page.getByRole('article', { name: /2号灵田/ }).getByRole('button', { name: '除虫', exact: true }).click();
  await expect(page.getByRole('article', { name: /2号灵田/ }).getByText('已照料 · 静待成熟')).toBeVisible();
  await page.getByRole('button', { name: /^选择3号灵田/ }).click();
  await page.getByRole('article', { name: /3号灵田/ }).getByRole('button', { name: '收获 4 份' }).click();
  await expect(page.getByRole('article', { name: /3号灵田 空地/ })).toBeVisible();
  await page.getByRole('button', { name: /^仓库/ }).click();
  await page.getByRole('button', { name: '出售 4 份 · 208 灵石' }).click();
  await expect(page.locator('.farm-wallet')).toContainText('5,208');
  expect(calls.map(call => call.action)).toEqual(['plant', 'water', 'pest', 'harvest', 'sell']);
  expect(calls.every(call => /^[a-z0-9-]{36}$/i.test(call.request_id))).toBeTruthy();
  expect(new Set(calls.map(call => call.request_id)).size).toBe(calls.length);
});

test('购买校验数量和解锁，扩地消耗灵石前必须确认', async ({ page }) => {
  const { calls } = await mountFarm(page);
  await page.getByRole('button', { name: '种子铺', exact: true }).click();
  const quantity = page.getByRole('spinbutton', { name: '黄精芝购买数量' });
  await quantity.fill('2');
  await page.getByRole('button', { name: '48 灵石 · 购买', exact: true }).click();
  await expect(page.locator('.farm-wallet')).toContainText('4,952');
  await expect(page.getByRole('button', { name: '1,500 灵石 · 购买', exact: true })).toBeDisabled();
  await quantity.fill('1.5');
  await expect(page.locator('.crop-shop-item').first().getByRole('button')).toBeDisabled();
  await page.getByRole('button', { name: '洞天', exact: true }).click();
  await page.getByRole('button', { name: '180 灵石 · 扩建' }).click();
  expect(calls.map(call => call.action)).toEqual(['buy_seed']);
  const confirmation = page.getByRole('dialog', { name: '开辟新灵田' });
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  expect(calls.map(call => call.action)).toEqual(['buy_seed']);
  await page.getByRole('button', { name: '180 灵石 · 扩建' }).click();
  await confirmation.getByRole('button', { name: '确认扩建', exact: true }).click();
  await expect(page.getByRole('article', { name: /4号灵田/ })).toBeVisible();
  await expect(page.locator('.farm-wallet')).toContainText('4,772');
});

test('丢失写入回包后使用相同 request_id 核对，防止重复购买', async ({ page }) => {
  const { calls } = await mountFarm(page, farm(), { failFirst: true });
  await page.getByRole('button', { name: '种子铺', exact: true }).click();
  await page.getByRole('button', { name: '24 灵石 · 购买', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('同一次操作不会重复扣款');
  await expect(page.getByRole('button', { name: '24 灵石 · 购买', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '核对这次操作' }).click();
  await expect(page.locator('.farm-wallet')).toContainText('4,976');
  expect(calls).toHaveLength(2);
  expect(calls[0]!.request_id).toBe(calls[1]!.request_id);
});

test('串门隐藏库存及升级入口，偷菜发送目标用户且不能重复点击', async ({ page }) => {
  const { calls } = await mountFarm(page, farm(), { delay: 150 });
  await page.getByRole('button', { name: '串门', exact: true }).click();
  await page.getByRole('button', { name: '拜访', exact: true }).click();
  await expect(page.getByText('邻居道友的灵田', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '播种', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /^仓库/ }).click();
  await expect(page.getByText('邻居的仓库只对本人开放。')).toBeVisible();
  await page.getByRole('button', { name: '收起经营面板', exact: true }).click();
  await page.getByRole('button', { name: /^选择3号灵田/ }).click();
  await page.getByRole('button', { name: '偷采 1 份', exact: true }).click();
  await expect(page.getByRole('button', { name: '偷采 1 份', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '本轮不可偷采', exact: true })).toBeDisabled();
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({ action: 'steal', target_user_id: neighborId, plot_id: 3 });
  await expect(page.locator('.farm-wallet')).toContainText('5,000');
  await page.getByRole('button', { name: '回我的灵田' }).click();
  await expect(page.getByText('我的灵田', { exact: true })).toBeVisible();
});

test('成品图鉴全展示且狭窄横屏没有横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await mountFarm(page);
  await page.getByRole('button', { name: '灵草图鉴', exact: true }).click();
  await expect(page.locator('.crop-gallery article')).toHaveCount(crops.length);
  await expect(page.getByText('灵品 ×2', { exact: true })).toBeVisible();
  await expect(page.getByText('仙品 ×5', { exact: true })).toBeVisible();
  const overflow = await page.locator('.farm-game').evaluate(element => element.scrollWidth > element.clientWidth + 1);
  expect(overflow).toBe(false);
  await page.locator('.crop-gallery').scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'test-results-farm/farm-gallery-844.png', fullPage: true });
});

test('桌面农场的地块与经营栏可见并保留真实尺寸', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mountFarm(page);
  const garden = await page.locator('.farm-garden').boundingBox();
  expect(garden!.width).toBeGreaterThan(1300);
  await expect(page.locator('.farm-panel')).not.toBeVisible();
  await page.getByRole('button', { name: '种子铺', exact: true }).click();
  await expect(page.locator('.farm-panel')).toBeVisible();
  expect(await page.locator('.farm-plot').first().evaluate(element => element.clientWidth)).toBeGreaterThan(150);
  await page.screenshot({ path: 'test-results-farm/farm-1440.png', fullPage: true });
});

test('正常大厅入口进入农场并同步余额，返回大厅可继续选择玩法', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mountFarm(page, farm(), { integrated: true });
  await page.getByRole('button', { name: '种子铺', exact: true }).click();
  await page.getByRole('button', { name: '24 灵石 · 购买', exact: true }).click();
  await expect(page.locator('.farm-wallet')).toContainText('4,976');
  await page.screenshot({ path: 'test-results-farm/farm-app-1440.png', fullPage: true });
  await page.getByRole('button', { name: '游戏大厅', exact: true }).click();
  await expect(page.locator('.hub-game-grid > .game-card')).toHaveCount(8);
  await expect(page.getByRole('button', { name: '查看个人信息与统计' })).toContainText('4976');
});

test('客户端时间偏差不提前开放收获，成熟由服务端确认', async ({ page }) => {
  const initial = farm();
  initial.plots = [plot(1, { status: 'growing', crop_id: 'huangjing', crop_name: '黄精芝', planted_at: time - 100, mature_at: time + 120, yield_total: 4, yield_remaining: 4 })];
  await mountFarm(page, initial);
  const land = page.getByRole('article', { name: /1号灵田/ });
  await expect(land).toContainText('2分0秒后成熟');
  await expect(land.getByRole('button', { name: /收获/ })).toHaveCount(0);
  await expect(page.locator('.plot-progress i')).toHaveAttribute('style', /45\.45/);
});

test('常见手机竖屏自动旋转后可以播种，弹窗不会被侧栏裁切', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mountFarm(page, farm(), { integrated: true });
  await expect(page.locator('html')).toHaveAttribute('data-activity-rotated', 'true');
  const overflow = await page.locator('.farm-game').evaluate(element => element.scrollWidth > element.clientWidth + 1);
  expect(overflow).toBe(false);
  await page.getByRole('button', { name: /^选择1号灵田/ }).click();
  await page.getByRole('article', { name: /1号灵田/ }).getByRole('button', { name: '播种', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '选一粒灵种' });
  await expect(dialog).toBeVisible();
  const box = await dialog.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(box!.y + box!.height).toBeLessThanOrEqual(844);
  await page.screenshot({ path: 'test-results-farm/farm-app-390-plant.png', fullPage: true });
  await dialog.getByRole('button', { name: /黄精芝.*播种/ }).click();
  await expect(dialog).not.toBeVisible();
});

test('嵌入农场随窗口铺满安全矩形，横屏避右边且竖屏避底部', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mountFarm(page, farm(), { integrated: true });
  await page.evaluate(async () => {
    history.replaceState({}, '', `${location.pathname}?frame_id=farm-viewport`);
    const viewport = await import('/src/activityViewport.ts' as string);
    viewport.updateActivityViewport();
  });
  for (const { width, height } of [{ width: 1280, height: 720 }, { width: 844, height: 390 }, { width: 390, height: 844 }]) {
    await page.setViewportSize({ width, height });
    const portrait = height > width;
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.activityRotated)).toBe(String(portrait));
    await expect.poll(() => page.locator('.game-viewport-stage').evaluate(element => ({ width: element.clientWidth, height: element.clientHeight }))).toEqual({ width: portrait ? height - 64 : width - 64, height: portrait ? width : height });
    const contentWidth = width - (portrait ? 0 : 64);
    const contentHeight = height - (portrait ? 64 : 0);
    const farmBox = (await page.locator('.farm-game').boundingBox())!;
    expect(farmBox.x).toBeCloseTo(0, 1);
    expect(farmBox.y).toBeCloseTo(0, 1);
    expect(farmBox.width).toBeCloseTo(contentWidth, 1);
    expect(farmBox.height).toBeCloseTo(contentHeight, 1);
    expect(await page.locator('.farm-game').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    await page.getByRole('button', { name: /^选择1号灵田/ }).click();
    await page.getByRole('article', { name: /1号灵田/ }).getByRole('button', { name: '播种', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选一粒灵种' });
    const box = (await dialog.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(-1);
    expect(box.y).toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width).toBeLessThanOrEqual(contentWidth + 1);
    expect(box.y + box.height).toBeLessThanOrEqual(contentHeight + 1);
    await dialog.getByRole('button', { name: '关闭播种面板', exact: true }).click();
    await page.screenshot({ path: `test-results-farm/farm-safe-viewport-${width}.png` });
  }
});

test('成熟素材成功加载后替换图形，失败回退且阶段素材失败时保留原生动效', async ({ page }) => {
  await page.route('**/ui/farm-v2/growth-stages.webp', route => route.fulfill({ status: 404, body: '' }));
  await page.route('**/ui/farm/plants/huangjing.webp', route => route.fulfill({ status: 404, body: '' }));
  await page.route('**/ui/farm/plants/zihou.webp', route => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="140"><circle cx="80" cy="70" r="30" fill="green"/></svg>' }));
  await mountFarm(page);
  const mature = page.getByRole('article', { name: /3号灵田/ });
  await expect(mature.locator('.painted-plant.loaded')).toBeVisible();
  await expect(mature.locator('.plant-fallback')).not.toBeVisible();
  await expect(mature.locator('.painted-sparkles')).toBeVisible();
  await page.getByRole('button', { name: '种子铺', exact: true }).click();
  await expect(page.locator('.crop-shop-item').first().locator('.plant-fallback')).toBeVisible();
  await expect(page.getByRole('article', { name: /2号灵田/ }).locator('.plant-fallback')).toBeVisible();
  await expect(page.getByRole('article', { name: /2号灵田/ }).locator('.painted-plant')).toHaveCount(0);
});

test('成功照料播放月月语音，变异收获优先惊喜语音，静音后不再播放', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:sound', 'true');
    localStorage.setItem('yueyue:voice', 'true');
    (window as any).__farmTracks = [];
    class FarmAudio {
      src: string; original: string; paused = true; volume = 1; loop = false;
      onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = this.original = src; (window as any).__farmTracks.push(this); }
      play() { this.paused = false; return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    }
    (window as any).Audio = FarmAudio;
  });
  await mountFarm(page, farm(), { integrated: true });
  const played = (name: string) => page.evaluate(id => (window as any).__farmTracks.filter((track: any) => new URL(track.original, location.origin).pathname.endsWith(`/${id}.mp3`)).length, name);
  await page.getByRole('button', { name: /^选择2号灵田/ }).click();
  await page.getByRole('article', { name: /2号灵田/ }).getByRole('button', { name: '浇水', exact: true }).click();
  await expect.poll(() => played('farm_water')).toBe(1);
  await page.getByRole('button', { name: /^选择3号灵田/ }).click();
  await page.getByRole('article', { name: /3号灵田/ }).getByRole('button', { name: '收获 4 份' }).click();
  await expect.poll(() => played('farm_mutation')).toBe(1);
  expect(await played('farm_harvest')).toBe(0);
  await page.getByRole('button', { name: '声音设置', exact: true }).click();
  const audioPanel = page.getByRole('dialog', { name: '声音设置', exact: true });
  await audioPanel.getByRole('checkbox', { name: /月月与聊天语音/ }).uncheck();
  await audioPanel.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: /^选择2号灵田/ }).click();
  await page.getByRole('article', { name: /2号灵田/ }).getByRole('button', { name: '除虫', exact: true }).click();
  await expect(page.getByRole('article', { name: /2号灵田/ }).getByText('已照料 · 静待成熟')).toBeVisible();
  expect(await played('farm_pest')).toBe(0);
});
