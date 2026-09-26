import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { copyClassicRuntime, inventory } from './classic-build.mjs';

test('classic runtime uses explicit packages instead of copying the full distribution', async () => {
  const root = await fs.mkdtemp(path.join(process.env.PI_SCRATCH_DIR || os.tmpdir(), 'noname-build-test-'));
  try {
    const dist = path.join(root, 'source/apps/core/dist');
    const output = path.join(root, 'output');
    const required = ['index.html', 'noname.js', 'mode/identity.js', 'mode/connect.js', 'character/standard.js', 'character/rank.js', 'character/replace.js', 'character/perfectPairs.js', 'card/standard.js', 'game/config.json', 'game/package.js', 'game/game.js', 'game/keyWords.js', 'game/build-info.json', 'font/xiaozhuan.woff2', 'font/suits.woff2', 'font/motoyamaru.woff2'];
    required.push('game/update.js');
    const excluded = ['mode/guozhan.js', 'mode/boss.js', 'character/extra.js', 'card/extra.js', 'extension/boss/extension.js', 'src/noname.js', 'service-worker.js', 'font/xinwei.woff2'];
    for (const file of [...required, ...excluded]) {
      await fs.mkdir(path.dirname(path.join(dist, file)), { recursive: true });
      await fs.writeFile(path.join(dist, file), file);
    }
    for (const dir of ['noname', '_virtual', 'node_modules', 'layout', 'theme']) {
      await fs.mkdir(path.join(dist, dir), { recursive: true });
      await fs.writeFile(path.join(dist, dir, 'shared.js'), dir);
    }
    await copyClassicRuntime(path.join(root, 'source'), output);
    const files = (await inventory(output)).map(file => file.path);
    for (const file of required) assert.ok(files.includes(file), file);
    for (const file of excluded) assert.ok(!files.includes(file), file);
    assert.deepEqual(files.filter(file => file.startsWith('mode/')), ['mode/connect.js', 'mode/identity.js']);
    assert.deepEqual(files.filter(file => file.startsWith('card/')), ['card/standard.js']);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
