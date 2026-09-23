import { expect, test, type Page } from '@playwright/test';

async function mount(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:sound', 'true'); localStorage.setItem('yueyue:music', 'false');
    (window as any).__voice = [];
    (window as any).Audio = class {
      src: string; paused = true; volume = 1; loop = false; onended = null; onerror = null;
      constructor(src: string) { this.src = src; }
      play() { this.paused = false; (window as any).__voice.push(this.src); return Promise.resolve(); }
      pause() { this.paused = true; } removeAttribute() { this.src = ''; } load() {}
    };
  });
  await page.route('**/api/**', route => route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '测试用户', balance: 1000 } }));
  await page.goto('/');
  await expect.poll(() => page.evaluate(() => Boolean((document.querySelector('#app') as any)?.__vue_app__))).toBe(true);
  await page.clock.install({ time: new Date('2026-09-23T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-09-23T12:00:01Z'));
  await page.evaluate(async () => {
    const compiled = await (await fetch('/src/GameSocial.vue')).text();
    const vuePath = compiled.match(/from ["']([^"']*\/vue\.js[^"']*)["']/)?.[1];
    if (!vuePath) throw new Error('找不到 Vue 模块');
    const vue: any = await import(vuePath);
    const component = await import('/src/GameSocial.vue' as string);
    const audio = await import('/src/gameAudio.ts' as string);
    const viewport = await import('/src/activityViewport.ts' as string);
    (document.querySelector('#app') as any)?.__vue_app__?.unmount();
    document.body.innerHTML = '<div id="app" style="background:#524c72"><div id="social-test"></div><button data-game-avatar="one" style="position:absolute;left:16px;bottom:45px;width:52px;height:52px">自己</button><button data-game-avatar="two" style="position:absolute;left:16px;top:55px;width:52px;height:52px">牌友二</button><button data-game-avatar="three" style="position:absolute;right:16px;top:55px;width:52px;height:52px">牌友三</button></div><div style="position:fixed;right:0;top:0;bottom:0;width:64px;background:#777;z-index:99999"></div>';
    history.replaceState({}, '', '/?frame_id=social-test'); viewport.updateActivityViewport();
    const controls = vue.reactive({ roomId: 'BUBBLE1', members: ['one', 'two', 'three', 'absent'].map((id, index) => ({ user_id: id, username: `牌友${index + 1}` })) });
    const state = { events: [] as any[], eventId: 0 };
    const instance = vue.ref();
    const apiCall = async (path: string, method: string, body?: any) => {
      if (method === 'POST') {
        const event = { event_id: ++state.eventId, user_id: 'one', username: '自己', kind: body.kind, item_id: body.item_id, text: body.item_id === 'mm_or_gg' ? '你是MM还是GG？' : '谢谢你', timestamp: Date.now() / 1000 };
        state.events.push(event);
        return { cursor: state.eventId, event };
      }
      return { cursor: state.eventId, events: path.includes('?after=') ? state.events.splice(0) : [] };
    };
    const app = vue.createApp({ setup: () => () => vue.h(component.default, { scopeType: 'table', viewerId: 'one', ...controls, hideToggle: true, ref: instance, apiCall }) });
    audio.mountGameAudio(); app.mount('#social-test');
    (window as any).__bubbles = { controls, state, instance, app, audio };
  });
}

async function queue(page: Page, userId: string, text: string) {
  await page.evaluate(({ userId, text }) => {
    const { state } = (window as any).__bubbles;
    state.events.push({ event_id: ++state.eventId, user_id: userId, username: userId, kind: 'chat', item_id: 'thanks', text, timestamp: Date.now() / 1000 });
  }, { userId, text });
}

async function assertAnchored(page: Page, userId: string) {
  const bubble = page.locator(`[data-notice-user="${userId}"]`);
  await expect(bubble).toBeVisible();
  const b = (await bubble.boundingBox())!;
  const a = (await page.locator(`[data-game-avatar="${userId}"]`).boundingBox())!;
  const dx = Math.max(a.x - b.x - b.width, b.x - a.x - a.width, 0);
  const dy = Math.max(a.y - b.y - b.height, b.y - a.y - a.height, 0);
  expect(Math.hypot(dx, dy), '气泡紧靠发送者头像').toBeLessThanOrEqual(12);
  expect(Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) <= 0 || Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) <= 0, '气泡不遮住头像').toBe(true);
  expect(b.x).toBeGreaterThanOrEqual(0); expect(b.y).toBeGreaterThanOrEqual(0);
  expect(b.x + b.width).toBeLessThanOrEqual(page.viewportSize()!.width - 64 + 1);
  expect(b.y + b.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1);
  await expect(bubble).toHaveCSS('pointer-events', 'none');
  expect(await bubble.evaluate(element => {
    const box = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
  }), '气泡自身不能截获触控命中').toBe(false);
  await page.locator(`[data-game-avatar="${userId}"]`).click({ trial: true });
}

test('消息各自依附头像、同人替换、缺头像不漂边，过期与切房清理', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 }); await mount(page);
  await queue(page, 'one', '第一条'); await queue(page, 'two', '牌友二说话');
  await queue(page, 'absent', '没有头像的消息'); await queue(page, 'outsider', '不在房间的消息');
  await page.clock.runFor(1600);
  await expect(page.locator('.social-notices p')).toHaveCount(2);
  await assertAnchored(page, 'one'); await assertAnchored(page, 'two');
  await queue(page, 'one', '第二条替换第一条'); await page.clock.runFor(1500);
  await expect(page.locator('[data-notice-user="one"]')).toHaveText('one：第二条替换第一条');
  await expect(page.locator('.social-notices p')).toHaveCount(2);
  await page.evaluate(() => (window as any).__bubbles.instance.value.openChat());
  await expect(page.locator('.social-messages')).toContainText('没有头像的消息');
  await expect(page.locator('.social-messages')).toContainText('不在房间的消息');
  await page.keyboard.press('Escape');
  await page.clock.runFor(2400);
  await expect(page.locator('[data-notice-user="two"]')).toHaveCount(0);
  await expect(page.locator('[data-notice-user="one"]')).toBeVisible();
  await page.evaluate(() => document.querySelector('[data-game-avatar="one"]')?.remove());
  await page.clock.runFor(100);
  await expect(page.locator('.social-notices p')).toHaveCount(0);
  await queue(page, 'three', '切房前消息'); await page.clock.runFor(1500);
  await expect(page.locator('[data-notice-user="three"]')).toBeVisible();
  await page.evaluate(() => { (window as any).__bubbles.controls.roomId = 'BUBBLE2'; });
  await expect(page.locator('.social-notices p')).toHaveCount(0);
});

test('头像气泡随横竖屏移动，长句换行且聊天栏固定逻辑右下角', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 }); await mount(page);
  await queue(page, 'one', '已有气泡也应跟着旋转'); await page.clock.runFor(1600);
  const eventId = await page.locator('[data-notice-user="one"]').textContent();
  await page.setViewportSize({ width: 390, height: 844 }); await page.clock.runFor(100);
  await assertAnchored(page, 'one');
  await expect(page.locator('[data-notice-user="one"]')).toHaveText(eventId!);
  for (const viewport of [{ width: 844, height: 390 }, { width: 390, height: 844 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(viewport);
    await queue(page, 'one', '自己也有头像气泡');
    await queue(page, 'two', '这是牌友二的一条很长的消息，用来验证靠近左上角时会自动换行，并且不会遮住任何人的头像。');
    await queue(page, 'three', '右侧牌友也能说话');
    await page.clock.runFor(1600);
    for (const user of ['one', 'two', 'three']) await assertAnchored(page, user);
    await page.screenshot({ path: `../../../../../tmp/social-avatar-bubbles-${viewport.width}.png` });
    await page.evaluate(() => (window as any).__bubbles.instance.value.openChat());
    const panel = page.getByRole('region', { name: '牌桌聊天' });
    await expect(panel).toBeInViewport({ ratio: 1 });
    const gap = await panel.evaluate(async element => {
      const viewport = await import('/src/activityViewport.ts' as string);
      const rect = element.getBoundingClientRect();
      const first = viewport.clientPointToActivity(rect.left, rect.top);
      const last = viewport.clientPointToActivity(rect.right, rect.bottom);
      const root = document.getElementById('app')!;
      return { right: root.clientWidth - Math.max(first.x, last.x), bottom: root.clientHeight - Math.max(first.y, last.y), transform: getComputedStyle(element).transform };
    });
    expect(gap.right).toBeGreaterThanOrEqual(8); expect(gap.right).toBeLessThanOrEqual(14);
    expect(gap.bottom).toBeGreaterThanOrEqual(30); expect(gap.bottom).toBeLessThanOrEqual(32);
    expect(gap.transform).toBe('none');
    await page.screenshot({ path: `../../../../../tmp/social-avatar-chat-${viewport.width}.png` });
    await page.keyboard.press('Escape');
  }
});

test('发送快捷语音在自己头像旁显示且轮询不重播，离房成员气泡立即清理', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 }); await mount(page);
  await page.evaluate(() => (window as any).__bubbles.instance.value.openChat());
  await page.getByRole('button', { name: '你是MM还是GG？', exact: true }).click();
  await expect(page.locator('[data-notice-user="one"]')).toHaveText('自己：你是MM还是GG？');
  await page.clock.runFor(1600);
  expect(await page.evaluate(() => (window as any).__voice.filter((src: string) => src.endsWith('/mm_or_gg.mp3')).length)).toBe(1);
  await page.keyboard.press('Escape');
  await assertAnchored(page, 'one');
  await queue(page, 'two', '马上离开'); await page.clock.runFor(1500);
  await expect(page.locator('[data-notice-user="two"]')).toBeVisible();
  await page.evaluate(() => { const controls = (window as any).__bubbles.controls; controls.members = controls.members.filter((member: any) => member.user_id !== 'two'); });
  await page.clock.runFor(100);
  await expect(page.locator('[data-notice-user="two"]')).toHaveCount(0);
});
