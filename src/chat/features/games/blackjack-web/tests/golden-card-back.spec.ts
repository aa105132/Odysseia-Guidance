import { expect, test } from '@playwright/test';

const uid = '123456789012345678';
const cards = ['Spade10', 'ClubK', 'Heart2'];

for (const viewport of [{ width: 1440, height: 900 }, { width: 844, height: 390 }, { width: 390, height: 844 }]) {
  test(`炸金花未看牌显示三张牌背，看牌后原位翻开 ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const room: any = {
      room_id: 'BACK01', game_type: 'golden_flower', host_user_id: uid, state: 'playing', revision: 1,
      mode: 'solo', min_players: 2, max_players: 5, room_tier: 'beginner', base_stake: 1,
      entry_min: 100, loss_limit: 100, settlement_status: 'reserved', turn_deadline: Date.now() / 1000 + 60,
      players: [uid, 'bot:1', 'bot:2', 'bot:3', 'bot:4'].map((id, index) => ({ user_id: id, username: index ? `月月${index}` : '测试玩家', avatar_url: '/character/normal.webp', is_bot: !!index, is_ready: true, connected: true })),
      game: {
        phase: 'betting', finished: false, current_player_id: uid, legal_actions: ['look', 'fold', 'call', 'raise', 'compare'], winners: [],
        message: '尚未看牌，可继续闷牌下注。', pot: 5, current_bet: 1, call_amount: 1, min_raise_to: 2, max_raise_to: 100, compare_cost: 2,
        players: [uid, 'bot:1', 'bot:2', 'bot:3', 'bot:4'].map(id => ({ user_id: id, hand: [], hand_count: 3, stack: 99, looked: false })),
      },
    };
    let looks = 0;
    const faceRequests: string[] = [];
    page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/cards/')) faceRequests.push(request.url()); });
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 } });
      if (path.startsWith('/api/game-social/')) return route.fulfill({ json: { cursor: 0, events: [] } });
      if (path.endsWith('/action')) {
        expect(route.request().postDataJSON().action).toBe('look');
        looks++;
        Object.assign(room.game.players[0], { hand: [...cards], looked: true });
        room.game.legal_actions = ['fold', 'call', 'raise', 'compare'];
        room.game.message = '已看牌，跟注费用翻倍。'; room.game.call_amount = 2; room.revision++;
      }
      return route.fulfill({ json: { success: true, room, viewer_balance: 1900 } });
    });
    await page.goto(`/?dev_user_id=${uid}`);
    await page.getByRole('button', { name: /^炸金花/ }).click();
    await page.getByRole('button', { name: /^月月陪玩/ }).click();
    await page.evaluate(async () => {
      history.replaceState({}, '', `${location.pathname}?frame_id=golden-back-test`);
      const viewport = await import('/src/activityViewport.ts' as string);
      viewport.updateActivityViewport();
      const rail = document.createElement('div');
      rail.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:64px;z-index:99999;background:#555';
      document.body.append(rail);
    });
    const hand = page.locator('.tg-hand-shelf .tg-my-hand');
    const images = hand.locator('img');
    await expect(images).toHaveCount(3);
    const before = [];
    for (const [index, image] of (await images.all()).entries()) {
      await expect(image).toHaveAttribute('src', '/table-assets/card-back.png');
      await expect(image).toHaveAttribute('alt', `未看牌，第${index + 1}张`);
      await expect(image).toBeInViewport({ ratio: 1 });
      await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
      const box = (await image.boundingBox())!;
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width - 64 + 1);
      expect(Math.max(box.width, box.height), '牌背保留与正面一致的可读尺寸').toBeGreaterThan(55);
      before.push(box);
    }
    expect(faceRequests, '看牌前不请求任何真实牌面图片').toEqual([]);
    expect(await hand.innerHTML(), '看牌前的手牌 DOM 不携带暗牌值').not.toMatch(/Spade10|ClubK|Heart2/);
    await page.screenshot({ path: `../../../../../tmp/golden-card-back-${viewport.width}.png` });
    await page.getByRole('button', { name: '看牌', exact: true }).click();
    await expect(images).toHaveCount(3);
    expect(looks).toBe(1);
    for (const [index, image] of (await images.all()).entries()) {
      await expect(image).toHaveAttribute('src', `/cards/${cards[index]}.webp`);
      const after = (await image.boundingBox())!;
      for (const key of ['x', 'y', 'width', 'height'] as const) expect(after[key], `看牌前后第${index + 1}张牌的${key}保持不变`).toBeCloseTo(before[index]![key], 0);
    }
    await page.screenshot({ path: `../../../../../tmp/golden-card-face-${viewport.width}.png` });
    await expect(page.locator('.tg-hidden-hand')).toHaveCount(4);
    await expect(page.locator('.tg-opponent-hand')).toHaveCount(0);
  });
}
