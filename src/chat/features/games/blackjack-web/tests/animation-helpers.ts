import { expect, type Locator, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

export async function captureFinalScreenshot(page: Page, name: string) {
  await expect.poll(() => page.locator('img:visible').evaluateAll(images => images.filter(image =>
    !(image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0)
  ).map(image => image.getAttribute('src'))), { message: '截图中的图片必须实际加载成功，不能用空白占位通过视觉验证' }).toEqual([]);
  const missingBackgrounds = await page.evaluate(async () => {
    const sources = new Set<string>();
    for (const element of document.querySelectorAll('*')) {
      if (!element.getClientRects().length) continue;
      const background = getComputedStyle(element).backgroundImage;
      for (const match of background.matchAll(/url\(["']?([^"')]+)["']?\)/g)) sources.add(match[1]!);
    }
    return (await Promise.all(Array.from(sources, async source => {
      const image = new Image();
      image.src = source;
      try { await image.decode(); return null; } catch { return source; }
    }))).filter(Boolean);
  });
  expect(missingBackgrounds, '截图中的桌面和茶楼背景必须加载成功').toEqual([]);
  const directory = resolve(process.cwd(), '../../../../../screenshots/guochao-ui');
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: resolve(directory, name) });
}

export async function expectIllustrationVisible(entry: Locator) {
  const illustration = entry.locator('.game-icon, .game-card-art img');
  await expect(illustration).toBeVisible();
  await expect.poll(() => illustration.evaluate(element => {
    if (element instanceof HTMLImageElement) return element.complete && element.naturalWidth > 0;
    return element instanceof SVGElement && element.querySelector('path,rect,circle,ellipse,g,image') !== null;
  }), { message: '游戏插画必须完整显示，支持真实图片或矢量图' }).toBe(true);
}

export async function waitForTableMotion(page: Page) {
  // 几何断言只采样动画完成后的实际位置，不放宽遮挡和溢出约束。
  await expect.poll(() => page.evaluate(() => document.querySelectorAll('[data-dealing="true"]').length + document.getAnimations().filter(animation => {
    const target = (animation.effect as KeyframeEffect | null)?.target;
    if (!(target instanceof Element) || !target.closest('.single-mode-view, .multi-mode-view, .table-games')) return false;
    const timing = animation.effect?.getComputedTiming();
    return (animation.playState === 'running' || animation.pending) && Number.isFinite(Number(timing?.endTime));
  }).length), { timeout: 5000, message: '等待牌桌有限动画结束后测量布局' }).toBe(0);
}

export async function expectRealMotion(locator: Locator) {
  await expect.poll(() => locator.evaluateAll(elements => elements.some(element =>
    element.getAnimations({ subtree: true }).some(animation => {
      if (!(animation instanceof CSSAnimation) || animation.playState !== 'running') return false;
      const effect = animation.effect as KeyframeEffect;
      const frames = effect.getKeyframes();
      const timing = effect.getComputedTiming();
      const progress = timing.progress;
      const changes = new Set(frames.map(frame => `${frame.transform}/${frame.opacity}/${frame.filter}`)).size > 1;
      return changes && progress !== null && progress > 0 && progress < 1;
    })
  )), { timeout: 2500, intervals: [20, 30, 40, 60], message: '必须实际播放具有变化关键帧的CSS动画，并观测到中间态' }).toBe(true);
}

export async function expectBustSpriteMotion(locator: Locator) {
  const sprite = locator.locator('.bust-sprite');
  await expect(sprite).toBeVisible();
  await expect.poll(() => sprite.evaluate(element => element.getAnimations().some(animation => {
    if (!(animation instanceof CSSAnimation) || animation.playState !== 'running') return false;
    const effect = animation.effect as KeyframeEffect;
    const progress = effect.getComputedTiming().progress;
    const positions = new Set(effect.getKeyframes().map(frame => `${frame.backgroundPosition}/${frame.backgroundPositionX}/${frame.backgroundPositionY}`));
    return animation.animationName.startsWith('bust-sprite') && positions.size > 1 && progress !== null && progress > 0 && progress < 1;
  })), { timeout: 2500, intervals: [20, 30, 40], message: '爆牌序列帧必须实际切换帧并观测到中间态' }).toBe(true);
  const source = await sprite.evaluate(element => getComputedStyle(element).backgroundImage.match(/url\(["']?([^"')]+)["']?\)/)?.[1]);
  expect(source).toContain('/ui/guochao/burst-strip.webp');
  const dimensions = await sprite.page().evaluate(async source => {
    const image = new Image();
    image.src = source!;
    await image.decode();
    return { width: image.naturalWidth, height: image.naturalHeight };
  }, source);
  expect(dimensions.width, '爆牌序列图需包含16帧完整图像').toBe(dimensions.height * 16);
}

export async function recordCardAnimations(page: Page) {
  await page.addInitScript(() => {
    const entries: { name: string; label: string; targetId: number; time: number }[] = [];
    const ids = new WeakMap<Element, number>();
    let nextId = 0;
    (window as any).__cardAnimations = entries;
    (window as any).__bustAnimations = [];
    (window as any).__resultAnimations = [];
    document.addEventListener('animationstart', event => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest('[data-bust-burst]')) (window as any).__bustAnimations.push(event.animationName);
      if (target.closest('.round-feedback')) (window as any).__resultAnimations.push(event.animationName);
      const card = target.closest('.playing-card, .tg-hand-card, [data-card-dealing]');
      if (!card) return;
      if (!ids.has(card)) ids.set(card, ++nextId);
      entries.push({
        name: event.animationName, label: card.getAttribute('alt') || card.getAttribute('aria-label') || '',
        targetId: ids.get(card)!, time: performance.now(),
      });
    }, true);
  });
}

export async function animationEvents(page: Page) {
  return page.evaluate(() => (window as any).__cardAnimations as { name: string; label: string; targetId: number; time: number }[]);
}

export async function expectResultReadable(page: Page, result: Locator) {
  await waitForTableMotion(page);
  await expect(result).toBeInViewport({ ratio: 1 });
  const details = await result.evaluate(element => {
    const box = element.getBoundingClientRect();
    const headings = Array.from(element.querySelectorAll('h1,h2,h3,strong')).filter(node => node.textContent?.trim());
    return {
      font: Math.max(...headings.map(node => parseFloat(getComputedStyle(node).fontSize))),
      width: box.width, height: box.height,
      overflow: element.scrollWidth > element.clientWidth + 1,
    };
  });
  expect(details.font, '结算标题必须醒目').toBeGreaterThanOrEqual(20);
  expect(details.width).toBeGreaterThan(100);
  expect(details.height).toBeGreaterThan(45);
  expect(details.overflow, '结果内容不可横向裁切').toBe(false);
  const collisions = await result.evaluate(element => {
    const a = element.getBoundingClientRect();
    const targets = document.querySelectorAll('.playing-card, .own-score, .score-badge, .seat-player-avatar, .table-toolbar button, .action-dock button, .action-dock input, .tg-hand-card, .tg-board-card img, .tg-avatar, .tg-toolbar button, .tg-dock button, .tg-dock input, .tg-dock select, .tg-seat-play img, .tg-play-label, .tg-hidden-hand, .tg-tile-row');
    return Array.from(targets).filter(target => {
      if (!target.getClientRects().length) return false;
      const b = target.getBoundingClientRect();
      return Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1
        && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
    }).map(target => target.getAttribute('aria-label') || target.getAttribute('alt') || target.className);
  });
  expect(collisions, '结算不能覆盖手牌、点数、头像、公开牌或操作按钮').toEqual([]);
}
