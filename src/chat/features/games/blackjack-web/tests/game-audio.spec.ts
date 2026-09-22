import { expect, test } from '@playwright/test';

test('声音开关独立生效且刷新保留，音乐在用户操作后才开始', async ({ page }) => {
  await page.addInitScript(() => {
    const NativeContext = window.AudioContext;
    const trace = { starts: 0, contexts: [] as AudioContext[], gains: [] as GainNode[] };
    (window as any).__audioTrace = trace;
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
  await expect(page.getByRole('button', { name: '战绩与声音' })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__audioTrace.starts)).toBe(0);
  await page.getByRole('button', { name: '战绩与声音' }).click();
  const menu = page.getByRole('dialog', { name: '战绩与声音' });
  const sound = menu.getByRole('checkbox', { name: /游戏音效/ });
  const music = menu.getByRole('checkbox', { name: /背景音乐/ });
  await expect(sound).toBeChecked();
  await expect(music).not.toBeChecked();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.starts)).toBeGreaterThan(0);
  await sound.uncheck();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.gains[0].gain.value)).toBe(0);
  await music.check();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.gains[1].gain.value)).toBeGreaterThan(0);
  const before = await page.evaluate(() => (window as any).__audioTrace.starts);
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.starts)).toBeGreaterThan(before);
  await music.uncheck();
  await expect.poll(() => page.evaluate(() => (window as any).__audioTrace.gains[1].gain.value)).toBe(0);
  const muted = await page.evaluate(() => (window as any).__audioTrace.starts);
  await menu.getByRole('button', { name: '关闭' }).click();
  await page.getByRole('button', { name: '战绩与声音' }).click();
  expect(await page.evaluate(() => (window as any).__audioTrace.starts)).toBe(muted);
  await page.reload();
  await page.getByRole('button', { name: '战绩与声音' }).click();
  await expect(sound).not.toBeChecked();
  await expect(music).not.toBeChecked();
  expect(await page.evaluate(() => (window as any).__audioTrace.starts)).toBe(0);
});
