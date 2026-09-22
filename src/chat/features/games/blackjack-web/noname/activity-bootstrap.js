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
    const { ui, get, _status } = await import('./noname.js');
    const publish = value => parent.postMessage(value, parentOrigin);
    let roomList = [];
    let pending = false;
    const rooms = list => {
      roomList = list.filter(Array.isArray);
      publish({ type: 'noname-rooms', rooms: roomList.map(([host, , config, count, id]) => ({
        id: String(id), host: String(host || '牌友'), count: Number(count),
        capacity: Number(config.number || config.player_number || 8), playing: Boolean(config.gameStarted),
      })) });
    };
    for (const name of ['roomlist', 'updaterooms']) {
      const original = lib.message.client[name];
      lib.message.client[name] = function (list, ...args) {
        const result = original.call(this, list, ...args);
        rooms(list);
        return result;
      };
    }
    lib.message.client.enterroomfailed = () => {
      pending = false;
      // 原版失败提示使用 alert，在 Discord 内可能不显示。
      _status.enteringroom = false;
      ui.create.connecting(true);
      publish({ type: 'noname-error', message: '房间已满或已开始，请重新选择' });
    };
    game.reload = () => publish({ type: 'noname-exit' });
    window.odysseiaConnect = () => {
      // 同浏览器的两个活动也必须有各自的联机身份，避免覆盖房间。
      game.onlineKey = crypto.randomUUID();
      game.roomId = undefined;
      game.requireSandboxOn();
      game.connect(lib.hallURL, success => {
        if (!success) publish({ type: 'noname-error', message: '联机连接失败，请返回重试' });
      });
    };
    window.addEventListener('message', event => {
      if (event.source !== parent || event.origin !== parentOrigin || event.data?.type !== 'noname-command') return;
      const command = event.data;
      if (command.action === 'start') {
        if (_status.waitingForPlayer && !game.online && game.connectPlayers?.filter(player => player.nickname).length >= 2) {
          // 复用原版开始按钮的完整清理与开局逻辑。
          ui.connectStartButton?.dispatchEvent(new Event(lib.config.touchscreen ? 'touchend' : 'click'));
        }
        return;
      }
      if (!ui.rooms || pending) return;
      if (command.action === 'create') {
        pending = true;
        const classic = command.preset !== 'expanded';
        const capacity = [2, 4, 6, 8].includes(command.capacity) ? command.capacity : 8;
        game.saveConfig('connect_identity_mode', 'normal', 'identity');
        game.saveConfig('connect_player_number', String(capacity), 'identity');
        game.saveConfig('connect_characters', classic ? lib.connectCharacterPack.filter(pack => pack !== 'standard') : []);
        game.saveConfig('connect_cards', lib.connectCardPack.filter(pack => !['standard', ...(classic ? [] : ['extra'])].includes(pack)));
        game.saveConfig('new_tutorial', true);
        lib.configOL.mode = 'identity';
        game.send('server', 'create', game.onlineKey, get.connectNickname(), lib.config.connect_avatar);
      } else if (command.action === 'join') {
        const room = roomList.find(room => String(room[4]) === String(command.id));
        if (!room || room[2].gameStarted || Number(room[3]) >= Number(room[2].number || room[2].player_number || 8)) {
          publish({ type: 'noname-error', message: '房间不存在、已满或已开局' }); return;
        }
        pending = true;
        game.roomId = room[4];
        game.send('server', 'enter', room[4], get.connectNickname(), lib.config.connect_avatar);
      }
    });
    let previous = '';
    const monitor = setInterval(() => {
      const waiting = Boolean(_status.waitingForPlayer && game.connectPlayers);
      const state = {
        type: 'noname-state', phase: waiting ? 'waiting' : ui.rooms ? 'lobby' : game.players.length ? 'playing' : 'loading',
        roomId: game.roomId ? String(game.roomId) : '', host: Boolean(waiting && !game.online),
        players: waiting ? game.connectPlayers.filter(player => player.nickname).map(player => String(player.nickname)) : [],
        capacity: Number(lib.configOL.number || 8),
      };
      const serialized = JSON.stringify(state);
      if (serialized !== previous) { previous = serialized; publish(state); }
    }, 300);
    window.addEventListener('pagehide', () => clearInterval(monitor), { once: true });
    const requireSandbox = game.requireSandboxOn.bind(game);
    game.requireSandboxOn = () => requireSandbox('');
    const address = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/noname-ws`;
    lib.hallURL = address;
    game.saveConfig('hall_ip', address);
    game.saveConfig('last_ip', address);
    game.saveConfig('reconnect_info', []);
    game.saveConfig('tmp_owner_roomId');
    game.saveConfig('tmp_user_roomId');
    game.saveConfig('directstartmode');
    localStorage.removeItem(lib.configprefix + 'directstart');
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
