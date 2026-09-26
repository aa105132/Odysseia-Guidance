import fs from 'node:fs/promises';
import path from 'node:path';

// 固定版本的共享引擎与经典入口；不复制 dist/src、其他模式、其他卡包或扩展。
export async function copyClassicRuntime(source, output) {
  const dist = path.join(source, 'apps/core/dist');
  const dirs = ['noname', '_virtual', 'node_modules', 'layout', 'theme'];
  const files = ['index.html', 'noname.js', 'mode/identity.js', 'mode/connect.js',
    'character/standard.js', 'character/rank.js', 'character/replace.js', 'character/perfectPairs.js',
    'card/standard.js', 'game/config.json', 'game/package.js', 'game/game.js', 'game/keyWords.js', 'game/update.js',
    'game/build-info.json', 'font/xiaozhuan.woff2', 'font/suits.woff2', 'font/motoyamaru.woff2'];
  for (const name of dirs) await fs.cp(path.join(dist, name), path.join(output, name), { recursive: true });
  for (const name of files) {
    await fs.mkdir(path.dirname(path.join(output, name)), { recursive: true });
    await fs.copyFile(path.join(dist, name), path.join(output, name));
  }
}

export async function inventory(root, prefix = '') {
  const files = [];
  for (const entry of await fs.readdir(path.join(root, prefix), { withFileTypes: true })) {
    const name = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) files.push(...await inventory(root, name));
    else if (entry.isFile()) files.push({ path: name, bytes: (await fs.stat(path.join(root, name))).size });
    else throw new Error('发布资源不允许符号链接：' + name);
  }
  return files.sort((a, b) => a.path.localeCompare(b.path));
}
