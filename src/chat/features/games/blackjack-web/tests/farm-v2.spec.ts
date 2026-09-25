import { expect, test, type Page } from '@playwright/test';
import type { FarmActionBody, FarmPet, FarmPlot, FarmState } from '../src/farmTypes';

const now = 1_790_000_000;
const pets: FarmPet[] = [
  { id: 'qingling_fox', name: '青团灵狐', price: 300, unlock_level: 1, guard_chance: .25, water_bonus: 0, icon_key: 'qingling_fox', description: '守护期间有25%概率抓住偷菜者。' },
  { id: 'mountain_hound', name: '巡山灵犬', price: 650, unlock_level: 2, guard_chance: .4, water_bonus: 0, icon_key: 'mountain_hound', description: '守护期间有40%概率抓住偷菜者。' },
  { id: 'dew_crane', name: '衔露仙鹤', price: 500, unlock_level: 3, guard_chance: .15, water_bonus: .05, icon_key: 'dew_crane', description: '浇水额外缩短5%基础生长期。' },
];
function plot(id: number, values: Partial<FarmPlot> = {}): FarmPlot {
  return { plot_id: id, status: 'empty', crop_id: null, crop_name: null, planted_at: null, mature_at: null, progress: 0, watered: false, has_pest: false, pest_cleared: false, quality: null, yield_total: 0, yield_remaining: 0, stolen_count: 0, can_steal: false, mutation_chance: .04, dew_used: false, ward_used: false, ward_active: false, ward_until: null, theft_attempted: false, ...values };
}
function fixture(): FarmState {
  return { server_time: now, is_owner: true, owner: { user_id: '123456789012345678', username: '洞天主人', avatar_url: '/ui/player-avatar.svg' }, balance: 5000,
    farm: { level: 3, xp: 300, next_level_xp: 540, unlocked_plots: 6, aura_level: 1, growth_multiplier: 1.1, mutation_bonus: .01, created_at: now - 90000 },
    plots: Array.from({ length: 6 }, (_, i) => plot(i + 1, i < 2 ? { status: 'mature', crop_id: 'huangjing', crop_name: '黄精芝', planted_at: now - 2000, mature_at: now - 10, quality: 'normal', yield_total: 4, yield_remaining: 4, can_steal: true } : i === 5 ? {} : { status: 'growing', crop_id: 'huangjing', crop_name: '黄精芝', planted_at: now - 500, mature_at: now + 900, yield_total: 4, yield_remaining: 4 })),
    inventory: { seeds: [{ crop_id: 'huangjing', quantity: 6 }], produce: [], items: [{ item_id: 'pet_food', quantity: 2 }, { item_id: 'spirit_dew', quantity: 2 }, { item_id: 'ward_talisman', quantity: 2 }] }, pets: [], guardian: null,
    catalog: { crops: [{ id: 'huangjing', name: '黄精芝', tier: '凡品', unlock_level: 1, grow_seconds: 1800, seed_price: 24, sale_price: 10, base_yield: 4, xp: 12, description: '常见灵草', source_url: 'https://fanren-wiki.pages.dev/', icon_key: 'huangjing' }], pets,
      items: [{ id: 'pet_food', name: '灵兽口粮', price: 40, unlock_level: 1, icon_key: 'pet_food', description: '增加24小时守护，最多积攒72小时。' }, { id: 'spirit_dew', name: '灵露', price: 20, unlock_level: 1, icon_key: 'spirit_dew', description: '缩短10%基础生长期，一茬一次。' }, { id: 'ward_talisman', name: '护田符', price: 35, unlock_level: 1, icon_key: 'ward_talisman', description: '保护1小时，一茬一次。' }], land_levels: [], aura_levels: [], rules: { pet_food_seconds: 86400, pet_guard_max_seconds: 259200 } }, activity: [] };
}
function equip(state: FarmState, id: string, expiry = now + 86400) {
  const pet = pets.find(value => value.id === id)!;
  state.guardian = { pet_id: id, name: pet.name, guard_chance: pet.guard_chance, water_bonus: pet.water_bonus, active: expiry > now, guard_until: expiry, remaining_seconds: Math.max(0, expiry - now) };
  for (const owned of state.pets!) owned.equipped = owned.pet_id === id;
}
async function mount(page: Page, state = fixture(), options: { failFirst?: boolean; realAssets?: boolean; integrated?: boolean } = {}) {
  const calls: FarmActionBody[] = [];
  const receipts = new Map<string, FarmState>();
  if (!options.realAssets) await page.route('**/ui/farm-v2/**', route => route.fulfill({ contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="2048" height="256">${Array.from({ length: 8 }, (_, i) => `<circle cx="${i * 256 + 128}" cy="128" r="${50 + i * 8}" fill="hsl(${i * 35} 50% 60%)"/>`).join('')}</svg>` }));
  await page.route('**/farm-v2-test', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="farm-test-app"></div><script type="module" src="/tests/farm-harness.ts"></script></body></html>' }));
  await page.route('**/api/farm**', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: state });
    const body = route.request().postDataJSON() as FarmActionBody;
    calls.push(body);
    if (receipts.has(body.request_id)) return route.fulfill({ json: receipts.get(body.request_id) });
    state.result = { action: body.action, message: '操作已完成' };
    if (body.action === 'buy_pet') {
      const pet = pets.find(value => value.id === body.pet_id)!;
      state.balance! -= pet.price;
      state.pets!.push({ pet_id: pet.id, equipped: !state.guardian, active: true, guard_until: now + 86400, remaining_seconds: 86400 });
      if (!state.guardian) equip(state, pet.id);
    }
    if (body.action === 'equip_pet') equip(state, body.pet_id!);
    if (body.action === 'buy_item') { state.balance! -= state.catalog.items!.find(item => item.id === body.item_id)!.price * body.quantity!; state.inventory.items!.find(item => item.item_id === body.item_id)!.quantity += body.quantity!; }
    if (body.action === 'use_item') {
      state.inventory.items!.find(item => item.item_id === body.item_id)!.quantity--;
      if (body.item_id === 'pet_food') { state.guardian!.guard_until += 86400; state.guardian!.remaining_seconds += 86400; }
      if (body.item_id === 'spirit_dew') { state.plots[body.plot_id! - 1]!.dew_used = true; state.plots[body.plot_id! - 1]!.mature_at! -= 180; }
      if (body.item_id === 'ward_talisman') Object.assign(state.plots[body.plot_id! - 1]!, { ward_used: true, ward_active: true, ward_until: now + 3600 });
    }
    if (body.action === 'water') state.plots[body.plot_id! - 1]!.watered = true;
    if (body.action === 'plant') Object.assign(state.plots[body.plot_id! - 1]!, { status: 'growing', crop_id: 'huangjing', crop_name: '黄精芝', planted_at: now, mature_at: now + 1800 });
    if (body.action === 'harvest') state.plots[body.plot_id! - 1] = plot(body.plot_id!);
    if (body.action === 'steal') { state.plots[body.plot_id! - 1]!.can_steal = false; state.result = { action: body.action, caught: true, quantity: 0, message: '青团灵狐抓住了你，本次没有偷到灵草' }; state.activity.unshift({ action: 'caught', message: state.result.message, timestamp: now }); }
    receipts.set(body.request_id, structuredClone(state));
    if (options.failFirst && calls.length === 1) return route.abort('failed');
    return route.fulfill({ json: state });
  });
  if (options.integrated) {
    await page.route('**/api/profile', route => route.fulfill({ json: { success: true, ...state.owner, balance: 5000 } }));
    await page.route('**/api/config', route => route.fulfill({ json: { noname_available: false } }));
    await page.goto(`/?dev_user_id=${state.owner.user_id}`);
    await page.getByRole('button', { name: /^修仙灵圃/ }).click();
  } else await page.goto('/farm-v2-test');
  await expect(page.getByRole('heading', { name: '灵草洞天', exact: true })).toBeVisible();
  return { calls, state };
}

test('三只灵兽有立绘并支持购置、切换和按当前装备喂养', async ({ page }) => {
  const { calls } = await mount(page);
  await page.getByRole('button', { name: '灵兽', exact: true }).click();
  await expect(page.locator('.pet-portrait img')).toHaveCount(3);
  await page.getByRole('button', { name: '300 灵石 · 请回家', exact: true }).click();
  await expect(page.locator('.garden-guardian')).toContainText('青团灵狐');
  await expect(page.locator('.farm-wallet')).toContainText('4,700');
  await page.getByRole('button', { name: '650 灵石 · 请回家', exact: true }).click();
  await page.locator('.pet-shop-card').filter({ hasText: '巡山灵犬' }).getByRole('button', { name: '出战守护' }).click();
  await expect(page.locator('.garden-guardian')).toContainText('巡山灵犬');
  await page.getByRole('button', { name: '喂口粮 ×2' }).click();
  await expect(page.locator('.garden-guardian')).toContainText('喂口粮 ×1');
  expect(calls.map(call => call.action)).toEqual(['buy_pet', 'buy_pet', 'equip_pet', 'use_item']);
  expect(calls.at(-1)).toMatchObject({ action: 'use_item', item_id: 'pet_food' });
  expect(calls.at(-1)).not.toHaveProperty('plot_id');
  expect(calls.at(-1)).not.toHaveProperty('quantity');
});

test('道具有素材、严格数量和灵田选择，已用田块不可再次使用', async ({ page }) => {
  const { calls } = await mount(page);
  await page.getByRole('button', { name: '道具', exact: true }).click();
  await expect(page.locator('.item-shop-card > img')).toHaveCount(3);
  const dew = page.locator('.item-shop-card').filter({ hasText: '缩短10%' });
  await dew.getByRole('spinbutton').fill('1.5');
  await expect(dew.getByRole('button', { name: /灵石 · 购买/ })).toBeDisabled();
  await dew.getByRole('spinbutton').fill('2');
  await dew.getByRole('button', { name: /灵石 · 购买/ }).click();
  await dew.getByRole('button', { name: '选择灵田使用' }).click();
  const dialog = page.getByRole('dialog', { name: '灵露 · 选择灵田' });
  await expect(dialog.getByRole('button', { name: /1号灵田/ })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: /6号灵田/ })).toBeDisabled();
  await dialog.getByRole('button', { name: /3号灵田/ }).click();
  await expect(dialog).not.toBeVisible();
  await dew.getByRole('button', { name: '选择灵田使用' }).click();
  await expect(dialog.getByRole('button', { name: /3号灵田/ })).toBeDisabled();
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.locator('.item-shop-card').filter({ hasText: '保护1小时' }).getByRole('button', { name: '选择灵田使用' }).click();
  await page.getByRole('dialog', { name: '护田符 · 选择灵田' }).getByRole('button', { name: /1号灵田/ }).click();
  expect(calls.map(call => call.action)).toEqual(['buy_item', 'use_item', 'use_item']);
  expect(calls[1]).toMatchObject({ item_id: 'spirit_dew', plot_id: 3 });
  expect(calls[2]).toMatchObject({ item_id: 'ward_talisman', plot_id: 1 });
});

test('购宠回包丢失沿用同一编号核对且不允许另一笔写入', async ({ page }) => {
  const { calls } = await mount(page, fixture(), { failFirst: true });
  await page.getByRole('button', { name: '灵兽', exact: true }).click();
  await page.getByRole('button', { name: '300 灵石 · 请回家', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('同一次操作不会重复扣款');
  await expect(page.getByRole('button', { name: '650 灵石 · 请回家', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '核对这次操作', exact: true }).click();
  await expect(page.locator('.farm-wallet')).toContainText('4,700');
  expect(calls).toHaveLength(2);
  expect(calls[0]!.request_id).toBe(calls[1]!.request_id);
});

test('访客看守护信息、被抓反馈和日志，不能买宠与道具', async ({ page }) => {
  const state = fixture(); equip(state, 'qingling_fox'); state.is_owner = false; state.balance = null; state.inventory = { seeds: [], produce: [], items: [] };
  const { calls } = await mount(page, state);
  await expect(page.locator('.garden-guardian')).toContainText('抓偷概率 25%');
  await expect(page.locator('.garden-guardian').getByRole('button')).toHaveCount(0);
  await page.getByRole('button', { name: '灵兽', exact: true }).click();
  await expect(page.getByRole('button', { name: '300 灵石 · 请回家', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: /^选择1号灵田/ }).click();
  await page.getByRole('article', { name: /1号灵田/ }).getByRole('button', { name: '偷采 1 份' }).click();
  await expect(page.locator('.farm-notice')).toContainText('本次没有偷到灵草');
  await expect(page.locator('.plot-action-effect')).toContainText('被灵兽抓住了');
  await page.locator('.farm-activity summary').click();
  await expect(page.locator('.farm-activity')).toContainText('青团灵狐抓住了你');
  await expect(page.getByRole('article', { name: /1号灵田/ }).getByRole('button', { name: '本轮不可偷采' })).toBeDisabled();
  expect(calls[0]).toMatchObject({ action: 'steal', target_user_id: state.owner.user_id, plot_id: 1 });
  await page.getByRole('button', { name: '道具', exact: true }).click();
  await expect(page.locator('.item-shop-card button:enabled')).toHaveCount(0);
});

test('守护到期变成休息状态、72小时上限禁止浪费口粮', async ({ page }) => {
  const state = fixture(); equip(state, 'qingling_fox', now + 200000);
  await mount(page, state);
  await expect(page.getByRole('button', { name: '喂口粮 ×2' })).toBeDisabled();
  state.guardian!.guard_until = now - 1;
  await page.getByRole('button', { name: '刷新农场' }).click();
  await expect(page.locator('.garden-guardian')).toContainText('守护已到期');
  await expect(page.getByRole('button', { name: '喂口粮 ×2' })).toBeEnabled();
});

test('浇水逐帧图集持续可见，幼苗有生成阶段图，后台暂停动画', async ({ page }) => {
  await mount(page);
  const growing = page.getByRole('article', { name: /3号灵田/ });
  await expect(growing.locator('.painted-stage.sprite-loaded')).toBeVisible();
  await page.getByRole('button', { name: /^选择3号灵田/ }).click();
  await growing.getByRole('button', { name: '浇水', exact: true }).click();
  const animation = growing.getByRole('img', { name: '浇水动画' });
  await page.getByRole('button', { name: /^选择4号灵田/ }).click();
  await page.getByRole('article', { name: /4号灵田/ }).getByRole('button', { name: '浇水', exact: true }).click();
  await expect(page.getByRole('img', { name: '浇水动画' })).toHaveCount(2);
  await expect(animation).toBeVisible();
  const strip = animation.locator('.sprite-strip');
  await expect.poll(() => strip.evaluate(el => getComputedStyle(el).animationName)).toContain('farm-sprite-once');
  const first = await strip.evaluate(el => getComputedStyle(el).transform);
  await page.waitForTimeout(550);
  const second = await strip.evaluate(el => getComputedStyle(el).transform);
  expect(second).not.toBe(first);
  await page.waitForTimeout(900);
  await expect(animation).toBeVisible();
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => strip.evaluate(el => getComputedStyle(el).animationPlayState)).toBe('paused');
});

test('服务端确认新成熟后播放生长图集，不在客户端提前收获', async ({ page }) => {
  const { state } = await mount(page);
  const growing = page.getByRole('article', { name: /3号灵田/ });
  await expect(growing.getByRole('button', { name: /收获/ })).toHaveCount(0);
  Object.assign(state.plots[2]!, { status: 'mature', mature_at: now - 1, quality: 'normal' });
  await page.getByRole('button', { name: '刷新农场' }).click();
  await expect(growing.getByRole('img', { name: '灵草成熟动画' })).toBeVisible();
  await page.getByRole('button', { name: /^选择3号灵田/ }).click();
  await expect(growing.getByRole('button', { name: '收获 4 份' })).toBeVisible();
});

test('减少动态效果展示图集末帧，收获依然有对应图片反馈', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mount(page);
  await page.getByRole('button', { name: /^选择1号灵田/ }).click();
  await page.getByRole('article', { name: /1号灵田/ }).getByRole('button', { name: '收获 4 份', exact: true }).click();
  const animation = page.getByRole('img', { name: '收获动画' });
  await expect(animation).toBeVisible();
  await expect.poll(() => animation.locator('.sprite-strip').evaluate(el => getComputedStyle(el).animationName)).toBe('none');
  expect(await animation.locator('.sprite-strip').evaluate(el => getComputedStyle(el).transform)).not.toBe('none');
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 600 }, { width: 844, height: 390 }, { width: 390, height: 844 }]) {
  test(`连续田园在 ${viewport.width}×${viewport.height} 正常展示三宠三道具且不横向溢出`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mount(page, fixture(), { integrated: true });
    await page.getByRole('button', { name: '灵兽', exact: true }).click();
    await expect(page.locator('.pet-portrait img')).toHaveCount(3);
    await expect(page.locator('.farm-game')).toBeVisible();
    const overflow = await page.locator('.farm-game').evaluate(el => ({ scroll: el.scrollWidth, client: el.clientWidth }));
    expect(overflow.scroll).toBeLessThanOrEqual(overflow.client + 1);
    await page.getByRole('button', { name: '道具', exact: true }).click();
    await page.locator('.item-shop-card').filter({ hasText: '缩短10%' }).getByRole('button', { name: '选择灵田使用' }).click();
    const dialog = page.getByRole('dialog', { name: '灵露 · 选择灵田' });
    await expect(dialog).toBeVisible();
    const box = (await dialog.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(-1);
    expect(box.y).toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
  });
}


test('正式生成素材可加载，浇水真图集逐帧变化、灵兽和道具原画可见', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const state = fixture(); state.pets = [{ pet_id: 'qingling_fox', equipped: true, guard_until: now + 86400, remaining_seconds: 86400, active: true }]; equip(state, 'qingling_fox');
  await mount(page, state, { realAssets: true, integrated: true });
  await expect(page.locator('.farm-panel')).not.toBeVisible();
  await expect.poll(() => page.locator('.farm-world-backdrop > img').evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth === 1536)).toBe(true);
  await expect.poll(() => page.locator('.farm-world-dock img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
  await expect(page.locator('.guardian-sprite.sprite-loaded')).toBeVisible();
  await page.getByRole('button', { name: /^选择3号灵田/ }).click();
  await page.getByRole('article', { name: /3号灵田/ }).getByRole('button', { name: '浇水', exact: true }).click();
  const water = page.getByRole('img', { name: '浇水动画' });
  await expect(water).toHaveClass(/sprite-loaded/);
  await expect.poll(() => water.locator('img').evaluate((image: HTMLImageElement) => [image.naturalWidth, image.naturalHeight])).toEqual([2048, 256]);
  const first = await water.locator('img').evaluate(image => getComputedStyle(image).transform);
  await page.waitForTimeout(660);
  expect(await water.locator('img').evaluate(image => getComputedStyle(image).transform)).not.toEqual(first);
  await page.screenshot({ path: 'test-results-farm-world-real/water-1440.png' });
  await page.getByRole('button', { name: '灵兽', exact: true }).click();
  await expect.poll(() => page.locator('.pet-portrait img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth === 384))).toBe(true);
  await page.screenshot({ path: 'test-results-farm-world-real/pets-1440.png' });
  await page.getByRole('button', { name: '道具', exact: true }).click();
  await expect.poll(() => page.locator('.item-shop-card > img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth === 256))).toBe(true);
  await page.screenshot({ path: 'test-results-farm-world-real/items-1440.png' });
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 844, height: 390 }, { width: 390, height: 844 }]) {
  test(`十二块田在${viewport.width}×${viewport.height}都能真实点击并展开操作`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const state = fixture(); state.farm.unlocked_plots = 12;
    state.plots = Array.from({ length: 12 }, (_, index) => plot(index + 1, { status: 'growing', crop_id: 'huangjing', crop_name: '黄精芝', planted_at: now - 500, mature_at: now + 900, yield_total: 4, yield_remaining: 4 }));
    await mount(page, state, { realAssets: true, integrated: true });
    for (const index of Array.from({ length: 12 }, (_, index) => index + 1)) {
      await page.getByRole('button', { name: new RegExp(`^选择${index}号灵田`) }).click();
      const action = page.getByRole('article', { name: new RegExp(`^${index}号灵田 `) }).getByRole('button', { name: '浇水', exact: true });
      await action.click({ trial: true });
    }
    await page.screenshot({ path: `test-results-farm-world-real/twelve-${viewport.width}.png` });
  });
}
