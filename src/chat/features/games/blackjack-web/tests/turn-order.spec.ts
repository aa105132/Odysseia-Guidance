import { expect, test } from '@playwright/test';

for (const spec of [
  { type: 'landlord', title: '斗地主', count: 3, directions: ['south', 'east', 'west'] },
  { type: 'mahjong', title: '四人麻将', count: 4, directions: ['south', 'east', 'north', 'west'] },
]) {
  for (let self = 0; self < spec.count; self++) {
    test(`${spec.title}第${self + 1}席视角逆时针完整轮转与出牌归属`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.setViewportSize({ width: self % 2 ? 568 : 844, height: self % 2 ? 320 : 390 });
      const ids = Array.from({ length: spec.count }, (_, i) => `12345678901234567${i}`);
      const uid = ids[self]!;
      let current = self;
      let revision = 1;
      const room = () => ({
        room_id: 'ORDER1', game_type: spec.type, host_user_id: ids[0], state: 'playing',
        revision, round_number: 2, mode: 'multi', min_players: spec.count, max_players: spec.count,
        room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
        settlement_status: 'reserved', turn_deadline: Math.floor(Date.now() / 1000) + 60,
        players: ids.map((id, i) => ({ user_id: id, username: `玩家${i + 1}`, avatar_url: '/character/normal.webp', is_bot: false, is_ready: true, connected: true })),
        game: {
          phase: 'playing', finished: false, current_player_id: ids[current], winners: [], legal_actions: [], message: '逆时针轮转验收',
          bottom_cards: ['ClubA', 'Heart2', 'JokerBig'],
          seat_actions: Object.fromEntries(ids.map((id, i) => [id, { action: 'play', cards: [`Club${i + 3}`], label: `玩家${i + 1}出牌` }])),
          players: ids.map((id, i) => ({ user_id: id, hand: id === uid ? (spec.type === 'mahjong' ? ['m1', 'm2'] : ['Heart3', 'Heart4']) : [], hand_count: 2, stack: 100, discards: [`m${i + 1}`] })),
        },
      });
      await page.route('**/api/**', route => {
        if (new URL(route.request().url()).pathname === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: `玩家${self + 1}`, avatar_url: '/character/normal.webp', balance: 2000 } });
        return route.fulfill({ json: { success: true, room: room(), viewer_balance: 1900 } });
      });
      await page.goto(`/?dev_user_id=${uid}`);
      await page.getByRole('button', { name: new RegExp(`^${spec.title}`) }).click();
      await page.getByRole('button', { name: /^好友同桌/ }).click();
      await expect(page.locator('.tg-seat')).toHaveCount(spec.count);
      for (let offset = 0; offset <= spec.count; offset++) {
        current = (self + offset) % spec.count;
        revision++;
        await page.getByRole('button', { name: '同步', exact: true }).click();
        const direction = spec.directions[offset % spec.count]!;
        const active = page.locator('.tg-current');
        await expect(active).toContainText(`玩家${current + 1}`);
        await expect(active).toHaveAttribute('data-position', direction);
        const avatar = (await active.locator('.tg-avatar').boundingBox())!;
        const width = page.viewportSize()!.width;
        if (direction === 'east') expect(avatar.x + avatar.width / 2).toBeGreaterThan(width * .75);
        if (direction === 'west') expect(avatar.x + avatar.width / 2).toBeLessThan(width * .25);
        if (direction === 'north') expect(avatar.y).toBeLessThan((await page.locator('.tg-self .tg-avatar').boundingBox())!.y);
        if (spec.type === 'landlord') {
          const play = page.getByLabel(`玩家${current + 1}的出牌`, { exact: true });
          await expect(play).toHaveClass(new RegExp(`tg-play-${direction === 'south' ? 'self' : direction}`));
          await expect(play.locator('img')).toHaveAttribute('src', new RegExp(`Club${current + 3}\\.`));
          const bounds = (await play.boundingBox())!;
          if (direction === 'east') expect(bounds.x).toBeGreaterThan(width / 2);
          if (direction === 'west') expect(bounds.x + bounds.width).toBeLessThan(width / 2);
        } else {
          const river = page.locator(`.tg-river-${direction}`);
          await expect(river.locator('small')).toHaveText(current === self ? '你' : `玩家${current + 1}`);
          await expect(river.locator('img')).toHaveAttribute('src', `/mahjong/faces/m${current + 1}.svg`);
        }
      }
      await page.reload();
      await page.getByRole('button', { name: new RegExp(`^${spec.title}`) }).click();
      await expect(page.locator('.tg-current')).toHaveAttribute('data-position', 'south');
    });
  }
}
