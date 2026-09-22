// 父页面只提供显示昵称与一次性联机票据，不向子游戏传递 Discord Token。
const parentOrigin = location.origin;
let launch;
const ready = new Promise(resolve => {
  window.addEventListener('message', event => {
    if (event.source !== parent || event.origin !== parentOrigin || event.data?.type !== 'noname-launch') return;
    if (launch) return;
    launch = event.data;
    // 外层在用户勾选许可后才允许启动，避免 iframe 原生确认框被宿主禁用。
    localStorage.setItem('gplv3_noname_alerted', 'true');
    resolve(launch);
  });
});
parent.postMessage({ type: 'noname-ready' }, parentOrigin);
window.odysseiaLaunchReady = ready;
window.odysseiaConfigure = async ({ lib, game }) => {
  const data = await ready;
  game.saveConfig('connect_nickname', String(data.username || '茶楼牌友').slice(0, 24));
  game.saveConfig('show_splash', 'off');
  game.saveConfig('mode', data.mode === 'online' ? 'connect' : 'identity');
  game.saveConfig('totouched', true);
  game.saveConfig('touchscreen', matchMedia('(pointer: coarse)').matches);
  game.saveConfig('phonelayout', true);
  game.saveConfig('extension_sources', {});
  game.saveConfig('read_clipboard', false, 'connect');
  game.saveConfig('connect_nickname', String(data.username || '茶楼牌友').slice(0, 24), 'connect');
  if (data.mode === 'online') {
    const requireSandbox = game.requireSandboxOn.bind(game);
    game.requireSandboxOn = () => requireSandbox('');
    const address = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/noname-ws`;
    lib.hallURL = address;
    game.saveConfig('hall_ip', address);
    game.saveConfig('last_ip', address);
    game.saveConfig('reconnect_info', []);
    const originalConnect = game.connect.bind(game);
    game.connect = async (_address, callback) => {
      const request = crypto.randomUUID();
      const ticket = await new Promise(resolve => {
        const listener = event => {
          if (event.source !== parent || event.origin !== parentOrigin || event.data?.type !== 'noname-ticket-result' || event.data.request !== request) return;
          clearTimeout(timeout); window.removeEventListener('message', listener); resolve(event.data.ticket);
        };
        const timeout = setTimeout(() => { window.removeEventListener('message', listener); resolve(null); }, 15000);
        window.addEventListener('message', listener);
        parent.postMessage({ type: 'noname-ticket', request }, parentOrigin);
      });
      if (!ticket) { callback?.(false); return; }
      originalConnect(`${address}?ticket=${encodeURIComponent(ticket)}`, callback);
      // 清除界面和房间分享文案中的一次性票据，只保留大厅地址。
      const { _status } = await import('./noname.js');
      _status.ip = address;
    };
  }
};
// 上游保留自己的 GPL 确认；加载状态用于外层错误提示。
window.addEventListener('error', event => parent.postMessage({ type: 'noname-error', message: String(event.message).slice(0, 240) }, parentOrigin));
