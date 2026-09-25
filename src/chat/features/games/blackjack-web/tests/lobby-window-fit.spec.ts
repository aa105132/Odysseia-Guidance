import { expect, test, type Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const uid = '123456789012345678';

async function openLobby(page: Page, embedded: boolean, nonameAvailable = false) {
  await page.route(/.*(?:@discord_embedded-app-sdk|@discord\/embedded-app-sdk).*\.(?:js|mjs)(?:\?.*)?$/, route => route.fulfill({
    contentType: 'application/javascript',
    body: `export class DiscordSDK { clientId='1463798242981445672';customId='';instanceId='window-fit';channelId='111111111111111111';guildId='222222222222222222';async ready(){};commands={authorize:async()=>({code:'mock'}),authenticate:async()=>({user:{id:'${uid}'}})}; }`,
  }));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/config') return route.fulfill({ json: { discord_client_id: '1463798242981445672', noname_available: nonameAvailable } });
    if (path === '/api/token') return route.fulfill({ json: { access_token: 'mock' } });
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '窗口适配牌友', avatar_url: '/ui/player-avatar.svg', balance: 2000 } });
    return route.fulfill({ json: { success: true } });
  });
  await page.goto(embedded ? '/?frame_id=window-fit' : `/?dev_user_id=${uid}`);
  await expect(page.locator('.hub-game-grid > .game-card')).toHaveCount(nonameAvailable ? 7 : 6);
  await expect(page.locator('.yueyue-mascot-sprite')).toBeVisible();
}

async function expectLobbyFits(page: Page, width: number, height: number, embedded: boolean) {
  const rail = embedded ? 64 : 0;
  const rotated = height > width && width <= 768;
  const portrait = height > width;
  const available = { width: portrait ? width : width - rail, height: portrait ? height - rail : height };
  const logical = rotated ? { width: available.height, height: available.width } : available;
  // 横竖切换前后逻辑尺寸可以相同，需同时等待物理方向更新。
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.activityRotated)).toBe(String(rotated));
  await expect.poll(() => page.locator('#app').evaluate(element => ({ width: element.clientWidth, height: element.clientHeight }))).toEqual(logical);
  const layout = await page.evaluate(() => {
    const app = document.querySelector('#app') as HTMLElement;
    const root = document.querySelector('.multi-root') as HTMLElement;
    const grid = document.querySelector('.hub-game-grid') as HTMLElement;
    const cards = Array.from(grid.querySelectorAll<HTMLElement>('.game-card'));
    // 在任何点击或滚动前量取所有内容，防止 scrollIntoView 掩盖初始裁切。
    const bounds = (element: Element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
    };
    return {
      appHeight: app.clientHeight,
      rootHeight: root.clientHeight,
      rootScroll: { x: root.scrollWidth - root.clientWidth, y: root.scrollHeight - root.clientHeight, top: root.scrollTop },
      gridFraction: grid.clientWidth / app.clientWidth,
      rows: new Set(cards.map(card => card.offsetTop)).size,
      visibleContent: Array.from(document.querySelectorAll('.top-bar, .hub-game-grid > .game-card, .lobby-auxiliary, .lobby-footnote, .yueyue-mascot-button, .yueyue-mascot-speech')).map(bounds),
      cardContent: cards.map(card => ({ card: bounds(card), copy: bounds(card.querySelector('.game-card-copy')!), touchHeight: card.offsetHeight })),
      mascot: bounds(document.querySelector('.yueyue-mascot-button')!),
    };
  });
  expect(layout.rootHeight, '大厅高度随可用窗口变化').toBe(logical.height);
  expect(layout.rootScroll, '初始画面应完整排入窗口，无需滚动才能找到月月').toEqual({ x: 0, y: 0, top: 0 });
  expect(layout.gridFraction, '横条玩法区约占宽屏四成，留出人物场景').toBeGreaterThan(.35);
  expect(layout.gridFraction, '玩法网格不覆盖整幅场景').toBeLessThan(logical.height > 600 ? .5 : .75);
  expect(layout.rows).toBeGreaterThan(1);
  for (const box of layout.visibleContent) {
    expect(box.left).toBeGreaterThanOrEqual(-1);
    expect(box.top).toBeGreaterThanOrEqual(-1);
    expect(box.right, '横屏内容避让 Discord 右侧工具条').toBeLessThanOrEqual(available.width + 1);
    expect(box.bottom, '内容完整显示，竖屏避让底部 Discord 工具条').toBeLessThanOrEqual(available.height + 1);
  }
  for (const { card, copy, touchHeight } of layout.cardContent) {
    expect(touchHeight, '最小窗口仍保留至少 44px 点击高度').toBeGreaterThanOrEqual(44);
    expect(copy.left).toBeGreaterThanOrEqual(card.left);
    expect(copy.top).toBeGreaterThanOrEqual(card.top);
    expect(copy.right, '卡片文字未被横向裁切').toBeLessThanOrEqual(card.right);
    expect(copy.bottom, '卡片文字未被纵向裁切').toBeLessThanOrEqual(card.bottom);
    const horizontalOverlap = Math.min(card.right, layout.mascot.right) - Math.max(card.left, layout.mascot.left);
    const verticalOverlap = Math.min(card.bottom, layout.mascot.bottom) - Math.max(card.top, layout.mascot.top);
    expect(horizontalOverlap > 1 && verticalOverlap > 1, '月月按钮不覆盖游戏卡片').toBe(false);
  }
}

for (const { width, height, embedded } of [
  { width: 1243, height: 775, embedded: true },
  { width: 1280, height: 720, embedded: true },
  { width: 1920, height: 1080, embedded: false },
  { width: 1024, height: 768, embedded: false },
  { width: 844, height: 390, embedded: true },
  { width: 390, height: 844, embedded: true },
  { width: 568, height: 320, embedded: false },
  { width: 632, height: 320, embedded: true },
]) {
  test(`大厅铺满窗口且月月完整可见 ${width}×${height}${embedded ? ' 嵌入' : ''}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openLobby(page, embedded);
    await expectLobbyFits(page, width, height, embedded);
    await page.screenshot({ path: fileURLToPath(new URL(`../../../../../../screenshots/lobby-redesign/window-fit-${width}x${height}.png`, import.meta.url)) });
    const mascot = (await page.getByRole('button', { name: '和月月打招呼' }).boundingBox())!;
    // 按当前坐标点击，不允许 Playwright 自动滚动后才认为控件可用。
    await page.mouse.click(mascot.x + mascot.width / 2, mascot.y + mascot.height / 2);
    await expect(page.locator('.yueyue-mascot-sprite')).toHaveAttribute('data-animation', 'waving');
    await expectLobbyFits(page, width, height, embedded);
  });
}

test('大厅随窗口连续缩放与横竖切换重新排版，不保留旧窗口溢出', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await openLobby(page, true);
  for (const [width, height] of [[1280, 720], [1920, 1080], [1243, 775], [844, 390], [390, 844]]) {
    await page.setViewportSize({ width: width!, height: height! });
    await expectLobbyFits(page, width!, height!, true);
  }
});

for (const viewport of [{ width: 1504, height: 900 }, { width: 844, height: 390 }, { width: 390, height: 844 }, { width: 632, height: 320 }]) {
  test(`启用第七种玩法后仍完整入屏 ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await openLobby(page, true, true);
    await expectLobbyFits(page, viewport.width, viewport.height, true);
    await expect(page.locator('.hub-game-grid > .noname-card')).toContainText('三国杀');
    await expect(page.getByRole('button', { name: /修仙灵圃/ })).toHaveCount(1);
  });
}

test('横条卡片采用独立金边纹理，桌面四条长卡与底部双卡分层', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openLobby(page, false);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const cards = page.locator('.hub-game-grid > .game-card');
  const sizes = await cards.evaluateAll(elements => elements.map(element => {
    const card = element as HTMLElement;
    return { width: card.offsetWidth, height: card.offsetHeight, top: card.offsetTop };
  }));
  expect(sizes.slice(0, 4).every(card => card.width / card.height > 3)).toBe(true);
  expect(new Set(sizes.slice(0, 4).map(card => card.top)).size).toBe(4);
  expect(sizes[4]!.top).toBe(sizes[5]!.top);
  expect(sizes[4]!.width).toBeLessThan(sizes[0]!.width * .52);
  expect(await page.locator('.lobby-gameplay').evaluate(element => getComputedStyle(element).backgroundImage)).toBe('none');
  for (const card of await cards.all()) {
    await expect(card.locator('.card-arrow')).toBeVisible();
    const decor = await card.evaluate(element => ({
      frame: getComputedStyle(element, '::before').borderImageSource,
      texture: getComputedStyle(element).backgroundImage,
      motion: getComputedStyle(element).animationName,
    }));
    expect(decor.frame).toContain('/ui/lobby/card-frame.svg');
    expect(decor.texture).toContain('/ui/lobby/card-brocade.svg');
    expect(decor.motion).toBe('none');
  }
  for (const asset of ['/ui/lobby/card-frame.svg', '/ui/lobby/card-brocade.svg']) {
    expect((await page.request.get(asset)).ok()).toBe(true);
  }
  await page.getByRole('button', { name: '21点 立即游玩' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: /单人对战/ })).toBeVisible();
});
