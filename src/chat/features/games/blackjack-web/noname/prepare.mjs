// 固定上游版本的发布适配；资源独立存放，不混进大厅构建。
import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { copyClassicRuntime, inventory } from './classic-build.mjs';
import { copyClassicAssets } from './classic-assets.mjs';
import { restrictClassicPackage } from './classic-policy.js';
import { addClassicSkills } from './classic-skills.mjs';
const here = path.dirname(fileURLToPath(import.meta.url));
const source = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
const revision = '1bbf1759962f326e2c53d672b16ba0e214918c2d';
if (execFileSync('git', ['-C', source, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim() !== revision) throw new Error('上游版本不匹配');
await fs.mkdir(output, { recursive: true });
if ((await fs.readdir(output)).length) throw new Error('精简构建必须使用空目录，避免混入旧的完整资源');
await copyClassicRuntime(source, output);
await addClassicSkills(source, output);
await copyClassicAssets(source, output);
for (const name of ['LICENSE', 'README.md']) await fs.copyFile(path.join(source, name), path.join(output, name));
async function edit(name, transform) {
  const file = path.join(output, name);
  await fs.writeFile(file, transform(await fs.readFile(file, 'utf8')));
}
const packageContext = { window: {} };
vm.runInNewContext(await fs.readFile(path.join(output, 'game/package.js'), 'utf8'), packageContext);
restrictClassicPackage(packageContext.window.noname_package, true);
await fs.writeFile(path.join(output, 'game/package.js'), 'window.noname_package = ' + JSON.stringify(packageContext.window.noname_package) + ';\n');
await edit('index.html', text => text
  // 运行已编译包，不在 Discord 内注册即时编译 Service Worker。
  .replace(/<script type="module">[\s\S]*?<\/script>/, '')
  .replace(/<script type="module" crossorigin src="vue"><\/script>/g, '')
  .replace(/("(?:noname|vue|pinyin-pro|dedent)":\s*")\//g, '$1/noname/')
  .replace('<head>', '<head><script src="./activity-bootstrap.js"></script>'));
await edit('noname/entry.js', text => text.replace('"/preload.js"', '"/noname/preload.js"').replace('if (!localStorage.getItem("gplv3_noname_alerted"))', 'await window.odysseiaLaunchReady;\n    if (!localStorage.getItem("gplv3_noname_alerted"))'));
await edit('noname/init/browser.js', text => text.replace('fetch(`${route}${queryString}`, init)', 'fetch(`/noname${route}${queryString}`, init)'));
await edit('noname/init/index.js', text => {
  for (const marker of ['await loadConfig();', 'const pack = window.noname_package;']) {
    if (text.split(marker).length !== 2) throw new Error('经典模式加载补丁不匹配：' + marker);
  }
  return text.replace('await loadConfig();', 'await loadConfig();\n  await window.odysseiaConfigure({lib,game});')
    .replace('const pack = window.noname_package;', 'const pack = window.noname_package;\n  window.odysseiaFilterPackage(pack);');
});
await edit('noname/init/import.js', text => text.replace('async function importFunction(type, path) {', 'async function importFunction(type, path) {\n  path = "/noname" + path;'));
await edit('noname/game/index.js', text => {
  const marker = '`../../mode/${name2}/index.js`';
  if (text.split(marker).length !== 2) throw new Error('联机编译入口补丁不匹配');
  return text.replace(marker, '`../../mode/${name2}.js`');
});
await edit('mode/connect.js', text => {
  const marker = '_status.connectDenied = createNode;';
  if (!text.includes(marker)) throw new Error('自动联机入口补丁不匹配');
  return text.replace(marker, marker + '\n      window.odysseiaConnect?.();');
});
await fs.copyFile(path.join(here, 'activity-bootstrap.js'), path.join(output, 'activity-bootstrap.js'));
await fs.copyFile(path.join(here, 'classic-policy.js'), path.join(output, 'classic-policy.js'));
await fs.writeFile(path.join(output, 'preload.js'), 'export { default } from "./noname/init/browser.js";\n');
// Node 联机进程只绑定回环地址，经鉴权网关转发，不开放原生大厅端口。
const serverSource = await fs.readFile(path.join(source, 'packages/server/dist/cli.cjs'), 'utf8');
const serverPatched = serverSource.replace('WebSocketServer({ port: port2 })', 'WebSocketServer({ port: port2, host: "127.0.0.1", maxPayload: 1048576 })');
if (serverPatched === serverSource) throw new Error('联机服务绑定补丁不匹配');
await fs.writeFile(path.join(source, 'packages/server/dist/activity-server.cjs'), serverPatched);
await fs.writeFile(path.join(output, 'integration-version.json'), JSON.stringify({ upstream: revision, version: '1.11.6', integration: 2, preset: 'classic' }));
// 修改方式和对应源码随发布包提供，保留 GPL 归属。
await fs.cp(here, path.join(output, 'integration-source'), { recursive: true });
const sourcePaths = ['LICENSE', 'README.md', '.npmrc', 'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'tsconfig.json', 'scripts', 'packages',
  'apps/core/package.json', 'apps/core/pnpm-lock.yaml', 'apps/core/vite.config.ts', 'apps/core/tsconfig.json', 'apps/core/tsconfig.types.json',
  'apps/core/index.html', 'apps/core/noname.js', 'apps/core/noname', 'apps/core/typings', 'apps/core/scripts', 'apps/core/game',
  'apps/core/character/standard', 'apps/core/character/rank.js', 'apps/core/character/replace.js', 'apps/core/character/perfectPairs.js',
  // 仅供源码重建与技能提取，不复制进运行时 character/。
  'apps/core/character/refresh', 'apps/core/character/sp',
  'apps/core/card/standard.js', 'apps/core/mode/identity.js', 'apps/core/mode/connect.js', 'apps/core/mode/doudizhu.js', 'apps/core/layout', 'apps/core/theme',
  'apps/core/font/xiaozhuan.woff2', 'apps/core/font/suits.woff2', 'apps/core/font/motoyamaru.woff2'];
execFileSync('git', ['-C', source, 'archive', '--format=tar.gz', '--output=' + path.join(output, 'upstream-source.tar.gz'), revision, ...sourcePaths]);
const files = await inventory(output);
const bytes = files.reduce((total, file) => total + file.bytes, 0);
await fs.writeFile(path.join(output, 'classic-manifest.json'), JSON.stringify({ revision, preset: 'classic', bytes, files }, null, 2));
console.log(JSON.stringify({ output, files: files.length, bytes, mib: +(bytes / 1048576).toFixed(2) }));
