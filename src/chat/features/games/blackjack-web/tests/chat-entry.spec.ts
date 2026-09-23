import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';

async function expectChat(page: Page) {
  const button = page.getByRole('button', { name: '打开牌桌聊天', exact: true });
  await expect(button).toHaveText('聊天');
  await expect(button).toBeInViewport({ ratio: 1 });
  await button.click();
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  const line = page.getByRole('button', { name: '你是MM还是GG？', exact: true });
  await line.scrollIntoViewIfNeeded();
  await line.click();
  await expect(page.locator('.social-notices')).toContainText('你是MM还是GG？');
  await page.keyboard.press('Escape');
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(button).toBeFocused();
}

for (const [kind, title, count] of [
  ['texas', '德州扑克', 8], ['golden_flower', '炸金花', 5],
  ['landlord', '斗地主', 3], ['guandan', '掼蛋', 4],
  ['mahjong', '四人麻将', 4], ['sichuan_mahjong', '四人麻将', 4],
] as const) {
  test(`${kind}房间和对局直接显示聊天，旋转后仍可发送快捷语音`, async ({ page }) => {
    await page.setViewportSize({ width: 844, height: 390 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const room: any = {
      room_id: 'CHAT01', game_type: kind, host_user_id: uid, state: 'waiting', revision: 1,
      mode: 'multi', min_players: count, max_players: count,
      room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100,
      turn_timeout_seconds: 60, turn_deadline: Date.now() / 1000 + 60,
      players: Array.from({ length: count }, (_, index) => ({ user_id: index ? `bot:${index}` : uid, username: index ? `月月${index}` : '测试玩家', avatar_url: '/character/normal.webp', is_bot: !!index, is_ready: true, connected: true })),
      game: null,
    };
    let eventId = 0;
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 } });
      if (path.startsWith('/api/game-social/')) {
        const events = route.request().method() === 'POST' ? [{ event_id: ++eventId, kind: 'chat', item_id: 'mm_or_gg', text: '你是MM还是GG？', user_id: uid, username: '测试玩家', timestamp: Date.now() / 1000 }] : [];
        return route.fulfill({ json: { success: true, cursor: eventId, events } });
      }
      return route.fulfill({ json: { success: true, room, viewer_balance: 2000 } });
    });
    await page.goto(`/?dev_user_id=${uid}`);
    await page.getByRole('button', { name: new RegExp(`^${title}`) }).click();
    if (kind === 'sichuan_mahjong') await page.getByRole('button', { name: '四川血战', exact: true }).click();
    await page.getByRole('button', { name: '好友同桌', exact: true }).click();
    await expectChat(page);
    room.state = 'playing'; room.revision++;
    room.game = { phase: 'playing', finished: false, current_player_id: uid, legal_actions: [], winners: [], message: '等待操作', community_cards: [], players: room.players.map((p: any) => ({ user_id: p.user_id, hand: [], hand_count: 0, stack: 100, score: 0, discards: [] })) };
    await page.getByRole('button', { name: '同步', exact: true }).click();
    await page.evaluate(async () => {
      history.replaceState({}, '', `${location.pathname}?frame_id=chat-test`);
      const viewport = await import('/src/activityViewport.ts' as string);
      viewport.updateActivityViewport();
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expectChat(page);
    await page.getByRole('button', { name: '战绩与声音', exact: true }).click();
    await page.getByRole('dialog', { name: '战绩与声音' }).getByRole('button', { name: '玩法规则', exact: true }).click();
    await expect(page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: /规则/ }) })).toBeVisible();
  });
}

test('单人21点直接显示聊天并可使用新增语音', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/**', route => route.fulfill({ json: new URL(route.request().url()).pathname === '/api/profile'
    ? { success: true, user_id: uid, username: '测试玩家', avatar_url: '', balance: 2000 }
    : { success: true, game: null } }));
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: '21点 立即游玩' }).click();
  await page.getByRole('button', { name: /^单人对战/ }).click();
  await expectChat(page);
});
