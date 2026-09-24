import { expect, test, type Page } from '@playwright/test';

const uid = '123456789012345678';

async function loadResolver(page: Page) {
  await page.route('**/card-voice-resolver', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body></body></html>' }));
  await page.goto('/card-voice-resolver');
}

test('单牌、王、对子、副本标记和公开牌型准确报牌，未知输入安全回退', async ({ page }) => {
  await loadResolver(page);
  const resolved = await page.evaluate(async () => {
    const { tableActionVoice: voice } = await import('/src/tableActionVoice.ts' as string);
    return [
      voice('landlord', { action: 'play', cards: ['Club3'] }),
      voice('landlord', { action: 'play', cards: ['JokerSmall'] }),
      voice('guandan', { action: 'play', cards: ['JokerBig#1'] }),
      voice('guandan', { action: 'play', cards: ['ClubQ#0', 'ClubQ#1'], kind: 'pair' }),
      voice('guandan', { action: 'play', cards: ['Heart2#0', 'ClubK#1'], kind: 'pair' }),
      voice('landlord', { action: 'play', cards: ['Club3', 'Heart3', 'Spade3', 'Club4', 'Heart4', 'Spade4'] }),
      voice('guandan', { action: 'play', cards: ['Club3#0', 'Club3#1', 'Heart3#0', 'Club4#0', 'Club4#1', 'Heart4#0'], combo: 'triple_straight:4:6:' }),
      voice('guandan', { action: 'play', cards: ['JokerSmall#0', 'JokerSmall#1', 'JokerBig#0', 'JokerBig#1'], kind: 'rocket' }),
      voice('landlord', { action: 'play', cards: ['Hidden'] }),
      voice('guandan', { action: 'play', cards: ['Club3#99'] }),
      voice('landlord', { action: 'play', cards: ['Club3', 'Club3'] }),
      voice('landlord', { action: 'play', cards: ['Club3', 4] }),
      voice('landlord', { action: 'pass', user_id: 'bot' }, { last_play: { user_id: 'bot', cards: ['Club3'], kind: 'bomb' } }),
      voice('guandan', { action: 'play', user_id: 'bot', cards: ['Heart2#0', 'Club4#1'] }, { last_play: { user_id: 'bot', cards: ['Club3#0', 'Heart3#1', 'Club3#1', 'Heart3#0'], kind: 'bomb' } }),
      voice('landlord', { action: 'unknown' }),
    ];
  });
  expect(resolved).toEqual(['card_3', 'card_joker_small', 'card_joker_big', 'pair_q', 'cards_pair', 'cards_airplane', 'cards_steel_plate', 'cards_four_jokers', 'play', 'play', 'play', 'play', 'pass', 'cards_pair', null]);
});

test('34张麻将牌名齐全，字牌与自摸按公开事件播报，新增目录无重复', async ({ page }) => {
  await loadResolver(page);
  const result = await page.evaluate(async () => {
    const { tableActionVoice: voice } = await import('/src/tableActionVoice.ts' as string);
    const lines = (await import('/src/cardVoiceLines.json' as string)).default;
    const { gameVoiceIds } = await import('/src/gameVoiceLines.ts' as string);
    const tiles = ['m', 'p', 's'].flatMap(suit => Array.from({ length: 9 }, (_, i) => `${suit}${i + 1}`)).concat(Array.from({ length: 7 }, (_, i) => `z${i + 1}`));
    return {
      tiles: tiles.map(tile => voice('mahjong', { action: 'discard', tile })),
      extras: [voice('mahjong', { action: 'discard', tile: 'p1' }), voice('mahjong', { action: 'discard', tile: 'z1' }), voice('mahjong', { action: 'discard', tile: 'z8' }), voice('sichuan_mahjong', { action: 'win', user_id: 'bot' }, { win_events: [{ user_id: 'bot', kind: 'self_draw' }] }), voice('mahjong', { action: 'chow' })],
      count: lines.length, unique: new Set(lines.map((line: any) => line.id)).size,
      valid: lines.every((line: any) => /^[a-z0-9_]+$/.test(line.id) && gameVoiceIds.has(line.id) && line.text),
    };
  });
  expect(result.tiles).toHaveLength(34);
  expect(result.tiles.every(id => id?.startsWith('tile_'))).toBe(true);
  expect(result.extras).toEqual(['tile_p1', 'tile_z1', 'discard', 'self_draw', 'chow']);
  expect(result.count).toBe(result.unique);
  expect(result.count).toBe(81);
  expect(result.valid).toBe(true);
});

function makeRoom(kind = 'landlord') {
  const members = [uid, 'bot:1', 'bot:2', ...(kind === 'landlord' ? [] : ['bot:3'])];
  return {
    room_id: 'VOICE01', game_type: kind, host_user_id: uid, state: 'playing', revision: 1, round_number: 1,
    mode: 'solo', min_players: members.length, max_players: members.length, room_tier: 'beginner',
    base_stake: 1, entry_min: 100, loss_limit: 100, turn_deadline: Date.now() / 1000 + 60,
    players: members.map((id, i) => ({ user_id: id, username: i ? `月月${i}` : '我', avatar_url: '/character/normal.webp', is_bot: !!i, is_ready: true, connected: true })),
    last_public_action: { id: '1:1:1', user_id: 'bot:1', action: 'play', cards: ['Club4'] } as any,
    game: {
      phase: 'playing', finished: false, current_player_id: uid,
      legal_actions: kind.includes('mahjong') ? ['discard'] : ['play', 'pass'],
      winners: [], message: '轮到你操作', level: '2', team_levels: ['2', '2'], play_options: [] as any[],
      last_play: { user_id: 'bot:1', cards: ['Club4'], kind: 'single' } as any,
      players: members.map((id, i) => ({ user_id: id, hand: i ? [] : kind.includes('mahjong') ? ['p1', 'z1', 's9'] : kind === 'guandan' ? ['Club3#0', 'Club3#1', 'Heart2#0'] : ['Club3', 'Diamond3', 'Club5'], hand_count: 3, score: 0, team: i % 2, discards: [] })),
    },
  };
}

async function mountTable(page: Page, room = makeRoom()) {
  const state = { fail: false, replyBot: true, posts: [] as any[] };
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:sound', 'true'); localStorage.setItem('yueyue:voice', 'true'); localStorage.setItem('yueyue:music', 'false');
    (window as any).__cardAudio = [];
    (window as any).Audio = class {
      src: string; original: string; paused = true; volume = 1; loop = false;
      onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = this.original = src; }
      play() { this.paused = false; (window as any).__cardAudio.push(this); return Promise.resolve(); }
      pause() { this.paused = true; }
      removeAttribute() { this.src = ''; }
      load() {}
    };
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({ json: { success: true, user_id: uid, username: '我', avatar_url: '', balance: 2000 } });
    if (path === '/api/tables/action') {
      const body = route.request().postDataJSON(); state.posts.push(body);
      if (state.fail) return route.fulfill({ status: 400, json: { detail: '这次出牌失败' } });
      room.revision++;
      room.last_public_action = state.replyBot ? { id: `1:1:${room.revision}`, user_id: 'bot:1', action: 'play', cards: ['Club4'] }
        : { ...body, id: `1:1:${room.revision}`, user_id: uid };
      room.game.last_play = state.replyBot ? { user_id: 'bot:1', cards: ['Club4'], kind: 'single' }
        : { user_id: uid, cards: body.cards, kind: 'single' };
    }
    return route.fulfill({ json: { success: true, room, viewer_balance: 2000, events: [], cursor: 0 } });
  });
  await page.goto(`/?dev_user_id=${uid}`);
  const name = room.game_type === 'landlord' ? /^斗地主/ : room.game_type === 'guandan' ? /^掼蛋/ : /^四人麻将/;
  await page.getByRole('button', { name }).click();
  await page.getByRole('button', { name: /^月月陪玩/ }).click();
  await expect(page.locator('.tg-my-hand .tg-hand-card')).toHaveCount(3);
  return state;
}

const voiceIds = (page: Page) => page.evaluate(() => (window as any).__cardAudio.filter((audio: any) => audio.original.includes('/voice/')).map((audio: any) => new URL(audio.original, location.origin).pathname.split('/').pop().replace('.mp3', '')));
const finishVoice = (page: Page) => page.evaluate(() => (window as any).__cardAudio.findLast((audio: any) => !audio.paused && audio.original.includes('/voice/'))?.onended?.());

test('出牌成功先报本人牌名，再报立即响应的机器人，初次进入与重复轮询不重播', async ({ page }) => {
  await mountTable(page);
  expect(await voiceIds(page)).toEqual([]);
  await page.locator('.tg-my-hand .tg-hand-card').first().click();
  await page.getByRole('button', { name: '出牌', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['card_3']);
  await finishVoice(page);
  await expect.poll(() => voiceIds(page)).toEqual(['card_3', 'card_4']);
  await finishVoice(page);
  await page.getByRole('button', { name: '同步', exact: true }).click();
  expect(await voiceIds(page)).toEqual(['card_3', 'card_4']);
});

test('失败请求不报牌；后续机器人公开新事件按实际牌型播报', async ({ page }) => {
  const room = makeRoom();
  const state = await mountTable(page, room);
  state.fail = true;
  await page.locator('.tg-my-hand .tg-hand-card').first().click();
  await page.getByRole('button', { name: '出牌', exact: true }).click();
  await expect(page.locator('.tg-error')).toContainText('这次出牌失败');
  expect(await voiceIds(page)).toEqual([]);
  room.revision++;
  room.last_public_action = { id: '1:1:9', user_id: 'bot:1', action: 'play', cards: ['Club3', 'Heart3', 'Spade3', 'Club4', 'Heart4', 'Spade4'] };
  room.game.last_play = { ...room.last_public_action, kind: 'airplane' };
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['cards_airplane']);
  await finishVoice(page);
  room.revision++;
  room.last_public_action = { id: '1:1:10', user_id: 'bot:1', action: 'pass' };
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['cards_airplane', 'pass']);
});

test('麻将本人打筒和机器人打东风均报具体牌名，沿用独立语音音量与静音', async ({ page }) => {
  const room = makeRoom('mahjong');
  const state = await mountTable(page, room); state.replyBot = false;
  await page.evaluate(async () => (await import('/src/gameAudio.ts' as string)).setVoiceVolume(.42));
  await page.locator('.tg-my-hand .tg-hand-card').first().click();
  await page.getByRole('button', { name: '弃选中的牌', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['tile_p1']);
  expect(await page.evaluate(() => (window as any).__cardAudio.findLast((audio: any) => !audio.paused)?.volume)).toBe(.42);
  await finishVoice(page);
  room.revision++; room.last_public_action = { id: '1:1:8', user_id: 'bot:1', action: 'discard', tile: 'z1' };
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['tile_p1', 'tile_z1']);
  await page.evaluate(async () => (await import('/src/gameAudio.ts' as string)).setVoiceEnabled(false));
  room.revision++; room.last_public_action = { id: '1:1:9', user_id: 'bot:1', action: 'discard', tile: 's9' };
  await page.getByRole('button', { name: '同步', exact: true }).click();
  expect(await voiceIds(page)).toEqual(['tile_p1', 'tile_z1']);
});

test('排队报牌可被静音清空，切出房间不残留语音', async ({ page }) => {
  await mountTable(page);
  await page.locator('.tg-my-hand .tg-hand-card').first().click();
  await page.getByRole('button', { name: '出牌', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['card_3']);
  await page.evaluate(async () => {
    const audio = await import('/src/gameAudio.ts' as string);
    audio.setVoiceVolume(0); audio.setVoiceVolume(.7);
    await audio.playGameVoice('card_5', { enqueue: true });
  });
  await finishVoice(page);
  expect(await voiceIds(page)).toEqual(['card_3', 'card_5']);
  await page.evaluate(async () => {
    const audio = await import('/src/gameAudio.ts' as string);
    await audio.playGameVoice('card_6', { enqueue: true });
    await audio.playGameVoice('card_7', { enqueue: true });
    audio.stopGameVoice();
  });
  expect(await page.evaluate(() => (window as any).__cardAudio.filter((audio: any) => !audio.paused && audio.original.includes('/voice/')).length)).toBe(0);
  expect(await voiceIds(page)).not.toContain('card_7');
});

test('新局清空上一局报牌，下一条实时动作仍可播放', async ({ page }) => {
  const room = makeRoom();
  await mountTable(page, room);
  await page.locator('.tg-my-hand .tg-hand-card').first().click();
  await page.getByRole('button', { name: '出牌', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['card_3']);
  room.revision++; room.round_number++;
  room.last_public_action = null;
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__cardAudio.filter((audio: any) => !audio.paused && audio.original.includes('/voice/')).length)).toBe(0);
  room.revision++;
  room.last_public_action = { id: '2:1:1', user_id: 'bot:1', action: 'play', cards: ['JokerBig'] };
  room.game.last_play = { ...room.last_public_action, kind: 'single' };
  await page.getByRole('button', { name: '同步', exact: true }).click();
  await expect.poll(() => voiceIds(page)).toEqual(['card_3', 'card_joker_big']);
  await finishVoice(page);
  expect(await voiceIds(page)).not.toContain('card_4');
});
