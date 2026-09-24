import { expect, test, type Page } from '@playwright/test';

async function setup(page: Page, unlock = true) {
  await page.addInitScript(() => {
    const trace = { tracks: [] as TestAudio[], played: [] as string[], rejectNext: false };
    (window as any).__voiceQueueTrace = trace;
    class TestAudio {
      src: string; original: string; paused = true; volume = 1; loop = false;
      onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = this.original = src; trace.tracks.push(this); }
      play() {
        this.paused = false;
        trace.played.push(this.original.split('/').at(-1)!.replace('.mp3', ''));
        if (trace.rejectNext) { trace.rejectNext = false; return Promise.reject(new Error('媒体播放失败')); }
        return Promise.resolve();
      }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    }
    (window as any).Audio = TestAudio;
  });
  await page.route('**/api/profile', route => route.fulfill({ json: { success: true, user_id: '123456789012345678', username: '报牌测试', avatar_url: '', balance: 2000 } }));
  await page.goto('/?dev_user_id=123456789012345678');
  await expect(page.getByRole('button', { name: '声音设置' })).toBeVisible();
  if (unlock) await page.getByRole('button', { name: '声音设置' }).click();
}

async function play(page: Page, id: string, enqueue = true) {
  await page.evaluate(async ({ id, enqueue }) => {
    await (await import('/src/gameAudio.ts' as string)).playGameVoice(id, { enqueue });
  }, { id, enqueue });
}

async function finish(page: Page, event: 'ended' | 'error' = 'ended') {
  await page.evaluate(event => {
    const track = (window as any).__voiceQueueTrace.tracks.findLast((item: any) => !item.paused && item.original.includes('/voice/'));
    if (!track) throw new Error('没有正在播放的语音');
    track[event === 'ended' ? 'onended' : 'onerror']?.();
  }, event);
}

async function state(page: Page) {
  return page.evaluate(() => {
    const trace = (window as any).__voiceQueueTrace;
    return { played: trace.played, active: trace.tracks.filter((item: any) => !item.paused && item.original.includes('/voice/')).length };
  });
}

test('连续报牌只播放一句，结束或媒体错误后按动作顺序接续', async ({ page }) => {
  await setup(page);
  await play(page, 'card_3');
  await play(page, 'cards_airplane');
  await play(page, 'tile_p1');
  expect(await state(page)).toEqual({ played: ['card_3'], active: 1 });
  await finish(page);
  expect(await state(page)).toEqual({ played: ['card_3', 'cards_airplane'], active: 1 });
  await finish(page, 'error');
  expect(await state(page)).toEqual({ played: ['card_3', 'cards_airplane', 'tile_p1'], active: 1 });
  await finish(page);
  expect(await state(page)).toEqual({ played: ['card_3', 'cards_airplane', 'tile_p1'], active: 0 });
});

for (const cleanup of ['mute', 'volume0', 'stop', 'unmount', 'hidden'] as const) {
  test(`${cleanup} 会停止当前语音并清除待播报牌`, async ({ page }) => {
    await setup(page);
    await play(page, 'card_3');
    await play(page, 'tile_p1');
    await page.evaluate(async cleanup => {
      const api = await import('/src/gameAudio.ts' as string);
      (window as any).__staleVoiceEnd = (window as any).__voiceQueueTrace.tracks.at(-1).onended;
      if (cleanup === 'mute') api.setVoiceEnabled(false);
      if (cleanup === 'volume0') api.setVoiceVolume(0);
      if (cleanup === 'stop') api.stopGameVoice();
      // 大厅与月月挂件共同持有音频引用；卸载应用才能释放全部使用方。
      if (cleanup === 'unmount') (document.getElementById('app') as any).__vue_app__.unmount();
      if (cleanup === 'hidden') {
        Object.defineProperty(document, 'hidden', { configurable: true, value: true });
        document.dispatchEvent(new Event('visibilitychange'));
      }
    }, cleanup);
    expect(await state(page)).toEqual({ played: ['card_3'], active: 0 });
    await page.evaluate(async cleanup => {
      const api = await import('/src/gameAudio.ts' as string);
      if (cleanup === 'mute') api.setVoiceEnabled(true);
      if (cleanup === 'volume0') api.setVoiceVolume(.9);
      if (cleanup === 'unmount') api.mountGameAudio();
      if (cleanup === 'hidden') {
        Object.defineProperty(document, 'hidden', { configurable: true, value: false });
        document.dispatchEvent(new Event('visibilitychange'));
      }
    }, cleanup);
    // 卸载会撤销声音解锁，重新挂载后仍要求一次真实手势。
    await page.mouse.click(4, 4);
    await play(page, 'thanks');
    await page.evaluate(() => (window as any).__staleVoiceEnd?.());
    expect(await state(page)).toEqual({ played: ['card_3', 'thanks'], active: 1 });
    await finish(page);
    expect(await state(page)).toEqual({ played: ['card_3', 'thanks'], active: 0 });
  });
}

test('普通快捷语音立即替换报牌，并清除之前等待的报牌', async ({ page }) => {
  await setup(page);
  await play(page, 'card_3');
  await play(page, 'tile_p1');
  await play(page, 'hello', false);
  expect(await state(page)).toEqual({ played: ['card_3', 'hello'], active: 1 });
  await finish(page);
  expect(await state(page)).toEqual({ played: ['card_3', 'hello'], active: 0 });
});

test('浏览器尚未解锁声音时不会积压历史报牌', async ({ page }) => {
  await setup(page, false);
  await play(page, 'card_3');
  await play(page, 'tile_p1');
  expect(await state(page)).toEqual({ played: [], active: 0 });
  await page.getByRole('button', { name: '声音设置' }).click();
  await play(page, 'thanks');
  await finish(page);
  expect(await state(page)).toEqual({ played: ['thanks'], active: 0 });
});

test('排队最多保留四句，超过八秒的历史报牌不再补播', async ({ page }) => {
  await setup(page);
  await page.clock.install();
  const ids = ['card_3', 'card_4', 'card_5', 'card_6', 'card_7', 'card_8'];
  for (const id of ids) await play(page, id);
  for (let index = 0; index < 5; index++) await finish(page);
  expect(await state(page)).toEqual({ played: ids.slice(0, 5), active: 0 });
  await play(page, 'hello');
  await play(page, 'tile_p1');
  await page.clock.fastForward(8_001);
  await finish(page);
  expect(await state(page)).toEqual({ played: [...ids.slice(0, 5), 'hello'], active: 0 });
});

test('媒体卡住十五秒会释放播放权，播放承诺失败也不会堵塞后续声音', async ({ page }) => {
  await setup(page);
  await page.clock.install();
  await play(page, 'card_3');
  await play(page, 'tile_p1');
  await page.clock.fastForward(15_001);
  expect(await state(page)).toEqual({ played: ['card_3'], active: 0 });
  await page.evaluate(() => { (window as any).__voiceQueueTrace.rejectNext = true; });
  await play(page, 'hello');
  expect(await state(page)).toEqual({ played: ['card_3', 'hello'], active: 0 });
  await play(page, 'thanks');
  expect(await state(page)).toEqual({ played: ['card_3', 'hello', 'thanks'], active: 1 });
});
