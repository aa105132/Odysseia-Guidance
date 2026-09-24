import { expect, test, type Page } from '@playwright/test';

async function prepare(page: Page, validAtlas = true) {
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:voice', 'true');
    localStorage.setItem('yueyue:voice-volume', '.47');
    localStorage.setItem('yueyue:music', 'false');
    (window as any).__mascotTracks = [];
    class TestAudio {
      src: string; original: string; volume = 1; paused = true;
      onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = this.original = src; (window as any).__mascotTracks.push(this); }
      play() { this.paused = false; return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    }
    (window as any).Audio = TestAudio;
  });
  // 组件行为测试固定图集几何；实际角色素材另做完整图集验收。
  await page.route('**/ui/yueyue/spritesheet.webp*', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: `<svg xmlns="http://www.w3.org/2000/svg" width="1536" height="${validAtlas ? 2288 : 1872}"><rect width="1536" height="2288" fill="#7a9eab"/></svg>`,
  }));
  await page.route('**/mascot-test', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app"></div><script type="module" src="/tests/mascot-harness.ts"></script></body></html>' }));
  await page.goto('/mascot-test');
  await expect(page.getByRole('button', { name: '和月月打招呼' })).toBeVisible();
  if (validAtlas) await expect(page.locator('.yueyue-mascot-sprite')).toBeVisible();
}
async function voiceTracks(page: Page) {
  return page.evaluate(() => (window as any).__mascotTracks.filter((track: any) => track.original.includes('/voice/')).map((track: any) => ({ original: track.original, volume: track.volume, paused: track.paused })));
}

test('待机不自动发声，离屏隐藏和减少动态时停止切帧', async ({ page }) => {
  await page.clock.install();
  await prepare(page);
  const sprite = page.locator('.yueyue-mascot-sprite');
  await page.clock.runFor(290);
  await expect(sprite).toHaveAttribute('data-frame', '1');
  expect(await voiceTracks(page)).toEqual([]);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const pausedFrame = await sprite.getAttribute('data-frame');
  await page.clock.runFor(1500);
  await expect(sprite).toHaveAttribute('data-frame', pausedFrame!);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.runFor(1500);
  await expect(sprite).toHaveAttribute('data-frame', '0');
  await page.clock.runFor(1000);
  await expect(sprite).toHaveAttribute('data-frame', '0');
  await page.getByRole('button', { name: '和月月打招呼' }).click();
  await expect(sprite).toHaveAttribute('data-row', '0');
  await expect(sprite).toHaveAttribute('data-frame', '0');
  expect(await voiceTracks(page)).toHaveLength(1);
});

test('点击有动作与豆包语音，连续点击不重播，遵循语音音量和开关', async ({ page }) => {
  await page.clock.install();
  await prepare(page);
  const button = page.getByRole('button', { name: '和月月打招呼' });
  await button.click();
  await expect(page.locator('.yueyue-mascot-sprite')).toHaveAttribute('data-animation', 'waving');
  await expect(page.locator('.yueyue-mascot-speech')).toContainText('我在呢，今天想玩什么？');
  await button.click();
  expect(await voiceTracks(page)).toEqual([{ original: '/audio/voice/doubao-20260924-speed1/pet_hello.mp3', volume: .47, paused: false }]);
  await page.clock.runFor(2700);
  await button.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.yueyue-mascot-sprite')).toHaveAttribute('data-animation', 'review');
  expect((await voiceTracks(page)).at(-1)!.original).toContain('/doubao-20260924-speed1/pet_touch.mp3');
  await page.getByRole('button', { name: '关闭语音' }).click();
  expect((await voiceTracks(page)).every(track => track.paused)).toBe(true);
  await page.clock.runFor(2700);
  await button.click();
  await expect(page.locator('.yueyue-mascot-speech')).toContainText('这次也一起加油吧！');
  expect(await voiceTracks(page)).toHaveLength(2);
  await page.getByRole('button', { name: '卸载月月' }).click();
  await expect(button).toHaveCount(0);
  await page.clock.runFor(8000);
  expect(await voiceTracks(page)).toHaveLength(2);
});

test('指针凝视使用完整 v2 方向，正上与正左不混淆', async ({ page }) => {
  await prepare(page);
  const box = (await page.getByRole('button', { name: '和月月打招呼' }).boundingBox())!;
  const sprite = page.locator('.yueyue-mascot-sprite');
  await page.mouse.move(box.x + box.width / 2, box.y - 40);
  await expect(sprite).toHaveAttribute('data-row', '9');
  await expect(sprite).toHaveAttribute('data-frame', '0');
  await page.mouse.move(box.x - 60, box.y + box.height / 2);
  await expect(sprite).toHaveAttribute('data-row', '10');
  await expect(sprite).toHaveAttribute('data-frame', '4');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(sprite).toHaveAttribute('data-animation', 'idle');
});

test('竖屏自动旋转后触屏可互动且控件在可用区域内', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  const page = await context.newPage();
  await prepare(page);
  await expect(page.locator('html')).toHaveAttribute('data-activity-rotated', 'true');
  const button = page.getByRole('button', { name: '和月月打招呼' });
  await expect(button).toBeInViewport({ ratio: 1 });
  await button.tap();
  await expect(page.locator('.yueyue-mascot-sprite')).toHaveAttribute('data-animation', 'waving');
  expect(await voiceTracks(page)).toHaveLength(1);
  await expect(page.locator('.yueyue-mascot-speech')).toBeInViewport({ ratio: 1 });
  await context.close();
});

test('减少动态仍可键盘互动，图集未完成时保留月月静态后备', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await prepare(page, false);
  await expect(page.locator('.yueyue-mascot-fallback')).toBeVisible();
  await expect(page.locator('.yueyue-mascot-sprite')).toHaveCount(0);
  const button = page.getByRole('button', { name: '和月月打招呼' });
  await button.focus();
  await page.keyboard.press('Space');
  await expect(page.locator('.yueyue-mascot-speech')).toContainText('我在呢，今天想玩什么？');
  expect(await voiceTracks(page)).toHaveLength(1);
});
