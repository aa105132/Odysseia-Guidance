import { expect, test } from '@playwright/test';

test('德州2倍4倍仅填值，按当前桌注与合法上下限计算，点击加注才提交', async ({ page }) => {
  await page.addInitScript(() => {
    const Base = window.AudioContext;
    (window as any).__playedFrequencies = [];
    window.AudioContext = class extends Base {
      createOscillator() {
        const oscillator = super.createOscillator();
        const start = oscillator.start.bind(oscillator);
        oscillator.start = (...args) => { (window as any).__playedFrequencies.push(oscillator.frequency.value); start(...args); };
        return oscillator;
      }
    };
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 568, height: 320 });
  const uid = '123456789012345678';
  const actions: Record<string, unknown>[] = [];
  const room = {
    room_id: 'QUICK1', game_type: 'texas', host_user_id: uid, state: 'playing', revision: 1,
    mode: 'solo', min_players: 2, max_players: 8, room_tier: 'beginner', base_stake: 1,
    entry_min: 100, loss_limit: 100, settlement_status: 'reserved',
    players: [uid, 'bot:1'].map((id, i) => ({ user_id: id, username: `玩家${i}`, avatar_url: '/character/normal.webp', is_bot: !!i, is_ready: true, connected: true })),
    game: {
      phase: 'preflop', finished: false, current_player_id: uid, legal_actions: ['fold', 'call', 'raise', 'all_in'], winners: [], message: '轮到你下注',
      pot: 30, current_bet: 10, call_amount: 5, min_raise_to: 20, max_raise_to: 100,
      players: [uid, 'bot:1'].map((id, i) => ({ user_id: id, hand: i ? [] : ['Spade10', 'ClubK'], hand_count: 2, stack: 100 })),
    },
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '玩家0', avatar_url: '/character/normal.webp', balance: 1000 } });
    if (path.endsWith('/action')) actions.push(route.request().postDataJSON());
    return route.fulfill({ json: { success: true, room, viewer_balance: 900 } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  await page.getByRole('button', { name: /^德州扑克/ }).click();
  await page.getByRole('button', { name: /^月月陪玩/ }).click();
  const input = page.getByLabel('本轮加到', { exact: true });
  const twice = page.getByRole('button', { name: '填入2倍当前桌注' });
  const four = page.getByRole('button', { name: '填入4倍当前桌注' });
  const raise = page.getByRole('button', { name: '加注', exact: true });
  await twice.click(); await expect(input).toHaveValue('20');
  await four.click(); await expect(input).toHaveValue('40');
  await four.click(); await expect(input).toHaveValue('40');
  expect(actions).toEqual([]);
  await page.evaluate(() => { (window as any).__playedFrequencies = []; });
  await raise.click();
  await expect.poll(() => page.evaluate(() => (window as any).__playedFrequencies)).toEqual(expect.arrayContaining([660, 880, 1100]));
  expect(actions).toEqual([{ room_id: 'QUICK1', expected_revision: 1, action: 'raise', amount: 40 }]);
  const refresh = async () => { room.revision++; await page.getByRole('button', { name: '同步', exact: true }).click(); };
  room.game.current_bet = 0; room.game.min_raise_to = 2; await refresh();
  await twice.click(); await expect(input).toHaveValue('4');
  await four.click(); await expect(input).toHaveValue('8');
  room.game.current_bet = 10; room.game.min_raise_to = 25; await refresh();
  await twice.click(); await expect(input).toHaveValue('25');
  room.game.max_raise_to = 30; await refresh();
  await four.click(); await expect(input).toHaveValue('30');
  await expect(raise).toBeEnabled();
  room.game.max_raise_to = 18; await refresh();
  await twice.click(); await expect(input).toHaveValue('18');
  await expect(raise).toBeEnabled();
  await input.fill('999'); await expect(raise).toBeDisabled();
  await input.fill('10'); await expect(raise).toBeDisabled();
  await input.fill('11'); await expect(raise).toBeDisabled();
  expect(actions).toHaveLength(1);
  room.game.legal_actions = []; await refresh();
  await expect(twice).toHaveCount(0); await expect(four).toHaveCount(0);
});
