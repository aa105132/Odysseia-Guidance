import { expect, test, type Locator, type Page } from '@playwright/test';

async function setup(page: Page) {
  await page.addInitScript(() => {
    const NativeContext = window.AudioContext;
    const trace = { gains: [] as GainNode[], tracks: [] as TestAudio[] };
    (window as any).__volumeTrace = trace;
    class TestAudio {
      src: string; original: string; paused = true; volume = 1; loop = false;
      onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = this.original = src; trace.tracks.push(this); }
      play() { this.paused = false; return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    }
    (window as any).Audio = TestAudio;
    window.AudioContext = class extends NativeContext {
      createGain() { const gain = super.createGain(); trace.gains.push(gain); return gain; }
    };
  });
  await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '声音测试', avatar_url: '', balance: 2000 } }));
  await page.goto('/?dev_user_id=123456789012345678');
  await page.getByRole('button', { name: '声音设置' }).click();
  return page.getByRole('dialog', { name: '声音设置' });
}

async function changeVolume(slider: Locator, value: number) {
  await slider.evaluate((input, next) => {
    (input as HTMLInputElement).value = String(next);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}

function volumeOf(page: Page, category: string) {
  return page.evaluate(prefix => (window as any).__volumeTrace.tracks.findLast((track: any) => !track.paused && track.original.includes(prefix))?.volume ?? null, category);
}

test('三个音量独立即时生效，语音压低配乐后恢复手动值，静音不丢设置', async ({ page }) => {
  const menu = await setup(page);
  const effects = menu.getByRole('slider', { name: '音效音量', exact: true });
  const voice = menu.getByRole('slider', { name: '语音音量', exact: true });
  const music = menu.getByRole('slider', { name: '音乐音量', exact: true });
  await expect(effects).toHaveValue('32');
  await expect(voice).toHaveValue('90');
  await expect(music).toBeDisabled();
  await changeVolume(effects, 55);
  await expect.poll(() => page.evaluate(() => (window as any).__volumeTrace.gains[0].gain.value)).toBeCloseTo(.55);
  await changeVolume(voice, 60);
  await menu.getByRole('checkbox', { name: /背景音乐/ }).check();
  await changeVolume(music, 72);
  await expect.poll(() => volumeOf(page, '/music/')).toBe(.72);
  await page.evaluate(async () => (await import('/src/gameAudio.ts' as string)).playGameVoice('hello'));
  await expect.poll(() => volumeOf(page, '/voice/')).toBe(.6);
  await expect.poll(() => volumeOf(page, '/music/')).toBeCloseTo(.2);
  await changeVolume(music, 36);
  await expect.poll(() => volumeOf(page, '/music/')).toBeCloseTo(.1);
  await changeVolume(voice, 0);
  await expect.poll(() => volumeOf(page, '/voice/')).toBeNull();
  await expect.poll(() => volumeOf(page, '/music/')).toBe(.36);
  await page.evaluate(async () => (await import('/src/gameAudio.ts' as string)).playGameVoice('thanks'));
  await expect.poll(() => volumeOf(page, '/voice/')).toBeNull();
  await changeVolume(voice, 50);
  await menu.getByRole('checkbox', { name: /游戏音效/ }).uncheck();
  await expect(effects).toBeDisabled();
  await expect(voice).toBeEnabled();
  await page.evaluate(async () => (await import('/src/gameAudio.ts' as string)).playGameVoice('hello'));
  await expect.poll(() => volumeOf(page, '/voice/')).toBe(.5);
  await menu.getByRole('checkbox', { name: /月月与聊天语音/ }).uncheck();
  await expect(voice).toBeDisabled();
  await expect.poll(() => volumeOf(page, '/voice/')).toBeNull();
  await expect.poll(() => page.evaluate(() => (window as any).__volumeTrace.gains[0].gain.value)).toBe(0);
  await menu.getByRole('checkbox', { name: /游戏音效/ }).check();
  await expect.poll(() => page.evaluate(() => (window as any).__volumeTrace.gains[0].gain.value)).toBeCloseTo(.55);
  await page.reload();
  await page.getByRole('button', { name: '声音设置' }).click();
  await expect(effects).toHaveValue('55');
  await expect(voice).toHaveValue('50');
  await expect(menu.getByRole('checkbox', { name: /月月与聊天语音/ })).not.toBeChecked();
  await expect(menu.getByRole('checkbox', { name: /游戏音效/ })).toBeChecked();
  await expect(music).toHaveValue('36');
  expect(await page.evaluate(() => ['sound-volume', 'voice-volume', 'music-volume'].map(key => localStorage.getItem(`yueyue:${key}`)))).toEqual(['0.55', '0.5', '0.36']);
});

test('损坏或超范围的音量设置不会破坏音频，滑条可用键盘操作', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:sound-volume', 'oops');
    localStorage.setItem('yueyue:voice-volume', '12');
    localStorage.setItem('yueyue:music-volume', '-3');
  });
  const menu = await setup(page);
  const effects = menu.getByRole('slider', { name: '音效音量', exact: true });
  await expect(effects).toHaveValue('32');
  await expect(menu.getByRole('slider', { name: '语音音量', exact: true })).toHaveValue('100');
  await expect(menu.getByRole('slider', { name: '音乐音量', exact: true })).toHaveValue('0');
  await effects.focus();
  await effects.press('End');
  await effects.press('ArrowLeft');
  await expect(effects).toHaveValue('99');
  await expect.poll(() => page.evaluate(() => (window as any).__volumeTrace.gains[0].gain.value)).toBeCloseTo(.99);
});

test('竖屏自动旋转后全部音量可触达，弹窗不越过可用视口', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const menu = await setup(page);
  await expect(page.locator('html')).toHaveAttribute('data-activity-rotated', 'true');
  await expect(menu).toBeInViewport({ ratio: 1 });
  await menu.getByRole('checkbox', { name: /背景音乐/ }).check();
  for (const name of ['音效音量', '语音音量', '音乐音量']) {
    const slider = menu.getByRole('slider', { name, exact: true });
    await slider.scrollIntoViewIfNeeded();
    await expect(slider).toBeInViewport({ ratio: 1 });
    await changeVolume(slider, 42);
    await expect(slider).toHaveValue('42');
  }
  await page.screenshot({ path: '../../../../../tmp/audio-volume-mobile.png' });
});

test('旧总静音迁移为语音静音，之后音效与语音互不覆盖', async ({ page }) => {
  const menu = await setup(page);
  await page.evaluate(() => {
    localStorage.setItem('yueyue:sound', 'false');
    localStorage.removeItem('yueyue:voice');
  });
  await page.reload();
  await page.getByRole('button', { name: '声音设置' }).click();
  await expect(menu.getByRole('checkbox', { name: /游戏音效/ })).not.toBeChecked();
  await expect(menu.getByRole('checkbox', { name: /月月与聊天语音/ })).not.toBeChecked();
  await menu.getByRole('checkbox', { name: /游戏音效/ }).check();
  await page.reload();
  await page.getByRole('button', { name: '声音设置' }).click();
  await expect(menu.getByRole('checkbox', { name: /游戏音效/ })).toBeChecked();
  await expect(menu.getByRole('checkbox', { name: /月月与聊天语音/ })).not.toBeChecked();
});
