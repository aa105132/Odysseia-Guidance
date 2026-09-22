import { expect, test } from '@playwright/test';

test('声音开关独立生效且刷新保留，音乐在用户操作后才开始', async ({ page }) => {
  await page.addInitScript(() => {
    const NativeContext = window.AudioContext;
    const trace = { starts: 0, contexts: [] as AudioContext[], gains: [] as GainNode[], tracks: [] as TestAudio[] };
    (window as any).__audioTrace = trace;
    class TestAudio {
      src: string; paused = true; volume = 1; loop = false;
      onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = src; trace.tracks.push(this); }
      play() { this.paused = false; return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    }
    (window as any).Audio = TestAudio;
    window.AudioContext = class extends NativeContext {
      constructor(options?: AudioContextOptions) { super(options); trace.contexts.push(this); }
      createGain() { const gain = super.createGain(); trace.gains.push(gain); return gain; }
      createOscillator() {
        const oscillator = super.createOscillator();
        const start = oscillator.start.bind(oscillator);
        oscillator.start = (...args) => { trace.starts++; start(...args); };
        return oscillator;
      }
    };
  });
  await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '音乐牌友', avatar_url: '/character/normal.webp', balance: 2000 } }));
  await page.goto('/?dev_user_id=123456789012345678');
  await expect(page.getByRole('button', { name: '声音设置' })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__audioTrace.starts)).toBe(0);
  expect(await page.evaluate(() => (window as any).__audioTrace.tracks.length)).toBe(0);
  await page.getByRole('button', { name: '声音设置' }).click();
  const menu = page.getByRole('dialog', { name: '声音设置' });
  const sound = menu.getByRole('checkbox', { name: /游戏音效/ });
  const music = menu.getByRole('checkbox', { name: /背景音乐/ });
  await expect(sound).toBeChecked();
  await expect(music).not.toBeChecked();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.starts)).toBeGreaterThan(0);
  await sound.uncheck();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.gains[0].gain.value)).toBe(0);
  const mutedEffects = await page.evaluate(() => (window as any).__audioTrace.starts);
  await music.check();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.tracks.some((track: any) => !track.paused && /music\/(Exciting1|Exciting2|lobby)\.mp3$/.test(track.src)))).toBe(true);
  expect(await page.evaluate(() => (window as any).__audioTrace.starts)).toBe(mutedEffects);
  expect(await page.evaluate(() => (window as any).__audioTrace.gains[0].gain.value)).toBe(0);
  await music.uncheck();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.tracks.every((track: any) => track.paused))).toBe(true);
  const muted = await page.evaluate(() => (window as any).__audioTrace.starts);
  await menu.getByRole('button', { name: '关闭' }).click();
  await page.getByRole('button', { name: '声音设置' }).click();
  expect(await page.evaluate(() => (window as any).__audioTrace.starts)).toBe(muted);
  await page.reload();
  await page.getByRole('button', { name: '声音设置' }).click();
  await expect(sound).not.toBeChecked();
  await expect(music).not.toBeChecked();
  expect(await page.evaluate(() => (window as any).__audioTrace.starts)).toBe(0);
  expect(await page.evaluate(() => (window as any).__audioTrace.tracks.length)).toBe(0);
  await sound.check();
  await menu.getByRole('button', { name: '关闭' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.starts)).toBeGreaterThan(0);
  expect(await page.evaluate(() => (window as any).__audioTrace.tracks.length)).toBe(0);
});

test('音乐开启状态刷新保留，刷新后仍等待一次真实手势', async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__mediaPlays = [];
    HTMLMediaElement.prototype.play = function () { (window as any).__mediaPlays.push(this.src); return Promise.resolve(); };
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '音乐牌友', avatar_url: '', balance: 2000 } }));
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: '声音设置' }).click();
  const menu = page.getByRole('dialog', { name: '声音设置' });
  await menu.getByRole('checkbox', { name: /游戏音效/ }).uncheck();
  await menu.getByRole('checkbox', { name: /背景音乐/ }).check();
  await expect.poll(() => page.evaluate(() => (window as any).__mediaPlays.length)).toBeGreaterThan(0);
  await page.reload();
  await expect(page.getByRole('button', { name: '声音设置' })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__mediaPlays)).toEqual([]);
  await page.getByRole('button', { name: '声音设置' }).click();
  await expect(menu.getByRole('checkbox', { name: /游戏音效/ })).not.toBeChecked();
  await expect(menu.getByRole('checkbox', { name: /背景音乐/ })).toBeChecked();
  await expect.poll(() => page.evaluate(() => (window as any).__mediaPlays.some((src: string) => src.includes('/audio/music/')))).toBe(true);
});
