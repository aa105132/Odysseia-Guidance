import { expect, test, type Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
const uid = '123456789012345678';
const shots = (name: string) => fileURLToPath(new URL(`../../../../../../screenshots/lobby-redesign/room-modal-${name}.png`, import.meta.url));

async function setup(page: Page, embedded = false) {
  const calls: { path: string; method: string; body: any }[] = [];
  let currentRoom: any = null;
  let failCreate = false;
  await page.route(/.*(?:@discord_embedded-app-sdk|@discord\/embedded-app-sdk).*\.(?:js|mjs)(?:\?.*)?$/, route => route.fulfill({ contentType: 'application/javascript', body: `export class DiscordSDK {clientId='test';customId='';instanceId='secondary';channelId='111111111111111111';guildId='222222222222222222';async ready(){};commands={authorize:async()=>({code:'mock'}),authenticate:async()=>({user:{id:'${uid}'}})};}` }));
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ path, method: request.method(), body });
    if (path === '/api/config') return route.fulfill({ json: { discord_client_id: 'test', noname_available: true } });
    if (path === '/api/token') return route.fulfill({ json: { access_token: 'mock' } });
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '茶馆牌友', avatar_url: '/ui/player-avatar.svg', balance: 8000 } });
    if (path === '/api/rooms') return route.fulfill({ json: { rooms: [], total: 0 } });
    if (path === '/api/game/current') return route.fulfill({ json: { game: null } });
    if (path === '/api/noname/status') return route.fulfill({ json: { available: true } });
    if (path === '/api/tables/create') {
      if (failCreate) return route.fulfill({ status: 400, json: { detail: '测试建房失败，保留选择' } });
      currentRoom = { room_id: 'SELECT1', game_type: body.game_type, host_user_id: uid, state: 'waiting', revision: 1, mode: body.mode, room_tier: body.room_tier, base_stake: 5, entry_min: 1000, loss_limit: 500, include_yueyue: body.include_yueyue, min_players: 2, max_players: 4, turn_timeout_seconds: body.turn_timeout_seconds, game: null, players: [{ user_id: uid, username: '茶馆牌友', is_bot: false, is_ready: false, connected: true, avatar_url: '/ui/player-avatar.svg' }] };
    }
    if (path === '/api/tables/leave') currentRoom = null;
    if (path.startsWith('/api/tables/')) return route.fulfill({ json: { success: true, room: currentRoom, viewer_balance: 8000 } });
    return route.fulfill({ json: { success: true } });
  });
  await page.goto(embedded ? '/?frame_id=secondary' : `/?dev_user_id=${uid}`);
  await expect(page.locator('.hub-game-grid > button')).toHaveCount(embedded ? 7 : 6);
  await page.locator('.hub-game-grid').evaluate(async el => { await Promise.all(el.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {}))); });
  return { calls, fail: (value: boolean) => { failCreate = value; } };
}

async function stable(page: Page) {
  await expect(page.locator('.lobby-panel-enter-active, .lobby-panel-leave-active')).toHaveCount(0);
}

async function shellFits(page: Page) {
  await stable(page);
  await expect(page.locator('.multi-root')).toHaveClass(/hub-fullscreen/);
  await expect(page.locator('.lobby-auxiliary')).toBeVisible();
  await expect(page.locator('.lobby-scene')).toBeVisible();
  await expect(page.locator('button[aria-label="和月月打招呼"]')).toHaveCount(1);
  expect(await page.locator('.multi-root').evaluate(el => [el.scrollWidth - el.clientWidth, el.scrollHeight - el.clientHeight])).toEqual([0, 0]);
  const overflow = await page.locator('.lobby-panel-stage, .tg-lobby, .secondary-panel, .noname-entry').evaluateAll(elements => elements.map(el => [el.scrollWidth - el.clientWidth, el.scrollHeight - el.clientHeight]));
  expect(overflow.every(values => values.every(value => value <= 1)), JSON.stringify(overflow)).toBe(true);
  for (const element of await page.locator('.lobby-gameplay, .lobby-panel-stage, .lobby-embedded-viewport, .embedded-selection, .embedded-selection .tg-toolbar, .secondary-panel, .noname-game, .noname-entry').all()) {
    expect(await element.evaluate(el => { const s = getComputedStyle(el); return [s.backgroundImage, s.backgroundColor, s.boxShadow]; })).toEqual(['none', 'rgba(0, 0, 0, 0)', 'none']);
  }
  for (const el of await page.locator('.lobby-auxiliary, .lobby-gameplay, .yueyue-mascot-button').all()) await expect(el).toBeInViewport({ ratio: 1 });
  expect(await page.locator('.multi-root').evaluate(el => getComputedStyle(el).backgroundImage)).toContain('yueyue-teahouse.webp');
}

for (const [width, height, embedded] of [[1440, 900, false], [844, 390, true], [390, 844, true], [568, 320, false]] as const) {
  test(`二级玩法仅切换右区且控件可达 ${width}x${height}`, async ({ page }) => {
    test.setTimeout(25_000);
    await page.setViewportSize({ width, height });
    const { calls } = await setup(page, embedded);
    const geometry = async () => page.locator('.lobby-gameplay, .lobby-panel-stage, .lobby-scene, .lobby-auxiliary').evaluateAll(elements => elements.map(el => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(value => Math.round(value)); }));
    const primaryGeometry = await geometry();
    await page.getByRole('button', { name: '房间列表', exact: true }).click();
    await expect(page.locator('.room-directory-embedded')).toBeVisible();
    expect(await geometry()).toEqual(primaryGeometry);
    expect(await page.locator('.room-directory-embedded').evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
    await page.screenshot({ path: shots(`rooms-${width}x${height}`) });
    await page.getByRole('button', { name: '精选玩法', exact: true }).click();
    await page.locator('.hub-game-grid').evaluate(async el => { await Promise.all(el.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => {}))); });
    await page.screenshot({ path: shots(`home-${width}x${height}`) });
    const shell = await page.locator('.game-hub-panel').elementHandle();
    const nav = await page.locator('.lobby-auxiliary').elementHandle();
    const scene = await page.locator('.lobby-scene').elementHandle();
    for (const game of ['texas', 'landlord', 'mahjong', 'golden_flower', 'guandan']) {
      await page.locator(`.hub-game-grid > .${game}-card`).click();
      await expect(page.locator('.embedded-selection')).toBeVisible();
      const contentSize = await page.locator('.embedded-selection').evaluate(el => [el.clientWidth, el.clientHeight]);
      expect(contentSize[0]).toBeLessThanOrEqual(440);
      expect(contentSize[1]).toBeLessThanOrEqual(340);
      await shellFits(page);
      expect(await geometry()).toEqual(primaryGeometry);
      expect(await shell!.evaluate(el => el === document.querySelector('.game-hub-panel'))).toBe(true);
      expect(await nav!.evaluate(el => el === document.querySelector('.lobby-auxiliary'))).toBe(true);
      expect(await scene!.evaluate(el => el === document.querySelector('.lobby-scene'))).toBe(true);
      for (const button of await page.locator('.tg-lobby button').all()) {
        await expect(button).toBeInViewport({ ratio: 1 });
        await button.click({ trial: true });
      }
      expect(await page.locator('.tg-lobby').evaluate(el => el.scrollTop)).toBe(0);
      expect(await page.locator('.tg-lobby-heading').evaluate(el => getComputedStyle(el, '::after').content)).not.toContain('下滑');
      await page.getByRole('button', { name: '入席设置', exact: true }).click();
      const settings = page.getByRole('dialog', { name: '入席设置', exact: true });
      await expect(settings).toBeVisible();
      await expect(settings.locator('#create-turn-seconds')).toBeInViewport({ ratio: 1 });
      await settings.getByRole('button', { name: '关闭入席设置' }).click();
      if (game === 'mahjong') {
        await page.getByRole('button', { name: '四川血战', exact: true }).click();
        await expect(page.getByRole('button', { name: '四川血战', exact: true })).toHaveAttribute('aria-pressed', 'true');
        await page.screenshot({ path: shots(`mahjong-${width}x${height}`) });
      }
      await page.getByRole('button', { name: '返回大厅', exact: true }).click();
      await stable(page);
      await expect(page.locator(`.hub-game-grid > .${game}-card`)).toBeFocused();
    }
    await page.locator('.blackjack-card').click();
    await shellFits(page);
    expect(await geometry()).toEqual(primaryGeometry);
    for (const button of await page.locator('.mode-panel button').all()) await expect(button).toBeInViewport({ ratio: 1 });
    if (!embedded) {
      await page.getByRole('button', { name: /多人对战/ }).click();
      await shellFits(page);
      expect(await geometry()).toEqual(primaryGeometry);
      const dialog = page.getByRole('dialog', { name: '21点房间设置', exact: true });
      await expect(dialog).toBeVisible();
      for (const control of await dialog.locator('button, input').all()) await expect(control).toBeInViewport({ ratio: 1 });
      expect(await dialog.evaluate(el => [el.scrollWidth - el.clientWidth, el.scrollHeight - el.clientHeight])).toEqual([0, 0]);
      await page.screenshot({ path: shots(`blackjack-room-${width}x${height}`) });
      await page.getByRole('button', { name: '关闭21点房间设置' }).click();
    }
    await expect(page.locator('.mode-panel')).toBeVisible();
    await stable(page);
    await page.screenshot({ path: shots(`blackjack-${width}x${height}`) });
    await page.getByRole('button', { name: '返回上一级' }).click();
    if (embedded) {
      await page.locator('.noname-card').click();
      await shellFits(page);
      expect(await geometry()).toEqual(primaryGeometry);
      await expect(page.getByRole('button', { name: '单机试玩' })).toBeDisabled();
      await page.screenshot({ path: shots(`noname-${width}x${height}`) });
      await page.getByRole('button', { name: '返回大厅', exact: true }).click();
    }
    expect(calls.filter(call => call.method === 'POST' && call.path !== '/api/token')).toEqual([]);
  });
}

test('选场到入桌再退出保持实例、选择和单条轮询，不重复加入', async ({ page }) => {
  const { calls, fail } = await setup(page);
  await page.locator('.mahjong-card').click();
  await page.getByRole('button', { name: '四川血战', exact: true }).click();
  await page.getByRole('button', { name: '选择中级场', exact: true }).click();
  await page.getByRole('button', { name: '入席设置', exact: true }).click();
  await page.getByRole('dialog', { name: '入席设置' }).getByLabel('邀请月月一起玩').uncheck();
  await page.locator('#create-turn-seconds').selectOption('90');
  await page.getByRole('button', { name: '关闭入席设置' }).click();
  const component = await page.locator('.table-games').elementHandle();
  await page.getByRole('button', { name: '玩法规则', exact: true }).click();
  await page.getByRole('button', { name: '关闭规则' }).click();
  fail(true);
  await page.getByRole('button', { name: '好友同桌', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('测试建房失败');
  await shellFits(page);
  await expect(page.getByRole('button', { name: '选择中级场' })).toHaveAttribute('aria-pressed', 'true');
  fail(false);
  await page.getByRole('button', { name: '好友同桌', exact: true }).click();
  await expect(page.locator('.table-games')).toHaveClass(/has-room/);
  await expect(page.locator('.multi-root')).toHaveClass(/table-fullscreen/);
  await expect(page.locator('.lobby-auxiliary')).toBeHidden();
  expect(await component!.evaluate(el => el === document.querySelector('.table-games'))).toBe(true);
  expect(calls.filter(call => call.path.endsWith('/create')).at(-1)?.body).toEqual({ game_type: 'sichuan_mahjong', mode: 'multi', include_yueyue: false, room_tier: 'intermediate', turn_timeout_seconds: 90 });
  await page.waitForTimeout(1700);
  expect(calls.filter(call => call.path === '/api/tables/SELECT1').length).toBeLessThanOrEqual(2);
  expect(calls.filter(call => call.path === '/api/tables/join')).toHaveLength(0);
  await page.getByRole('button', { name: '离开房间', exact: true }).click();
  await shellFits(page);
  expect(await component!.evaluate(el => el === document.querySelector('.table-games'))).toBe(true);
  await expect(page.getByRole('button', { name: '选择中级场' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '入席设置', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '入席设置' }).getByLabel('邀请月月一起玩')).not.toBeChecked();
  await expect(page.locator('#create-turn-seconds')).toHaveValue('90');
  await page.getByRole('button', { name: '关闭入席设置' }).click();
  await expect(page.getByRole('button', { name: '四川血战', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('21点多人设置使用弹窗，关闭回焦且保留输入，单人入桌才全屏', async ({ page }) => {
  const { calls } = await setup(page);
  await page.locator('.blackjack-card').click();
  await stable(page);
  const modePanel = await page.locator('.mode-panel').elementHandle();
  await page.getByRole('button', { name: /多人对战/ }).click();
  await shellFits(page);
  await page.getByLabel('21点建房等待时长').fill('90');
  await page.getByLabel('21点房间号').fill('FRIEND');
  await page.screenshot({ path: shots('blackjack-room-desktop') });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: /多人对战/ })).toBeFocused();
  expect(await modePanel!.evaluate(el => el === document.querySelector('.mode-panel'))).toBe(true);
  await page.getByRole('button', { name: /多人对战/ }).click();
  await expect(page.getByLabel('21点房间号')).toHaveValue('FRIEND');
  await expect(page.getByLabel('21点建房等待时长')).toHaveValue('90');
  expect(calls.filter(call => call.method === 'POST')).toEqual([]);
  await page.getByRole('button', { name: '关闭21点房间设置' }).click();
  await page.getByRole('button', { name: /单人对战/ }).click();
  await expect(page.locator('.single-mode-view')).toBeVisible();
  await expect(page.locator('.multi-root')).toHaveClass(/table-fullscreen/);
  await page.getByRole('button', { name: '返回', exact: true }).click();
  await shellFits(page);
});

test('面板进退有位移动画，减少动画偏好禁用位移', async ({ page }) => {
  await setup(page);
  const motion = async () => page.locator('.lobby-panel-stage').evaluate(el => {
    const probe = document.createElement('div');
    const scope = Array.from(el.attributes).find(attr => attr.name.startsWith('data-v-'))!;
    probe.setAttribute(scope.name, '');
    probe.className = 'lobby-panel-enter-active lobby-panel-enter-from'; el.append(probe);
    const style = getComputedStyle(probe), result = { duration: style.transitionDuration, transform: style.transform }; probe.remove(); return result;
  });
  expect(await motion()).toMatchObject({ duration: '0.15s, 0.15s' });
  expect((await motion()).transform).toContain('24');
  await page.evaluate(() => {
    (window as any).__panelTransitions = [];
    document.querySelector('.lobby-panel-stage')!.addEventListener('transitionrun', event => {
      const e = event as TransitionEvent;
      if ((e.target as HTMLElement).className.includes('lobby-panel-')) (window as any).__panelTransitions.push(e.propertyName);
    });
  });
  await page.locator('.texas-card').click();
  await expect(page.locator('.embedded-selection')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__panelTransitions)).toContain('transform');
  await page.getByRole('button', { name: '返回大厅', exact: true }).click();
  await stable(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await motion()).toEqual({ duration: '0s', transform: 'none' });
  await page.locator('.texas-card').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.tg-title h2')).toBeFocused();
  await shellFits(page);
});

for (const [width, height, embedded] of [[568, 320, false], [844, 390, true], [390, 844, true]] as const) {
  test(`21点设置弹窗居中、失败可重试且提交后关闭 ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await setup(page, embedded);
    let fail = true;
    const posts: { path: string; body: any }[] = [];
    const room = { room_id: 'MODAL1', host_user_id: uid, max_players: 3, state: 'waiting', current_turn_user_id: null, ready_player_count: 0, all_players_ready: false, turn_timeout_seconds: 90, dealer: { name: '月月', avatar_path: '/character/normal.webp', expression: 'normal', hand: [], score: 0 }, players: [{ user_id: uid, username: '茶馆牌友', avatar_url: '/ui/player-avatar.svg', seat_index: 0, bet_amount: 0, hand: [], score: 0, status: 'waiting', result: null, payout_amount: 0, is_ready: false, is_current_turn: false, is_bot: false }] };
    await page.route('**/api/multi/room/**', route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === 'POST') {
        posts.push({ path, body: request.postDataJSON() });
        if (fail) return route.fulfill({ status: 400, json: { detail: '暂时未能连接，请重试' } });
      }
      return route.fulfill({ json: { success: true, room, viewer_balance: 8000 } });
    });
    await page.locator('.blackjack-card').click();
    await page.getByRole('button', { name: /多人对战/ }).click();
    const dialog = page.getByRole('dialog', { name: '21点房间设置', exact: true });
    await expect(dialog).toBeVisible();
    await page.getByLabel('21点建房等待时长').fill('90');
    await page.getByLabel('21点房间号').fill('MODAL1');
    const submit = dialog.getByRole('button', { name: embedded ? '连接当前会话' : '创建房间', exact: true });
    await submit.click();
    await expect(dialog.getByRole('alert')).toContainText('请重试');
    expect(await dialog.evaluate(el => [el.scrollWidth - el.clientWidth, el.scrollHeight - el.clientHeight])).toEqual([0, 0]);
    for (const control of await dialog.locator('button, input').all()) {
      await expect(control).toBeInViewport({ ratio: 1 });
      await control.click({ trial: true });
    }
    const center = await page.evaluate(() => {
      const modal = document.querySelector('.blackjack-entry-dialog')!.getBoundingClientRect(), app = document.querySelector('#app')!.getBoundingClientRect();
      return [Math.abs(modal.x + modal.width / 2 - app.x - app.width / 2), Math.abs(modal.y + modal.height / 2 - app.y - app.height / 2)];
    });
    expect(center.every(value => value < 1)).toBe(true);
    await page.screenshot({ path: shots(`dialog-${width}x${height}`) });
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await expect(page.getByRole('button', { name: /多人对战/ })).toBeFocused();
    await page.getByRole('button', { name: /多人对战/ }).click();
    await expect(dialog).toBeVisible();
    await expect(page.getByLabel('21点房间号')).toHaveValue('MODAL1');
    await expect(page.getByLabel('21点建房等待时长')).toHaveValue('90');
    fail = false;
    await (embedded ? dialog.getByRole('button', { name: '加入房间', exact: true }) : submit).click();
    await expect(page.locator('.multi-mode-view')).toBeVisible();
    await expect(dialog).not.toBeVisible();
    expect(posts.at(-1)).toEqual(embedded ? { path: '/api/multi/room/join', body: { room_id: 'MODAL1' } } : { path: '/api/multi/room/create', body: { turn_timeout_seconds: 90 } });
    expect(posts.filter(post => /\/(bet|ready|start)$/.test(post.path))).toHaveLength(0);
  });
}

test('21点设置弹窗转到房间列表不叠加遮罩', async ({ page }) => {
  await setup(page);
  await page.locator('.blackjack-card').click();
  await page.getByRole('button', { name: /多人对战/ }).click();
  await page.getByRole('dialog', { name: '21点房间设置', exact: true }).getByRole('button', { name: '房间列表', exact: true }).click();
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  await expect(page.getByRole('dialog', { name: '房间列表', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '关闭房间列表' }).click();
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  await expect(page.locator('.mode-panel')).toBeVisible();
});
