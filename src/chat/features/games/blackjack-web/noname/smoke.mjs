// 真实构建验收：需要已启动资源宿主、联机进程和支持开发身份的本地大厅。
import { chromium, expect } from '@playwright/test';

const url = new URL(process.argv[2] || 'http://127.0.0.1:8491/?dev_user_id=123456789012345678');
if (!['127.0.0.1', 'localhost'].includes(url.hostname)) throw new Error('验收脚本仅允许访问本地测试服务');
const browser = await chromium.launch({ headless: true });
let timer;
try {
  await Promise.race([
    (async () => {
      async function open() {
        // 每个页面使用独立上下文，模拟两名玩家而非共享存储的标签页。
        const page = await browser.newPage({ viewport: { width: 844, height: 390 }, isMobile: true, hasTouch: true });
        page.on('dialog', dialog => dialog.dismiss());
        await page.goto(url.href);
        await page.getByRole('button', { name: '三国杀 无名杀 · 娱乐试玩' }).click();
        await page.getByRole('checkbox').check();
        await page.getByRole('button', { name: '好友联机', exact: true }).click();
        const frame = page.frameLocator('iframe');
        await page.getByRole('button', { name: '创建房间', exact: true }).waitFor();
        return { page, frame };
      }
      const host = await open();
      await host.page.getByRole('button', { name: '创建房间', exact: true }).click();
      await expect(host.page.getByRole('heading', { name: '等候牌友' })).toBeVisible();
      const code = (await host.page.getByRole('button', { name: /复制房间号/ }).innerText()).trim();
      await expect(host.page.getByRole('button', { name: '开始游戏', exact: true })).toBeDisabled();
      const guest = await open();
      await expect(guest.page.getByRole('button', { name: '入座', exact: true })).toBeVisible();
      await guest.page.getByRole('textbox', { name: '三国杀房间号' }).fill(code);
      await guest.page.getByRole('button', { name: '加入房间', exact: true }).click();
      await expect(guest.page.getByText('等待房主开局', { exact: true })).toBeVisible();
      await host.page.screenshot({ path: 'tmp/noname-direct-room-waiting.png' });
      await host.page.getByRole('button', { name: '开始游戏', exact: true }).click();
      // 主公和其他身份按顺序选将，机器人由上游自动补齐。
      for (let turn = 0; turn < 20; turn++) {
        for (const { frame } of [host, guest]) {
          const choices = frame.locator('.dialog .button.character').filter({ visible: true });
          if (await choices.count()) {
            await choices.first().tap();
            const confirm = frame.getByText('确定', { exact: true }).filter({ visible: true });
            if (await confirm.count()) await confirm.last().tap();
          }
        }
        if (await host.frame.locator('#handcards1 .card').count() && await guest.frame.locator('#handcards1 .card').count()) break;
        await host.page.waitForTimeout(500);
      }
      for (const { frame } of [host, guest]) {
        expect(await frame.locator('#handcards1 .card').count()).toBeGreaterThan(0);
        expect(await frame.locator('body').innerText()).not.toContain('?ticket=');
      }
      const config = await host.page.frames()[1].evaluate(async () => {
        const { lib } = await import('/noname/noname.js');
        return { characters: lib.configOL.characterPack, cards: lib.configOL.cardPack, mode: lib.configOL.identity_mode };
      });
      expect(config).toEqual({ characters: ['standard'], cards: ['standard'], mode: 'normal' });
      await host.page.waitForTimeout(700);
      await host.page.screenshot({ path: 'tmp/noname-direct-room-playing.png' });
      await host.page.getByRole('button', { name: '返回大厅', exact: true }).click();
      await expect(guest.page.getByRole('button', { name: '好友联机', exact: true })).toBeVisible();
      await guest.page.getByRole('button', { name: '好友联机', exact: true }).click();
      await guest.page.getByRole('button', { name: '创建房间', exact: true }).click();
      await expect(guest.page.getByRole('heading', { name: '等候牌友' })).toBeVisible();
      await guest.page.getByRole('button', { name: '离开房间', exact: true }).click();
      await expect(guest.page.getByRole('button', { name: '好友联机', exact: true })).toBeVisible();
      console.log('通过：手机横屏、直接开房、房号加入、房主开局、经典标准包、双方发牌');
    })(),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('真实联机验收超过 60 秒')), 60000); }),
  ]);
} finally {
  clearTimeout(timer);
  await browser.close();
}
