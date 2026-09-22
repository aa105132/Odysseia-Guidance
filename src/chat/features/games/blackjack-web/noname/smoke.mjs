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
        const page = await browser.newPage({ viewport: { width: 844, height: 390 } });
        page.on('dialog', dialog => dialog.dismiss());
        await page.goto(url.href);
        await page.getByRole('button', { name: '三国杀 无名杀 · 娱乐试玩' }).click();
        await page.getByRole('checkbox').check();
        await page.getByRole('button', { name: '好友联机', exact: true }).click();
        const frame = page.frameLocator('iframe');
        await frame.getByText('连接', { exact: true }).click();
        await frame.getByText('创建房间', { exact: true }).waitFor();
        return { page, frame };
      }
      const host = await open();
      await host.frame.getByText('创建房间', { exact: true }).click();
      await host.frame.getByText('启', { exact: true }).filter({ visible: true }).first().click();
      await host.frame.getByText('跳过向导', { exact: true }).click();
      const guest = await open();
      await guest.frame.getByText(/等待中.*人数：1\/8/).click();
      await expect(guest.frame.getByText('分享房间', { exact: true })).toBeVisible();
      await host.frame.getByText('开始游戏', { exact: true }).click();
      // 主公和其他身份按顺序选将，机器人由上游自动补齐。
      for (let turn = 0; turn < 20; turn++) {
        for (const { frame } of [host, guest]) {
          const choices = frame.locator('.dialog .button.character').filter({ visible: true });
          if (await choices.count()) {
            await choices.first().click();
            const confirm = frame.getByText('确定', { exact: true }).filter({ visible: true });
            if (await confirm.count()) await confirm.last().click();
          }
        }
        if (await host.frame.locator('#handcards1 .card').count() && await guest.frame.locator('#handcards1 .card').count()) break;
        await host.page.waitForTimeout(500);
      }
      for (const { frame } of [host, guest]) {
        expect(await frame.locator('#handcards1 .card').count()).toBeGreaterThan(0);
        expect(await frame.locator('body').innerText()).not.toContain('?ticket=');
      }
      console.log('通过：手机横屏、两客户端入房、选将、双方发牌、界面无票据泄露');
    })(),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('真实联机验收超过 60 秒')), 60000); }),
  ]);
} finally {
  clearTimeout(timer);
  await browser.close();
}
