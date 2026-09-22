import { expect, test, type Page } from '@playwright/test';

const ids = ['123456789012345678', '223456789012345678', '323456789012345678', '423456789012345678'];
const hand = ['Heart2#0', 'Heart2#1', 'Club2#0', 'Club3#0', 'Club3#1', 'Diamond3#0', 'Heart4#0', 'Spade4#0', 'Club5#0', 'Club6#0', 'Heart7#0', 'Spade7#0', 'Club8#0', 'Diamond8#0', 'Club9#0', 'Club10#0', 'Heart10#0', 'Spade10#1', 'ClubJ#0', 'DiamondJ#0', 'HeartQ#0', 'SpadeQ#0', 'ClubK#0', 'HeartA#0', 'SpadeA#0', 'JokerSmall#0', 'JokerBig#1'];

async function setup(page: Page, viewport: {width: number; height: number}, finished = false) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const requests: Array<Record<string, unknown>> = [];
  const room = {
    room_id: 'GUANDAN', game_type: 'guandan', host_user_id: ids[0], state: finished ? 'finished' : 'playing', revision: 1, round_number: 1,
    last_public_action: {id: '1:0:1', user_id: ids[1], action: 'pass', bid: 0},
    mode: 'multi', include_yueyue: false, min_players: 4, max_players: 4,
    room_tier: 'beginner', base_stake: 1, entry_min: 100, loss_limit: 100, turn_timeout_seconds: 60,
    turn_deadline: Date.now() / 1000 + 60, buy_in: 100, settlement_status: finished ? 'settled' : 'reserved',
    actual_settlement: finished ? {[ids[0]!]: 3, [ids[1]!]: -3, [ids[2]!]: 3, [ids[3]!]: -3} : {},
    players: ids.map((id, index) => ({ user_id: id, username: ['测试玩家', '下家', '对家队友', '上家'][index], avatar_url: '', is_bot: index === 1, is_ready: false, connected: true })),
    game: {
      phase: finished ? 'finished' : 'playing', finished, current_player_id: finished ? null : ids[0],
      message: finished ? '一队双上，升3级' : '轮到你出牌', winners: finished ? [ids[0], ids[2]] : [],
      players: ids.map((id, index) => ({user_id: id, hand: index === 0 ? hand : [], hand_count: index === 0 ? 27 : 23, team: index % 2, stack: 100, score_delta: 0, finished_rank: finished ? [1, 3, 2, 4][index] : null})),
      legal_actions: finished ? [] : ['play', 'pass'], level: '2', team_levels: finished ? ['5', '2'] : ['2', '2'],
      match_finished: false, match_winner_team: null, level_gain: finished ? 3 : 0,
      finish_order: finished ? [ids[0], ids[2], ids[1], ids[3]] : [],
      tribute_events: [{ kind: 'tribute', user_id: ids[1], target_id: ids[0], card: 'JokerBig#0', automatic: true }],
      last_play: { user_id: ids[3], cards: ['Club4#1'], name: '单张', kind: 'single' },
      play_options: [
        { cards: ['Heart2#0', 'Heart2#1'], combo: 'pair:2', kind: 'pair', name: '对子', rank: 15, size: 2 },
        { cards: ['Club5#0'], combo: 'single:5', kind: 'single', name: '单张', rank: 5, size: 1 },
      ],
    },
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/profile') return route.fulfill({json:{success:true,user_id:ids[0],username:'测试玩家',avatar_url:'',balance:2000}});
    if (path.startsWith('/api/game-social/')) return route.fulfill({json:{success:true,events:[],cursor:0,catalog:{chat:[],interaction:[]}}});
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      requests.push(body);
      room.revision++;
    }
    return route.fulfill({json:{success:true,room,viewer_balance:2000}});
  });
  await page.goto(`/?dev_user_id=${ids[0]}`);
  await page.getByRole('button', {name: /^掼蛋/}).click();
  await page.getByRole('button', {name:'好友同桌',exact:true}).click();
  await expect(page.locator('.game-guandan.has-room')).toBeVisible();
  return {room,requests};
}

for (const viewport of [{width:568,height:320},{width:844,height:390},{width:1440,height:900}]) {
  test(`掼蛋27张双行手牌可读可选 ${viewport.width}`, async ({page}) => {
    const {requests} = await setup(page,viewport);
    const cards = page.locator('.tg-guandan-hand .tg-hand-card');
    await expect(cards).toHaveCount(27);
    await expect(page.getByLabel('掼蛋级牌与队伍')).toContainText('本局打 2');
    await expect(page.locator('.tg-guandan-team').filter({hasText:'队友'})).toHaveCount(1);
    await expect.poll(() => cards.locator('img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    const boxes = await cards.locator('img').evaluateAll(images => images.map(image => { const r=image.getBoundingClientRect(); return {width:r.width,height:r.height,left:r.left,right:r.right,top:r.top,bottom:r.bottom}; }));
    for (const box of boxes) {
      expect(box.height).toBeGreaterThanOrEqual(50);
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(viewport.width);
      expect(box.bottom).toBeLessThanOrEqual(viewport.height);
    }
    expect(new Set(boxes.map(box=>Math.round(box.top))).size).toBe(2);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    // 所有牌的独立点击区域可达，不能被邻牌、工具栏或聊天按钮盖住。
    for (let index = 0; index < 27; index++) await cards.nth(index).click({trial:true});
    await page.getByRole('button',{name:'提示',exact:true}).click();
    await expect(page.locator('.tg-guandan-hand [aria-pressed="true"]')).toHaveCount(2);
    await page.getByRole('button',{name:'出牌',exact:true}).click();
    expect(requests.at(-1)).toMatchObject({action:'play', cards:['Heart2#0','Heart2#1'],combo:'pair:2'});
    await expect(page.getByRole('button',{name:'同步',exact:true})).toBeEnabled();
    await page.screenshot({path:`../../../../../tmp/guandan-${viewport.width}.png`});
  });
}

test('相同牌面有独立选择，提示可循环，手选不沿用旧combo', async ({page}) => {
  const {requests} = await setup(page,{width:844,height:390});
  await page.getByRole('button',{name:'提示',exact:true}).click();
  await page.getByRole('button',{name:'提示',exact:true}).click();
  await expect(page.locator('[data-unique-card="Club5#0"]')).toHaveAttribute('aria-pressed','true');
  await page.getByRole('button',{name:'清空选择',exact:true}).click();
  await page.locator('[data-unique-card="Heart2#1"]').click();
  await expect(page.locator('[data-unique-card="Heart2#0"]')).toHaveAttribute('aria-pressed','false');
  await page.getByRole('button',{name:'出牌',exact:true}).click();
  expect(requests.at(-1)).toMatchObject({cards:['Heart2#1']});
  expect(requests.at(-1)).not.toHaveProperty('combo');
});

test('月月公开动作只播一次，减少动画时仍有结算音乐', async ({page}) => {
  await page.addInitScript(() => {
    localStorage.setItem('yueyue:music', 'true');
    const played: string[] = [];
    const seen = new WeakSet<HTMLMediaElement>();
    (window as unknown as {__played: string[]}).__played = played;
    HTMLMediaElement.prototype.play = function () {
      if (!seen.has(this)) { seen.add(this); played.push(this.src); }
      return Promise.resolve();
    };
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  const {room} = await setup(page,{width:844,height:390});
  const voiceCount = () => page.evaluate(() => (window as unknown as {__played:string[]}).__played.filter(src=>src.includes('/audio/voice/')).length);
  expect(await voiceCount()).toBe(0);
  room.revision++;
  room.last_public_action = {id:'1:0:2',user_id:ids[1],action:'bid',bid:0};
  await page.getByRole('button',{name:'同步',exact:true}).click();
  await expect.poll(voiceCount).toBe(1);
  expect(await page.evaluate(()=>(window as unknown as {__played:string[]}).__played.at(-1))).toContain('/audio/voice/no_bid.mp3');
  await page.getByRole('button',{name:'同步',exact:true}).click();
  expect(await voiceCount()).toBe(1);
  room.revision++;
  room.last_public_action = {id:'1:0:3',user_id:ids[1],action:'play',bid:0};
  room.game.last_play = {user_id:ids[1],cards:['Club4#1'],name:'炸弹',kind:'bomb'};
  await page.getByRole('button',{name:'同步',exact:true}).click();
  await expect.poll(voiceCount).toBe(2);
  expect(await page.evaluate(()=>(window as unknown as {__played:string[]}).__played.at(-1))).toContain('/audio/voice/bomb.mp3');
  room.revision++;
  room.state = 'finished'; room.game.finished = true; room.game.phase = 'finished';
  room.game.winners = [ids[0],ids[2]]; room.game.legal_actions = [];
  room.actual_settlement = {[ids[0]!]:3}; room.settlement_status = 'settled';
  await page.getByRole('button',{name:'同步',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {__played:string[]}).__played.filter(src=>src.endsWith('/audio/music/win.mp3')).length)).toBe(1);
  await page.getByRole('button',{name:'同步',exact:true}).click();
  expect(await page.evaluate(()=>(window as unknown as {__played:string[]}).__played.filter(src=>src.endsWith('/audio/music/win.mp3')).length)).toBe(1);
});

test('结算显示名次升级续局，自动贡还贡可以查看', async ({page}) => {
  await setup(page,{width:1440,height:900},true);
  await expect(page.locator('.tg-guandan-match')).toContainText('胜方升 3 级');
  await expect(page.locator('.tg-guandan-team').first()).toContainText('第1名');
  await page.locator('.tg-guandan-tribute summary').click();
  await expect(page.locator('.tg-guandan-tribute')).toContainText('下家 → 测试玩家 进贡 大王');
  await expect(page.getByRole('button',{name:'准备下一局',exact:true})).toBeVisible();
  await page.screenshot({path:'../../../../../tmp/guandan-finished-1440.png'});
  await page.setViewportSize({width:568,height:320});
  await page.getByRole('button',{name:'准备下一局',exact:true}).click({trial:true});
  const resultBox = (await page.locator('.tg-round-result').boundingBox())!;
  for (const card of await page.locator('.tg-guandan-hand img').all()) {
    const box = (await card.boundingBox())!;
    expect(resultBox.x + resultBox.width <= box.x || box.x + box.width <= resultBox.x || resultBox.y + resultBox.height <= box.y || box.y + box.height <= resultBox.y).toBe(true);
  }
  await page.screenshot({path:'../../../../../tmp/guandan-finished-568.png'});
});
