// 本地真实上游验收；宿主只用固定测试身份，无钱包或生产服务依赖。
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

const url = new URL(process.argv[2] || 'http://127.0.0.1:8492/?dev_user_id=123456789012345678');
if (!['127.0.0.1', 'localhost'].includes(url.hostname)) throw new Error('仅允许本地测试宿主');
url.searchParams.set('frame_id', 'classic-test');
const output = process.argv[3];
if (!output) throw new Error('请指定截图输出目录');
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const errors = [];
const missing = [];
let timer;
try {
  await Promise.race([(async () => {
    async function entry(mode, seed = false) {
      const page = await browser.newPage({ viewport: { width: 844, height: 390 }, isMobile: true, hasTouch: true });
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', response => { if (response.status() === 404 && response.url().includes('/noname/')) missing.push(new URL(response.url()).pathname); });
      await page.route('**/assets/*.js', async route => {
        const response = await route.fetch();
        const body = await response.text();
        if (body.includes('DiscordSDK') && body.includes('class DiscordSDK')) return route.fulfill({ contentType: 'application/javascript', body: `export class DiscordSDK { clientId='test';customId='';instanceId='classic-test';async ready(){};commands={authorize:async()=>({code:'mock'}),authenticate:async()=>({user:{id:'123456789012345678'}})}; }` });
        if (body.includes('DiscordSDK') && /export\s*\{[^}]*DiscordSDK/.test(body)) return route.fulfill({ contentType: 'application/javascript', body: `export class DiscordSDK { clientId='test';customId='';instanceId='classic-test';async ready(){};commands={authorize:async()=>({code:'mock'}),authenticate:async()=>({user:{id:'123456789012345678'}})}; }` });
        return route.fulfill({ response, body });
      });
      await page.route('**/api/config', route => route.fulfill({ json: { discord_client_id: 'test', noname_available: true } }));
      await page.route('**/api/token', route => route.fulfill({ json: { access_token: 'local-test' } }));
      page.on('dialog', dialog => dialog.dismiss());
      await page.goto(url.href);
      if (seed) await page.evaluate(async () => {
        const db = await new Promise((resolve, reject) => {
          const req = indexedDB.open('noname_0.9_data', 4);
          req.onupgradeneeded = () => {
            for (const name of ['video', 'image', 'audio', 'config', 'data']) {
              if (!req.result.objectStoreNames.contains(name)) req.result.createObjectStore(name, name === 'video' ? { keyPath: 'time' } : undefined);
            }
          };
          req.onsuccess = () => resolve(req.result); req.onerror = reject;
        });
        await new Promise((resolve, reject) => {
          const tx = db.transaction('config', 'readwrite');
          for (const [key, value] of Object.entries({ mode: 'guozhan', characters: ['extra', 'refresh'], cards: ['extra'], extensions: ['old-extension'], plays: ['boss'], identity_mode_mode_config_identity: 'purple', connect_identity_mode_mode_config_identity: 'stratagem', directstartmode: 'guozhan', continue_name: ['shen_caocao'], new_tutorial: true })) tx.objectStore('config').put(value, key);
          tx.oncomplete = resolve; tx.onerror = reject;
        });
        db.close();
        localStorage.setItem('noname_0.9_directstart', 'true');
        localStorage.setItem('noname_0.9_playback', 'old');
      });
      await page.getByRole('button', { name: '三国杀 无名杀 · 免费娱乐试玩' }).click();
      await page.getByRole('checkbox').check();
      await page.getByRole('button', { name: mode === 'solo' ? '单机试玩' : '好友联机', exact: true }).click();
      const iframe = page.frameLocator('iframe');
      await iframe.locator('#window').waitFor({ timeout: 30000 });
      return { page, iframe, frame: page.frames().find(frame => frame.url().includes('/noname/')) };
    }
    async function assertPolicy(client, online) {
      const actual = await client.frame.evaluate(async () => {
        const { lib } = await import('/noname/noname.js');
        if (lib.card.list.some(card => !lib.cardPack.standard.includes(card[2]))) throw new Error('牌堆含非标准牌');
        const characters = Object.values(lib.characterPack.standard);
        if (characters.length !== 33) throw new Error('标准武将数量不符');
        const seen = new Set();
        function checkSkill(name) {
          if (seen.has(name)) return;
          seen.add(name);
          const split = name.lastIndexOf('_');
          const skill = lib.skill[name] || lib.skill[name.slice(0, split)]?.subSkill?.[name.slice(split + 1)];
          if (!skill) throw new Error('缺少技能：' + name);
          if (skill.inherit) checkSkill(skill.inherit);
          for (const child of typeof skill.group === 'string' ? [skill.group] : skill.group || []) checkSkill(child);
        }
        for (const character of characters) for (const skill of character.skills || character[3] || []) checkSkill(skill);
        return { modes: lib.config.all.mode, characters: lib.config.all.characters, cards: lib.config.all.cards,
          loadedCharacters: Object.keys(lib.characterPack), loadedCards: Object.keys(lib.cardPack).filter(key => !['mode_derivation', 'zhanfa'].includes(key)),
          soloModes: Object.keys(lib.mode.identity.config.identity_mode.item), onlineModes: Object.keys(lib.mode.identity.connect.connect_identity_mode.item),
          identity: lib.config.mode_config.identity.identity_mode, extensions: lib.config.extensions };
      });
      expect(actual).toEqual({ modes: online ? ['identity', 'connect'] : ['identity'], characters: ['standard'], cards: ['standard'], loadedCharacters: ['standard'], loadedCards: ['standard'], soloModes: ['normal'], onlineModes: ['normal'], identity: 'normal', extensions: [] });
    }
    async function deal(clients) {
      for (let turn = 0; turn < 60; turn++) {
        for (const { iframe } of clients) {
          if (await iframe.getByText('1.11.6更新内容', { exact: true }).isVisible()) {
            await iframe.getByText('确定', { exact: true }).filter({ visible: true }).last().tap();
          }
          const choices = iframe.locator('.dialog .button.character').filter({ visible: true });
          if (await choices.count()) {
            await choices.first().tap();
            const confirm = iframe.getByText('确定', { exact: true }).filter({ visible: true });
            if (await confirm.count()) await confirm.last().tap({ timeout: 1000 }).catch(async error => {
              if (await choices.count()) throw error; // 自动确认已关闭选将时无需重复点击。
            });
          }
        }
        if ((await Promise.all(clients.map(({ iframe }) => iframe.locator('#handcards1 .card').count()))).every(count => count > 0)) return;
        await clients[0].page.waitForTimeout(300);
      }
      throw new Error('未完成选将/发牌');
    }
    const solo = await entry('solo', true);
    await deal([solo]);
    await assertPolicy(solo, false);
    await solo.page.screenshot({ path: path.join(output, 'classic-solo.png') });
    // 真实菜单只能显示身份和标准包，不靠隐藏父页选项来维持。
    await solo.frame.evaluate(async () => { const { ui } = await import('/noname/noname.js'); ui.click.configMenu(); });
    await expect(solo.iframe.locator('.menu')).toBeVisible();
    await expect(solo.iframe.getByText('国战', { exact: true })).toHaveCount(0);
    await solo.page.screenshot({ path: path.join(output, 'classic-menu.png') });
    await solo.page.close();

    const host = await entry('online', true);
    await host.page.getByRole('button', { name: '创建房间', exact: true }).waitFor();
    await assertPolicy(host, true);
    // 旧父页仍发 expanded 时，子应用也只能创建经典房。
    await host.page.evaluate(() => document.querySelector('iframe').contentWindow.postMessage({ type: 'noname-command', action: 'create', preset: 'expanded', capacity: 4 }, location.origin));
    await expect(host.page.getByRole('heading', { name: '等候牌友' })).toBeVisible({ timeout: 15000 });
    const code = (await host.page.getByRole('button', { name: /复制房间号/ }).innerText()).trim();
    const guest = await entry('online');
    await expect(guest.page.getByRole('button', { name: '入座', exact: true })).toBeVisible();
    // 从真实大厅回调注入一个旧扩展房，列表与房号加入都必须排除它。
    await guest.frame.evaluate(async () => {
      const { lib } = await import('/noname/noname.js');
      lib.message.client.updaterooms([['旧扩展', '', { mode: 'identity', identity_mode: 'normal', characterPack: ['standard', 'extra'], cardPack: ['standard'], number: 4 }, 1, 'expanded-old']]);
    });
    await expect(guest.page.getByText('旧扩展 的房间', { exact: true })).toHaveCount(0);
    await guest.page.getByRole('textbox', { name: '三国杀房间号' }).fill('expanded-old');
    await guest.page.getByRole('button', { name: '加入房间', exact: true }).click();
    await expect(guest.page.getByRole('alert')).toContainText('不是经典标准包');
    // 重进拉取真实房间，确保拒绝错误房号后没有锁死后续操作。
    await guest.page.getByRole('button', { name: '返回', exact: true }).click();
    await guest.page.getByRole('button', { name: '好友联机', exact: true }).click();
    await expect(guest.page.getByRole('button', { name: '入座', exact: true })).toBeVisible();
    guest.frame = guest.page.frames().find(frame => frame.url().includes('/noname/'));
    await guest.page.getByRole('textbox', { name: '三国杀房间号' }).fill(code);
    await guest.page.getByRole('button', { name: '加入房间', exact: true }).click();
    await expect(guest.page.getByText('等待房主开局', { exact: true })).toBeVisible();
    await host.page.screenshot({ path: path.join(output, 'classic-waiting.png') });
    await host.page.getByRole('button', { name: '开始游戏', exact: true }).click();
    await deal([host, guest]);
    await assertPolicy(host, true);
    const config = await host.frame.evaluate(async () => {
      const { lib, game } = await import('/noname/noname.js');
      return { mode: lib.configOL.mode, identity: lib.configOL.identity_mode, characters: lib.configOL.characterPack, cards: lib.configOL.cardPack, players: game.players.length };
    });
    expect(config).toEqual({ mode: 'identity', identity: 'normal', characters: ['standard'], cards: ['standard'], players: 4 });
    await host.page.screenshot({ path: path.join(output, 'classic-online.png') });
    await host.page.getByRole('button', { name: '返回大厅', exact: true }).click();
    expect([...new Set(missing)]).toEqual([]);
    await expect(guest.page.getByRole('button', { name: '好友联机', exact: true })).toBeVisible();
    expect(errors).toEqual([]);
    console.log(JSON.stringify({ result: 'CLASSIC_SMOKE_OK', solo: 'dealt', online: 'two humans + two AI, both dealt', legacyPreset: 'forced classic', legacyRoom: 'hidden and rejected', config, pageErrors: errors }));
  })(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('验收超过 120 秒')), 120000); })]);
} catch (error) {
  console.error(JSON.stringify({ pageErrors: errors, missing: [...new Set(missing)] }));
  for (const [index, page] of browser.contexts().flatMap(context => context.pages()).entries()) {
    await page.screenshot({ path: path.join(output, `failure-${index}.png`) }).catch(() => {});
    console.error((await page.locator('body').innerText().catch(() => '')).slice(-1500));
    for (const frame of page.frames().filter(frame => frame.url().includes('/noname/'))) console.error('FRAME:', (await frame.locator('body').innerText().catch(() => '')).slice(-2500));
  }
  throw error;
} finally {
  clearTimeout(timer);
  await browser.close();
}
