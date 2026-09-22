// 固定上游版本的发布适配；资源独立存放，不混进大厅构建。
import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const source = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
const revision = '1bbf1759962f326e2c53d672b16ba0e214918c2d';
if (execFileSync('git', ['-C', source, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim() !== revision) throw new Error('上游版本不匹配');
await fs.mkdir(output, { recursive: true });
await fs.cp(path.join(source, 'apps/core/dist'), output, { recursive: true });
for (const name of ['audio', 'image', 'extension']) await fs.cp(path.join(source, 'apps/core', name), path.join(output, name), { recursive: true });
for (const name of ['LICENSE', 'README.md', 'docs']) await fs.cp(path.join(source, name), path.join(output, name), { recursive: true });
async function edit(name, transform) {
  const file = path.join(output, name);
  await fs.writeFile(file, transform(await fs.readFile(file, 'utf8')));
}
await edit('index.html', text => text
  // 运行已编译包，不在 Discord 内注册即时编译 Service Worker。
  .replace(/<script type="module">[\s\S]*?<\/script>/, '')
  .replace(/<script type="module" crossorigin src="vue"><\/script>/g, '')
  .replace(/("(?:noname|vue|pinyin-pro|dedent)":\s*")\//g, '$1/noname/')
  .replace('<head>', '<head><script src="./activity-bootstrap.js"></script>'));
await edit('noname/entry.js', text => text.replace('"/preload.js"', '"/noname/preload.js"').replace('if (!localStorage.getItem("gplv3_noname_alerted"))', 'await window.odysseiaLaunchReady;\n    if (!localStorage.getItem("gplv3_noname_alerted"))'));
await edit('noname/init/browser.js', text => text.replace('fetch(`${route}${queryString}`, init)', 'fetch(`/noname${route}${queryString}`, init)'));
await edit('noname/init/index.js', text => text.replace('await loadConfig();', 'await loadConfig();\n  await window.odysseiaConfigure?.({lib,game});'));
await edit('noname/init/import.js', text => text.replace('async function importFunction(type, path) {', 'async function importFunction(type, path) {\n  path = "/noname" + path;'));
await edit('mode/connect.js', text => {
  const marker = '_status.connectDenied = createNode;';
  if (!text.includes(marker)) throw new Error('自动联机入口补丁不匹配');
  return text.replace(marker, marker + '\n      window.odysseiaConnect?.();');
});
await fs.copyFile(path.join(here, 'activity-bootstrap.js'), path.join(output, 'activity-bootstrap.js'));
await fs.writeFile(path.join(output, 'preload.js'), 'export { default } from "./noname/init/browser.js";\n');
// Node 联机进程只绑定回环地址，经鉴权网关转发，不开放原生大厅端口。
const serverSource = await fs.readFile(path.join(source, 'packages/server/dist/cli.cjs'), 'utf8');
const serverPatched = serverSource.replace('WebSocketServer({ port: port2 })', 'WebSocketServer({ port: port2, host: "127.0.0.1", maxPayload: 1048576 })');
if (serverPatched === serverSource) throw new Error('联机服务绑定补丁不匹配');
await fs.writeFile(path.join(source, 'packages/server/dist/activity-server.cjs'), serverPatched);
await fs.writeFile(path.join(output, 'integration-version.json'), JSON.stringify({ upstream: revision, version: '1.11.6', integration: 1 }));
// 修改方式和对应源码随发布包提供，保留 GPL 归属。
await fs.cp(here, path.join(output, 'integration-source'), { recursive: true });
try { await fs.access(path.join(output, 'upstream-source.tar.gz')); }
catch { execFileSync('git', ['-C', source, 'archive', '--format=tar.gz', '--output=' + path.join(output, 'upstream-source.tar.gz'), revision]); }
console.log('无名杀资源已准备：' + output);
