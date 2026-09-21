import { expect, test } from '@playwright/test';
import { captureFinalScreenshot } from './animation-helpers';

for (const [gameType, playerCount] of [['texas', 2], ['texas', 8], ['golden_flower', 2], ['golden_flower', 5]] as const) {
  test(`${gameType}${playerCount}人连续拉伸窗口时牌桌铺满且信息互不重叠`, async ({ page }) => {
    test.setTimeout(30_000);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const uid = '123456789012345678';
    const ids = Array.from({ length: playerCount }, (_, i) => i ? `bot:${i}` : uid);
    const room = {
      room_id: 'LAYOUT', game_type: gameType, host_user_id: uid, state: 'playing', revision: 1,
      mode: 'solo', min_players: 2, max_players: gameType === 'texas' ? 8 : 5,
      room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
      settlement_status: 'reserved', turn_deadline: Math.floor(Date.now() / 1000) + 60,
      players: ids.map((id, index) => ({ user_id: id, username: index ? `月月${index}` : 'QAGuest名字较长的玩家', avatar_url: '/character/normal.webp', is_bot: !!index, is_ready: true, connected: true })),
      game: {
        phase: 'preflop', finished: false, current_player_id: uid, legal_actions: gameType === 'texas' ? ['fold', 'call', 'raise', 'all_in'] : ['look', 'fold', 'call', 'raise', 'compare'], winners: [],
        message: '已下小盲 1、大盲 2；翻牌前下注。', pot: 3, current_bet: 2, call_amount: 1,
        min_raise_to: 4, max_raise_to: 100, compare_cost: 2, community_cards: ['Club2', 'Heart7', 'DiamondQ', 'SpadeA', 'ClubK'],
        players: ids.map((id, index) => ({ user_id: id, hand: index ? [] : gameType === 'texas' ? ['Spade10', 'ClubK'] : ['Spade10', 'ClubK', 'Heart2'], hand_count: gameType === 'texas' ? 2 : 3, stack: 99 - index })),
      },
    };
    await page.route('**/api/**', route => route.fulfill({ json: new URL(route.request().url()).pathname === '/api/profile'
      ? { success: true, user_id: uid, username: 'QAGuest名字较长的玩家', avatar_url: '/character/normal.webp', balance: 20000 }
      : { success: true, room, viewer_balance: 19900 } }));
    await page.goto(`/?dev_user_id=${uid}`);
    await page.getByRole('button', { name: gameType === 'texas' ? /^德州扑克/ : /^炸金花/ }).click();
    await page.getByRole('button', { name: /^月月陪玩/ }).click();
    for (const viewport of [{ width: 1188, height: 1196 }, { width: 876, height: 1158 }, { width: 1440, height: 900 }, { width: 2560, height: 600 }, { width: 1024, height: 480 }, { width: 844, height: 390 }, { width: 667, height: 375 }, { width: 568, height: 320 }, { width: 700, height: 320 }, { width: 700, height: 800 }]) {
      await page.setViewportSize(viewport);
      const stage = (await page.locator('.game-viewport-stage').boundingBox())!;
      expect(stage.width, '牌桌铺满窗口宽度，不出现两侧留边').toBeCloseTo(viewport.width, 0);
      expect(stage.height, '牌桌铺满窗口高度，不出现上下留边').toBeCloseTo(viewport.height, 0);
      expect(Math.abs(stage.x + stage.width / 2 - viewport.width / 2)).toBeLessThan(1);
      expect(Math.abs(stage.y + stage.height / 2 - viewport.height / 2)).toBeLessThan(1);
      const status = (await page.locator('.tg-poker-status').boundingBox())!;
      const pot = (await page.locator('.tg-pot').boundingBox())!;
      expect(status.y + status.height, '提示行必须在底池上方').toBeLessThanOrEqual(pot.y);
      const overlaps = await page.evaluate(() => {
        const targets = [...document.querySelectorAll('.tg-notice, .tg-turn, .tg-pot, .tg-center > .tg-public-cards, .tg-center > .tg-table-caption')].filter(e => e.getClientRects().length);
        const blockers = [...document.querySelectorAll('.tg-player, .tg-hidden-hand > *, .tg-my-hand, .tg-control-panel button, .tg-control-panel .tg-field, .tg-control-panel > .tg-muted')];
        return targets.flatMap((a, i) => [...targets.slice(i + 1), ...blockers].filter(b => {
          const x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
          return Math.min(x.right, y.right) - Math.max(x.left, y.left) > 1 && Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top) > 1;
        }).map(b => `${a.className} / ${b.className}`));
      });
      expect(overlaps, `${viewport.width}×${viewport.height}不能发生碰撞`).toEqual([]);
      for (const control of await page.locator('.tg-toolbar button,.tg-control-panel button,.tg-control-panel input,.tg-control-panel select').all()) {
        await expect(control).toBeInViewport({ ratio: 1 });
        const bounds = (await control.boundingBox())!;
        expect(bounds.height).toBeGreaterThanOrEqual(35.5);
        expect(bounds.x).toBeGreaterThanOrEqual(stage.x - 1);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(stage.x + stage.width + 1);
        expect(bounds.y + bounds.height).toBeLessThanOrEqual(stage.y + stage.height + 1);
        if (await control.isEnabled()) await control.click({ trial: true });
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight)).toBe(true);
      if ([1188, 568, 2560].includes(viewport.width)) await captureFinalScreenshot(page, `status-layout-${gameType}-${playerCount}-${viewport.width}x${viewport.height}.png`);
    }
  });
}
