/* Yueyue cloud-only adapter. Loaded before the upstream entry module. */
(() => {
  'use strict';
  const origin = location.origin;
  const pending = new Map();
  let entries = Object.create(null);
  let soloEntries = null;
  let revision = 0, sequence = 0, change = 0, saved = 0;
  let loaded = false, blocked = null, active = null, debounce;
  let loading = false;
  let resolveReady;
  window.__pokerogueCloudReady = new Promise(resolve => { resolveReady = resolve; });
  const send = data => { if (parent !== window) parent.postMessage(data, origin); };
  const report = (state, error = '') => send({ type: 'pokerogue-cloud-state', state, error });
  function rpc(type, payload = {}) {
    if (parent === window) return Promise.reject(new Error('请从月月游戏大厅进入，云存档需要大厅登录。'));
    const request = `cloud-${++sequence}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(request); reject(new Error('云存档请求超时，请检查网络后重试。')); }, 18000);
      pending.set(request, { resolve, reject, timer });
      send({ type, request, ...payload });
    });
  }
  // Read-only native menu API; credentials and account identity stay in the parent.
  window.__pokerogueLeaderboard = async ({ metric, limit = 8, offset = 0 } = {}) => {
    if (!['endless', 'wins', 'collection', 'daily'].includes(metric) || !Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0 || offset > 10000) throw new Error('排行榜参数无效');
    const result = await rpc('pokerogue-leaderboard', { metric, limit, offset });
    const count = value => Number.isSafeInteger(value) && value >= 0;
    const row = value => value && count(value.rank) && value.rank > 0 && typeof value.username === 'string' && value.username.length <= 200 && count(value.value) && typeof value.is_self === 'boolean';
    if (!result || result.trust !== 'client-save' || result.metric !== metric || !Array.isArray(result.entries) || result.entries.length > limit || !result.entries.every(row) || !(result.self === null || (row(result.self) && result.self.is_self)) || !count(result.total) || typeof result.has_more !== 'boolean') throw new Error('排行榜响应格式异常');
    const publicRow = value => ({ rank: value.rank, username: value.username, value: value.value, is_self: value.is_self });
    return { trust: 'client-save', metric, entries: result.entries.map(publicRow), self: result.self === null ? null : publicRow(result.self), total: result.total, has_more: result.has_more, updated_at: typeof result.updated_at === 'string' ? result.updated_at : null };
  };
  const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  function boundedObject(value, bytes) {
    if (!plain(value)) return false;
    try { return new TextEncoder().encode(JSON.stringify(value)).length <= bytes; } catch { return false; }
  }
  // Server-authoritative standard-rules sparring; no writes to adventure Storage.
  window.__pokerogueBattle = async input => {
    const int = (v, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && v >= min && v <= max;
    const text = (v, max = 200) => typeof v === 'string' && v.length <= max;
    const exact = (v, keys) => plain(v) && Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
    const member = p => exact(p, ['species', 'level', 'moves', 'ability', 'nature', 'ivs', 'gender', 'shiny']) && int(p.species, 1, 1025) && int(p.level, 1, 100) && Array.isArray(p.moves) && p.moves.length >= 1 && p.moves.length <= 4 && p.moves.every(m => int(m, 1, 1000)) && new Set(p.moves).size === p.moves.length && int(p.ability, 1, 1000) && text(p.nature, 30) && Array.isArray(p.ivs) && p.ivs.length === 6 && p.ivs.every(v => int(v, 0, 31)) && ['M', 'F', 'N'].includes(p.gender) && typeof p.shiny === 'boolean';
    if (!plain(input) || !['create', 'join', 'state', 'choose', 'leave'].includes(input.op)) throw new Error('对战参数无效');
    const { op, room, snapshot: team, revision: epoch, choice } = input;
    const keys = op === 'create' ? ['op', 'snapshot'] : op === 'join' ? ['op', 'room', 'snapshot'] : op === 'choose' ? ['op', 'room', 'revision', 'choice'] : ['op', 'room'];
    if (!exact(input, keys) || !boundedObject(input, 32 * 1024)) throw new Error('对战参数无效');
    if (op !== 'create' && (typeof room !== 'string' || !/^\d{6}$/.test(room))) throw new Error('请输入六位房间号');
    if (['create', 'join'].includes(op) && (!exact(team, ['version', 'team']) || team.version !== 1 || !Array.isArray(team.team) || team.team.length < 1 || team.team.length > 6 || !team.team.every(member))) throw new Error('标准规则队伍格式无效');
    if (op === 'choose' && (!int(epoch) || typeof choice !== 'string' || !/^(move [1-4]|switch [1-6])$/.test(choice))) throw new Error('对战指令无效');
    const result = await rpc('pokerogue-battle', { payload: input });
    const health = p => plain(p) && int(p.species, 1, 1025) && text(p.name) && int(p.hp) && int(p.maxhp, 1) && p.hp <= p.maxhp && text(p.status, 50);
    const own = p => health(p) && typeof p.active === 'boolean' && typeof p.fainted === 'boolean';
    const move = m => plain(m) && int(m.slot, 1, 4) && text(m.name) && int(m.pp) && int(m.maxpp) && m.pp <= m.maxpp && typeof m.disabled === 'boolean';
    const b = result?.battle;
    const validBattle = b === null || (plain(b) && Array.isArray(b.own) && b.own.length >= 1 && b.own.length <= 6 && b.own.every(own) && (b.opponent === null || health(b.opponent)) && Array.isArray(b.moves) && b.moves.length <= 4 && b.moves.every(move) && Array.isArray(b.canSwitch) && b.canSwitch.length <= 6 && b.canSwitch.every(v => int(v, 1, b.own.length)) && typeof b.forceSwitch === 'boolean' && typeof b.waiting === 'boolean');
    if (!boundedObject(result, 64 * 1024) || !/^\d{6}$/.test(result.room) || (op !== 'create' && result.room !== room) || ![0, 1].includes(result.seat) || !['waiting', 'active', 'finished', 'closed'].includes(result.status) || !int(result.revision) || !int(result.turn) || !Array.isArray(result.players) || result.players.length !== 2 || !result.players.every(p => p === null || (plain(p) && text(p.name))) || !validBattle || !Array.isArray(result.log) || result.log.length > 20 || !result.log.every(s => text(s, 2000)) || ![null, 0, 1].includes(result.winner) || !(result.reason === null || text(result.reason, 1000))) throw new Error('对战响应格式异常');
    const publicHealth = p => ({ species: p.species, name: p.name, hp: p.hp, maxhp: p.maxhp, status: p.status });
    return { room: result.room, seat: result.seat, status: result.status, revision: result.revision, turn: result.turn, players: result.players.map(p => p === null ? null : { name: p.name }), battle: b === null ? null : { own: b.own.map(p => ({ ...publicHealth(p), active: p.active, fainted: p.fainted })), opponent: b.opponent === null ? null : publicHealth(b.opponent), moves: b.moves.map(m => ({ slot: m.slot, name: m.name, pp: m.pp, maxpp: m.maxpp, disabled: m.disabled })), canSwitch: [...b.canSwitch], forceSwitch: b.forceSwitch, waiting: b.waiting }, log: [...result.log], winner: result.winner, reason: result.reason };
  };
  window.__pokerogueDuel = async input => {
    if (!plain(input) || !['create', 'join', 'state', 'exchange', 'leave'].includes(input.op)) throw new Error('切磋参数无效');
    const { op, room, snapshot: team, step, digest, command } = input;
    if (op !== 'create' && (typeof room !== 'string' || !/^\d{6}$/.test(room))) throw new Error('请输入六位房间号');
    if (['create', 'join'].includes(op) && !boundedObject(team, 256 * 1024)) throw new Error('队伍快照无效或过大');
    if (op === 'exchange' && (!Number.isSafeInteger(step) || step < 0 || step > 10000 || typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest) || !boundedObject(command, 16 * 1024))) throw new Error('切磋指令无效');
    const payload = op === 'create' ? { op, snapshot: team } : op === 'join' ? { op, room, snapshot: team } : op === 'exchange' ? { op, room, step, digest, command } : { op, room };
    const result = await rpc('pokerogue-duel', { payload });
    const player = p => p === null || (plain(p) && typeof p.name === 'string' && p.name.length <= 200 && boundedObject(p.snapshot, 256 * 1024));
    const exchange = result?.exchange;
    if (!plain(result) || typeof result.room !== 'string' || !/^\d{6}$/.test(result.room) || (op !== 'create' && result.room !== room) || ![0, 1].includes(result.seat) || !['waiting', 'active', 'closed', 'desync'].includes(result.status) || typeof result.seed !== 'string' || !result.seed.length || result.seed.length > 200 || !Number.isSafeInteger(result.step) || result.step < 0 || result.step > 10001 || !Array.isArray(result.players) || result.players.length !== 2 || !result.players.every(player) || !Array.isArray(result.submitted) || result.submitted.length !== 2 || !result.submitted.every(v => typeof v === 'boolean') || !(result.reason === null || (typeof result.reason === 'string' && result.reason.length <= 1000)) || !(exchange === null || (plain(exchange) && Number.isSafeInteger(exchange.step) && exchange.step >= 0 && exchange.step < result.step && typeof exchange.digest === 'string' && /^[a-f0-9]{64}$/.test(exchange.digest) && Array.isArray(exchange.commands) && exchange.commands.length === 2 && exchange.commands.every(c => boundedObject(c, 16 * 1024))))) throw new Error('切磋响应格式异常');
    return { room: result.room, seat: result.seat, status: result.status, seed: result.seed, step: result.step, players: result.players.map(p => p === null ? null : { name: p.name, snapshot: p.snapshot }), exchange: exchange === null ? null : { step: exchange.step, digest: exchange.digest, commands: exchange.commands }, submitted: result.submitted, reason: result.reason };
  };
  // A duel gets a disposable Storage fork. Never merge it back into solo saves.
  let startingDuel = null;
  window.__pokerogueDuelStorage = {
    begin() {
      if (startingDuel) return startingDuel;
      startingDuel = (async () => {
        if (soloEntries) throw new Error('切磋存档已经隔离');
        await flush();
        if (blocked || saved !== change) throw new Error('请先完成云存档同步');
        soloEntries = entries;
        entries = Object.assign(Object.create(null), soloEntries);
      })().finally(() => { startingDuel = null; });
      return startingDuel;
    },
    end() {
      if (startingDuel) throw new Error('正在准备切磋存档，请稍后退出');
      if (soloEntries) { entries = soloEntries; soloEntries = null; }
    },
    get active() { return soloEntries !== null; },
  };
  // Resolve only a server-selected seed. Failed requests stay on a visible retry
  // screen rather than silently creating a different challenge from client time.
  let dailyRequest;
  window.__pokerogueDailySeed = () => {
    if (dailyRequest) return dailyRequest;
    dailyRequest = new Promise(resolve => {
      const attempt = async () => {
        const existing = document.getElementById('yueyue-daily-error');
        existing?.remove();
        try {
          const result = await rpc('pokerogue-daily-seed');
          if (!result || !/^\d{4}-\d{2}-\d{2}$/.test(result.date) || result.seed !== btoa(result.date)) throw new Error('每日挑战响应格式异常');
          resolve(result.seed);
        } catch (error) {
          const panel = document.createElement('div'); panel.id = 'yueyue-daily-error';
          Object.assign(panel.style, { position: 'fixed', inset: '0', zIndex: '2147483646', background: '#252431f5', color: '#fff0d0', display: 'grid', placeContent: 'center', padding: '24px', textAlign: 'center' });
          const text = document.createElement('p'); text.textContent = `每日挑战读取失败：${error.message}`;
          const retry = document.createElement('button'); retry.textContent = '重试每日挑战'; retry.onclick = attempt;
          panel.append(text, retry); document.body.append(panel);
        }
      };
      void attempt();
    }).finally(() => { dailyRequest = null; });
    return dailyRequest;
  };
  function snapshot() { return { ...entries }; }
  function equal(a, b) { const keys = Object.keys(a); return keys.length === Object.keys(b).length && keys.every(k => a[k] === b[k]); }
  function hideError() { document.getElementById('yueyue-cloud-error')?.remove(); }
  function displayError(error) {
    blocked = error;
    report('error', error.message);
    const render = () => {
      hideError();
      const panel = document.createElement('div'); panel.id = 'yueyue-cloud-error';
      Object.assign(panel.style, { position: 'fixed', inset: '0', zIndex: '2147483647', background: '#252431f5', color: '#fff0d0', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', padding: '24px', font: '16px/1.7 sans-serif', textAlign: 'center' });
      const heading = document.createElement('h2'); heading.textContent = '云存档尚未同步';
      const text = document.createElement('p'); text.textContent = error.message;
      const retry = document.createElement('button'); retry.textContent = '重试同步'; retry.onclick = () => retryCloud();
      const backup = document.createElement('button'); backup.textContent = '下载当前进度备份'; backup.disabled = !loaded;
      backup.onclick = () => { const url = URL.createObjectURL(new Blob([JSON.stringify({ format: 'yueyue-pokerogue-cloud-backup-v1', revision, entries: snapshot() })], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'pokerogue-cloud-backup.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); };
      const leave = document.createElement('button'); leave.textContent = '放弃未同步进度并返回大厅'; leave.onclick = () => { if (confirm('确定放弃当前未同步进度？服务器已有的云存档不会删除。')) send({ type: 'pokerogue-cloud-abandon' }); };
      for (const b of [retry, backup, leave]) Object.assign(b.style, { padding: '10px 16px', margin: '6px', cursor: 'pointer' });
      panel.append(heading, text, retry, backup, leave); document.body.append(panel);
    };
    if (document.body) render(); else document.addEventListener('DOMContentLoaded', render, { once: true });
  }
  function changed() {
    if (!loaded || soloEntries) return;
    change++; report('saving');
    clearTimeout(debounce);
    if (!blocked) debounce = setTimeout(() => { flush().catch(() => {}); }, 300);
  }
  // A per-iframe in-memory Storage-compatible object: no shared browser disk state
  // can leak into a different account. ownKeys supports Object.hasOwn upstream.
  const storage = new Proxy(Object.create(null), {
    get(_target, key) {
      if (key === 'length') return Object.keys(entries).length;
      if (key === 'getItem') return name => Object.hasOwn(entries, String(name)) ? entries[String(name)] : null;
      if (key === 'setItem') return (name, value) => { name = String(name); value = String(value); if (entries[name] !== value) { entries[name] = value; changed(); } };
      if (key === 'removeItem') return name => { name = String(name); if (Object.hasOwn(entries, name)) { delete entries[name]; changed(); } };
      if (key === 'clear') return () => { if (Object.keys(entries).length) { for (const name of Object.keys(entries)) delete entries[name]; changed(); } };
      if (key === 'key') return index => Object.keys(entries)[Number(index)] ?? null;
      if (key === Symbol.toStringTag) return 'Storage';
      return entries[key];
    },
    set(_target, key, value) { if (typeof key === 'string') { const text = String(value); if (entries[key] !== text) { entries[key] = text; changed(); } } return true; },
    deleteProperty(_target, key) { if (Object.hasOwn(entries, key)) { delete entries[key]; changed(); } return true; },
    ownKeys() { return Object.keys(entries); },
    has(_target, key) { return Object.hasOwn(entries, key); },
    getOwnPropertyDescriptor(_target, key) { if (Object.hasOwn(entries, key)) return { value: entries[key], writable: true, enumerable: true, configurable: true }; },
  });
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: false });
  async function load() {
    if (loading || loaded) return;
    loading = true;
    try {
      const result = await rpc('pokerogue-cloud-load');
      if (!Number.isInteger(result.revision) || !result.entries || typeof result.entries !== 'object') throw new Error('云存档响应格式异常');
      for (const key of Object.keys(entries)) delete entries[key];
      for (const [key, value] of Object.entries(result.entries)) { if (typeof value !== 'string') throw new Error('云存档内容格式异常'); entries[key] = value; }
      revision = result.revision; loaded = true; blocked = null;
      hideError(); report('saved'); resolveReady();
    } catch (error) { displayError(error); } finally { loading = false; }
  }
  async function flush() {
    clearTimeout(debounce);
    if (!loaded) throw new Error('云存档尚未读取成功，游戏未启动。');
    if (soloEntries) return; // Parent exit flush acknowledges only the saved solo fork.
    if (active) return active;
    if (blocked) throw blocked;
    active = (async () => {
      while (saved !== change) {
        const version = change, value = snapshot();
        report('saving');
        try {
          const result = await rpc('pokerogue-cloud-save', { revision, entries: value });
          revision = result.revision; saved = version;
        } catch (error) {
          // A lost response may hide a committed write. Only accept an exact match;
          // never rebase stale data over another device's newer save.
          if (!error.status || error.status === 409) {
            try { const remote = await rpc('pokerogue-cloud-load'); if (equal(remote.entries, value)) { revision = remote.revision; saved = version; continue; } } catch { /* Keep original failure. */ }
          }
          throw error;
        }
      }
      report('saved');
    })();
    try { await active; } catch (error) { displayError(error); throw error; } finally { active = null; }
  }
  async function retryCloud() {
    if (!loaded) return load();
    if (active) return;
    if (blocked?.status === 409) { displayError(blocked); return; }
    blocked = null; hideError();
    try { await flush(); } catch { /* Already shown. */ }
  }
  addEventListener('message', async event => {
    if (event.origin !== origin || event.source !== parent || parent === window) return;
    const data = event.data;
    if (data?.type === 'pokerogue-cloud-result') {
      const waiter = pending.get(data.request); if (!waiter) return;
      pending.delete(data.request); clearTimeout(waiter.timer);
      if (data.ok) waiter.resolve(data.data); else waiter.reject(Object.assign(new Error(data.error || '云存档失败'), { status: data.status }));
    }
    if (data?.type === 'pokerogue-cloud-retry') await retryCloud();
    if (data?.type === 'pokerogue-cloud-flush') {
      try { await flush(); send({ type: 'pokerogue-cloud-flushed', request: data.request, ok: true }); }
      catch (error) { send({ type: 'pokerogue-cloud-flushed', request: data.request, ok: false, error: error.message }); }
    }
  });
  for (const name of ['keydown', 'keyup', 'pointerdown', 'pointerup']) addEventListener(name, event => {
    if (blocked && !document.getElementById('yueyue-cloud-error')?.contains(event.target)) { event.stopImmediatePropagation(); event.preventDefault(); }
    const dailyPanel = document.getElementById('yueyue-daily-error');
    if (dailyPanel && !dailyPanel.contains(event.target)) { event.stopImmediatePropagation(); event.preventDefault(); }
  }, true);
  addEventListener('beforeunload', event => { if (loaded && saved !== change) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('visibilitychange', () => { if (document.hidden && loaded && !blocked) flush().catch(() => {}); });
  load();
})();
