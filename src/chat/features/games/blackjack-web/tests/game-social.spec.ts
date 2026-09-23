import { expect, test, type Page } from '@playwright/test';

async function mountSocial(page: Page, single = false) {
  await page.addInitScript(() => {
    (window as any).__audioCalls = [];
    class TestAudio {
      src: string; paused = true; volume = 1; loop = false; onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = src; (window as any).__audioCalls.push({ kind: 'create', src, audio: this }); }
      play() { this.paused = false; (window as any).__audioCalls.push({ kind: 'play', src: this.src }); return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    }
    (window as any).Audio = TestAudio;
  });
  await page.goto('/');
  await page.evaluate(async ({ single }) => {
    const compiled = await (await fetch('/src/GameSocial.vue')).text();
    const vuePath = compiled.match(/from ["']([^"']*\/vue\.js[^"']*)["']/)?.[1];
    if (!vuePath) throw new Error('没有找到组件使用的Vue模块');
    const vue = await import(vuePath);
    const component = await import('/src/GameSocial.vue' as string);
    const audio = await import('/src/gameAudio.ts' as string);
    const vueModule = vue as any;
    const old = document.querySelector('#app') as any;
    old?.__vue_app__?.unmount();
    document.body.innerHTML = '<div id="app"><div id="social-test"></div><button data-game-avatar="one" style="position:absolute;left:20px;top:30px">头像一</button><button data-game-avatar="two" style="position:absolute;left:300px;top:50px">头像二</button></div>';
    (window as any).__socialState = { calls: [], nextEvents: [], eventCount: 10 };
    const controls = vueModule.reactive({ scopeType: single ? 'single' : 'table', roomId: 'ROOM-A', viewerId: 'one' });
    const instance = vueModule.ref();
    const apiCall = async (endpoint: string, method: string, body?: any) => {
      const state = (window as any).__socialState;
      state.calls.push({ endpoint, method, body });
      if (state.delayNext) { state.delayNext = false; await new Promise<void>(resolve => { state.release = resolve; }); }
      if (method === 'POST') {
        const event = { event_id: ++state.eventCount, user_id: 'one', username: '牌友一', kind: body.kind, item_id: body.item_id, text: body.kind === 'chat' ? '很高兴和你一起玩' : '倒茶', target_id: body.target_id, target_username: '牌友二', timestamp: Date.now() / 1000 };
        state.nextEvents.push(event);
        return { event, cursor: state.eventCount };
      }
      const events = endpoint.includes('?after=') ? state.nextEvents.splice(0) : [];
      return { cursor: state.eventCount, events };
    };
    const app = vueModule.createApp({ setup: () => () => vueModule.h(component.default, { ...controls, ref: instance, members: [{ user_id: 'one', username: '牌友一' }, { user_id: 'two', username: '牌友二' }], apiCall }) });
    audio.mountGameAudio();
    app.mount('#social-test');
    (window as any).__socialTest = { controls, instance, app, audio };
  }, { single });
}

test('快捷聊天广播去重、头像互动原创动画、切房清理与键盘关闭', async ({ page }) => {
  await mountSocial(page);
  await expect.poll(() => page.evaluate(() => (window as any).__socialState.calls.length)).toBeGreaterThan(0);
  await page.getByRole('button', { name: '打开牌桌聊天' }).click();
  await page.getByRole('button', { name: '很高兴和你一起玩', exact: true }).click();
  await expect(page.locator('.social-messages p').filter({ hasText: '牌友一' })).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => (window as any).__socialState.calls.filter((call: any) => call.endpoint.includes('?after=')).length)).toBeGreaterThan(0);
  await expect(page.locator('.social-messages p').filter({ hasText: '牌友一' })).toHaveCount(1);
  await page.evaluate(() => (window as any).__socialTest.instance.value.openInteraction('two'));
  await expect(page.getByRole('dialog', { name: '牌友互动' })).toBeVisible();
  await page.getByRole('button', { name: '倒茶', exact: true }).click();
  await expect(page.locator('.game-social-animation svg')).toBeVisible();
  await page.evaluate(() => { (window as any).__socialTest.controls.roomId = 'ROOM-B'; });
  await expect(page.locator('.game-social-animation')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => (window as any).__socialState.calls.some((call: any) => call.endpoint.endsWith('/ROOM-B')))).toBe(true);
  await page.getByRole('button', { name: '打开牌桌聊天' }).click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('region', { name: '牌桌聊天' })).toHaveCount(0);
});

test('单人社交只本地反馈，减少动画偏好和卸载不遗留播放', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mountSocial(page, true);
  await page.getByRole('button', { name: '打开牌桌聊天' }).click();
  await page.getByRole('button', { name: '谢谢你', exact: true }).click();
  await expect(page.locator('.social-notices')).toContainText('谢谢你');
  await page.evaluate(() => (window as any).__socialTest.instance.value.openInteraction('two'));
  await page.getByRole('button', { name: '鲜花', exact: true }).click();
  await expect(page.locator('.game-social-animation')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__socialState.calls.length)).toBe(0);
  await page.evaluate(() => { (window as any).__socialTest.app.unmount(); (window as any).__socialTest.audio.unmountGameAudio(); });
  await expect(page.locator('.game-social')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'create').every((call: any) => call.audio.paused))).toBe(true);
});

test('用户音乐按场景切换、语音压低背景且遵守独立开关', async ({ page }) => {
  await mountSocial(page, true);
  await page.getByRole('button', { name: '打开牌桌聊天' }).click();
  await page.evaluate(() => (window as any).__socialTest.audio.setMusicEnabled(true));
  await expect.poll(() => page.evaluate(() => (window as any).__audioCalls.some((call: any) => call.kind === 'create' && /music\/(Exciting1|Exciting2|lobby)\.mp3/.test(call.src)))).toBe(true);
  await page.evaluate(() => (window as any).__socialTest.audio.setGameAudioScene('playing'));
  await expect.poll(() => page.evaluate(() => (window as any).__audioCalls.some((call: any) => call.kind === 'create' && call.src.endsWith('/Normal.mp3')))).toBe(true);
  await page.evaluate(() => (window as any).__socialTest.audio.playGameVoice('hello'));
  expect(await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'create' && call.src.endsWith('/Normal.mp3')).at(-1).audio.volume)).toBe(0.10);
  await page.evaluate(() => (window as any).__socialTest.audio.setSoundEnabled(false));
  expect(await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'create' && call.src.endsWith('/Normal.mp3')).at(-1).audio.volume)).toBe(0.36);
  const before = await page.evaluate(() => (window as any).__audioCalls.length);
  await page.evaluate(() => (window as any).__socialTest.audio.playGameVoice('thanks'));
  expect(await page.evaluate(() => (window as any).__audioCalls.length)).toBe(before);
  await page.evaluate(() => (window as any).__socialTest.audio.playRoundMusic('win'));
  await expect.poll(() => page.evaluate(() => (window as any).__audioCalls.some((call: any) => call.kind === 'create' && call.src.endsWith('/win.mp3')))).toBe(true);
  await page.evaluate(() => { const calls = (window as any).__audioCalls; calls.filter((call: any) => call.kind === 'create' && call.src.endsWith('/win.mp3')).at(-1).audio.onended(); });
  expect(await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'create').at(-1).src)).toBe('/audio/music/Normal.mp3');
  await page.evaluate(() => { (window as any).__socialTest.audio.playRoundMusic('loss'); (window as any).__socialTest.audio.setGameAudioScene('playing'); });
  expect(await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'create').at(-1).src)).toBe('/audio/music/Normal.mp3');
  expect(await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'create' && call.src.endsWith('/lose.mp3')).at(-1).audio.paused)).toBe(true);
  await page.evaluate(() => (window as any).__socialTest.audio.setMusicEnabled(false));
  expect(await page.evaluate(() => localStorage.getItem('yueyue:music'))).toBe('false');
});

test('568×320互动菜单不溢出并可滚动使用全部快捷句', async ({ page }) => {
  await page.setViewportSize({ width: 568, height: 320 });
  await mountSocial(page, true);
  await page.getByRole('button', { name: '打开牌桌聊天' }).click();
  await page.getByRole('button', { name: '你是MM还是GG？', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__audioCalls.some((call: any) => call.kind === 'play' && call.src.endsWith('/mm_or_gg.mp3')))).toBe(true);
  await page.getByRole('button', { name: '再来一局吧', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: '再来一局吧', exact: true })).toBeInViewport({ ratio: 1 });
  await page.evaluate(() => (window as any).__socialTest.instance.value.openInteraction('two'));
  const menu = page.getByRole('dialog', { name: '牌友互动' });
  await expect(menu).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: '../../../../../tmp/game-social-568.png' });
});

test('切房和页面隐藏废弃旧响应，恢复不补播旧语音', async ({ page }) => {
  await mountSocial(page);
  await page.getByRole('button', { name: '打开牌桌聊天' }).click();
  await page.evaluate(() => { (window as any).__socialState.delayNext = true; });
  await page.getByRole('button', { name: '谢谢你', exact: true }).click();
  await expect.poll(() => page.evaluate(() => Boolean((window as any).__socialState.release))).toBe(true);
  await page.evaluate(() => { (window as any).__socialTest.controls.roomId = 'ROOM-B'; (window as any).__socialState.release(); });
  await expect(page.locator('.social-notices p')).toHaveCount(0);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
  const plays = await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'play' && call.src.includes('/voice/')).length);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => page.evaluate(() => (window as any).__socialState.calls.filter((call: any) => call.endpoint.endsWith('/ROOM-B')).length)).toBeGreaterThan(1);
  expect(await page.evaluate(() => (window as any).__audioCalls.filter((call: any) => call.kind === 'play' && call.src.includes('/voice/')).length)).toBe(plays);
});
