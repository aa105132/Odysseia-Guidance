<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
type Save = { revision: number; entries: Record<string, string> };
const props = defineProps<{ username: string; apiCall: <T>(path: string, method: 'GET' | 'POST' | 'PUT', body?: unknown, retries?: number) => Promise<T> }>();
const emit = defineEmits<{ back: [] }>();
const frame = ref<HTMLIFrameElement>();
const started = ref(false);
const ready = ref(false);
const busy = ref(false);
const leaving = ref(false);
const error = ref('');
const cloudState = ref('等待开始');
const dirty = ref(false);
const exitDialog = ref<HTMLDialogElement>();
let timer: ReturnType<typeof setTimeout> | undefined;
let exitTimer: ReturnType<typeof setTimeout> | undefined;
let generation = 0;
let cloudBusy = false;
let disposed = false;
let leaderboardBusy = false;
let duelBusy = false;
let duelRoom: string | null = null;
let duelPending: Promise<unknown> | null = null;
let battleBusy = false;
let battleRoom: string | null = null;
let battlePending: Promise<unknown> | null = null;
function send(data: object) { frame.value?.contentWindow?.postMessage(data, location.origin); }
async function start() {
  if (busy.value || started.value) return;
  busy.value = true; error.value = '';
  try {
    const status = await props.apiCall<{ available: boolean }>('/api/pokerogue/status', 'GET');
    if (disposed) return;
    if (!status.available) throw new Error('宝可梦资源正在准备，请稍后再试');
    generation++; started.value = true; ready.value = false; cloudState.value = '正在读取云存档';
    timer = setTimeout(() => { error.value = '加载时间较长，请检查网络；游戏仍会继续加载。'; }, 90000);
  } catch (reason) { if (!disposed) error.value = reason instanceof Error ? reason.message : '启动失败'; }
  finally { if (!disposed) busy.value = false; }
}
function askExit() {
  if (!started.value) { emit('back'); return; }
  exitDialog.value?.showModal();
}
async function saveAndExit() {
  if (leaving.value) return;
  leaving.value = true; error.value = '';
  try {
    await duelPending;
    await battlePending;
    if (battleRoom) {
      await props.apiCall('/api/pokerogue/battle', 'POST', { op: 'leave', room: battleRoom }, 0);
      battleRoom = null;
    }
    if (duelRoom) {
      await props.apiCall('/api/pokerogue/duel', 'POST', { op: 'leave', room: duelRoom }, 0);
      duelRoom = null;
    }
    if (disposed) return;
    send({ type: 'pokerogue-cloud-flush', request: 'exit' });
    clearTimeout(exitTimer);
    exitTimer = setTimeout(() => { leaving.value = false; error.value = '尚未确认云存档保存成功，请重试；暂未退出游戏。'; }, 45000);
  } catch (reason) { leaving.value = false; error.value = reason instanceof Error ? reason.message : '离开切磋失败，请重试'; }
}
function retry() { error.value = ''; send({ type: 'pokerogue-cloud-retry' }); }
async function message(event: MessageEvent) {
  if (!started.value || event.origin !== location.origin || event.source !== frame.value?.contentWindow) return;
  const data = event.data;
  if (!data || typeof data !== 'object') return;
  if (data.type === 'pokerogue-ready') { ready.value = true; clearTimeout(timer); error.value = ''; }
  if (data.type === 'pokerogue-cloud-abandon') { dirty.value = false; emit('back'); return; }
  if (data.type === 'pokerogue-cloud-state') {
    dirty.value = data.state !== 'saved';
    cloudState.value = data.state === 'saved' ? '云存档已同步' : data.state === 'saving' ? '正在同步云存档…' : '云存档未同步';
    if (data.state === 'error') error.value = String(data.error || '云存档同步失败，请重试');
    if (data.state === 'saved') error.value = '';
  }
  if (data.type === 'pokerogue-error') error.value = String(data.message || '游戏启动失败，请返回后重试');
  if (data.type === 'pokerogue-cloud-flushed' && data.request === 'exit' && leaving.value) {
    clearTimeout(exitTimer); leaving.value = false;
    if (data.ok) { dirty.value = false; emit('back'); }
    else error.value = String(data.error || '保存失败，暂未退出游戏');
  }
  if (data.type === 'pokerogue-battle' && typeof data.request === 'string' && data.request.length > 0 && data.request.length <= 100) {
    const source = event.source as Window;
    const epoch = generation;
    const reply = (result: object) => {
      if (!disposed && epoch === generation && source === frame.value?.contentWindow) source.postMessage({ type: 'pokerogue-cloud-result', request: data.request, ...result }, location.origin);
    };
    const p = data.payload;
    let valid = p && typeof p === 'object' && !Array.isArray(p) && ['create', 'join', 'state', 'choose', 'leave'].includes(p.op);
    try {
      const keys = p?.op === 'create' ? ['op', 'snapshot'] : p?.op === 'join' ? ['op', 'room', 'snapshot'] : p?.op === 'choose' ? ['op', 'room', 'revision', 'choice'] : ['op', 'room'];
      valid = valid && Object.keys(p).every(key => keys.includes(key)) && new TextEncoder().encode(JSON.stringify(p)).length <= 32 * 1024;
      if (p?.op !== 'create') valid = valid && typeof p?.room === 'string' && /^\d{6}$/.test(p.room);
      if (['create', 'join'].includes(p?.op)) valid = valid && p.snapshot && typeof p.snapshot === 'object' && !Array.isArray(p.snapshot) && p.snapshot.version === 1 && Array.isArray(p.snapshot.team) && p.snapshot.team.length >= 1 && p.snapshot.team.length <= 6;
      if (p?.op === 'choose') valid = valid && Number.isSafeInteger(p.revision) && p.revision >= 0 && typeof p.choice === 'string' && /^(move [1-4]|switch [1-6])$/.test(p.choice);
    } catch { valid = false; }
    if (!valid) { reply({ ok: false, status: 400, error: '对战参数无效' }); return; }
    if (leaving.value) { reply({ ok: false, status: 409, error: '正在退出游戏' }); return; }
    if (battleBusy) { reply({ ok: false, status: 429, error: '对战请求处理中，请稍后重试' }); return; }
    battleBusy = true;
    try {
      battlePending = props.apiCall('/api/pokerogue/battle', 'POST', p, 0);
      const result = await battlePending as { room?: string; status?: string };
      if (typeof result?.room === 'string' && /^\d{6}$/.test(result.room)) {
        battleRoom = ['waiting', 'active'].includes(result.status || '') ? result.room : null;
        if (disposed && battleRoom) void props.apiCall('/api/pokerogue/battle', 'POST', { op: 'leave', room: battleRoom }, 0).catch(() => {});
      }
      reply({ ok: true, data: result });
    } catch (reason) { reply({ ok: false, status: Number((reason as { status?: number })?.status) || 0, error: reason instanceof Error ? reason.message : '对战连接失败' }); }
    finally { if (epoch === generation) battleBusy = false; battlePending = null; }
    return;
  }
  if (data.type === 'pokerogue-duel' && typeof data.request === 'string' && data.request.length > 0 && data.request.length <= 100) {
    const source = event.source as Window;
    const epoch = generation;
    const reply = (result: object) => {
      if (!disposed && epoch === generation && source === frame.value?.contentWindow) source.postMessage({ type: 'pokerogue-cloud-result', request: data.request, ...result }, location.origin);
    };
    const payload = data.payload;
    const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
    let valid = object(payload) && ['create', 'join', 'state', 'exchange', 'leave'].includes(String(payload.op));
    try {
      const fields = payload?.op === 'create' ? ['op', 'snapshot'] : payload?.op === 'join' ? ['op', 'room', 'snapshot'] : payload?.op === 'exchange' ? ['op', 'room', 'step', 'digest', 'command'] : ['op', 'room'];
      valid = valid && Object.keys(payload).every(key => fields.includes(key)) && new TextEncoder().encode(JSON.stringify(payload)).length <= 300 * 1024;
      if (payload?.op !== 'create') valid = valid && typeof payload?.room === 'string' && /^\d{6}$/.test(payload.room);
      if (['create', 'join'].includes(payload?.op)) valid = valid && object(payload.snapshot) && new TextEncoder().encode(JSON.stringify(payload.snapshot)).length <= 256 * 1024;
      if (payload?.op === 'exchange') valid = valid && Number.isSafeInteger(payload.step) && payload.step >= 0 && payload.step <= 10000 && typeof payload.digest === 'string' && /^[a-f0-9]{64}$/.test(payload.digest) && object(payload.command) && new TextEncoder().encode(JSON.stringify(payload.command)).length <= 16 * 1024;
    } catch { valid = false; }
    if (!valid) { reply({ ok: false, status: 400, error: '切磋参数无效' }); return; }
    if (leaving.value) { reply({ ok: false, status: 409, error: '正在退出游戏' }); return; }
    if (duelBusy) { reply({ ok: false, status: 429, error: '切磋请求处理中，请稍后重试' }); return; }
    duelBusy = true;
    try {
      duelPending = props.apiCall('/api/pokerogue/duel', 'POST', payload, 0);
      const result = await duelPending as { room?: string; status?: string };
      if (typeof result?.room === 'string' && /^\d{6}$/.test(result.room)) {
        duelRoom = ['waiting', 'active'].includes(result.status || '') ? result.room : null;
        if (disposed && duelRoom) void props.apiCall('/api/pokerogue/duel', 'POST', { op: 'leave', room: duelRoom }, 0).catch(() => {});
      }
      reply({ ok: true, data: result });
    }
    catch (reason) { reply({ ok: false, status: Number((reason as { status?: number })?.status) || 0, error: reason instanceof Error ? reason.message : '切磋连接失败' }); }
    finally { if (epoch === generation) duelBusy = false; duelPending = null; }
    return;
  }
  if (data.type === 'pokerogue-leaderboard' && typeof data.request === 'string' && data.request.length > 0 && data.request.length <= 100) {
    const source = event.source as Window;
    const epoch = generation;
    const reply = (result: object) => {
      if (!disposed && epoch === generation && source === frame.value?.contentWindow) source.postMessage({ type: 'pokerogue-cloud-result', request: data.request, ...result }, location.origin);
    };
    const { metric, limit, offset } = data;
    if (!['endless', 'wins', 'collection', 'daily'].includes(metric) || !Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0 || offset > 10000) {
      reply({ ok: false, status: 400, error: '排行榜参数无效' }); return;
    }
    if (leaderboardBusy) { reply({ ok: false, status: 429, error: '排行榜正在加载，请稍后重试' }); return; }
    leaderboardBusy = true;
    try {
      const result = await props.apiCall(`/api/pokerogue/leaderboard?metric=${metric}&limit=${limit}&offset=${offset}`, 'GET', undefined, 0);
      reply({ ok: true, data: result });
    } catch (reason) {
      reply({ ok: false, error: reason instanceof Error ? reason.message : '排行榜读取失败' });
    } finally { if (epoch === generation) leaderboardBusy = false; }
    return;
  }
  if (data.type === 'pokerogue-daily-seed' && typeof data.request === 'string' && data.request.length <= 100) {
    const source = event.source as Window;
    const epoch = generation;
    try {
      const result = await props.apiCall('/api/pokerogue/daily-seed', 'GET', undefined, 0);
      if (epoch === generation) source.postMessage({ type: 'pokerogue-cloud-result', request: data.request, ok: true, data: result }, location.origin);
    } catch (reason) {
      if (epoch === generation) source.postMessage({ type: 'pokerogue-cloud-result', request: data.request, ok: false, error: reason instanceof Error ? reason.message : '每日挑战读取失败' }, location.origin);
    }
    return;
  }
  if (!['pokerogue-cloud-load', 'pokerogue-cloud-save'].includes(data.type) || typeof data.request !== 'string' || data.request.length > 100) return;
  const source = frame.value!.contentWindow;
  const epoch = generation;
  const reply = (result: object) => { if (generation === epoch && source === frame.value?.contentWindow) source?.postMessage({ type: 'pokerogue-cloud-result', request: data.request, ...result }, location.origin); };
  if (cloudBusy) { reply({ ok: false, status: 429, error: '正在同步，请稍后重试' }); return; }
  cloudBusy = true;
  try {
    const result = data.type === 'pokerogue-cloud-load'
      ? await props.apiCall<Save>('/api/pokerogue/save', 'GET', undefined, 0)
      : await props.apiCall<Save>('/api/pokerogue/save', 'PUT', { revision: data.revision, entries: data.entries }, 0);
    reply({ ok: true, data: result });
  } catch (reason) {
    const status = Number((reason as { status?: number })?.status) || 0;
    reply({ ok: false, status, error: status === 409 ? '另一台设备已更新云存档。请先导出当前进度备份，再返回大厅重新进入，勿继续覆盖。' : reason instanceof Error ? reason.message : '云存档连接失败' });
  } finally { if (generation === epoch) cloudBusy = false; }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || leaving.value || duelRoom || battleRoom) { event.preventDefault(); event.returnValue = ''; } }
onMounted(() => { window.addEventListener('message', message); window.addEventListener('beforeunload', beforeUnload); void start(); });
onBeforeUnmount(() => {
  disposed = true; generation++; clearTimeout(timer); clearTimeout(exitTimer);
  window.removeEventListener('message', message); window.removeEventListener('beforeunload', beforeUnload);
  if (duelRoom) void props.apiCall('/api/pokerogue/duel', 'POST', { op: 'leave', room: duelRoom }, 0).catch(() => {});
  if (battleRoom) void props.apiCall('/api/pokerogue/battle', 'POST', { op: 'leave', room: battleRoom }, 0).catch(() => {});
});
</script>
<template>
  <section class="pokerogue-game" aria-label="宝可梦肉鸽冒险">
    <header class="rogue-toolbar"><div><strong>PokéRogue</strong><span role="status">{{ busy ? '正在检查资源…' : cloudState }}</span></div><button class="rogue-button" @click="askExit">返回大厅</button></header>
    <div v-if="!started" class="rogue-loading-screen" :aria-busy="busy">
      <p v-if="busy" role="status">正在加载宝可梦…</p>
      <button v-else class="rogue-button" @click="start">重新加载</button>
    </div>
    <div v-else class="rogue-stage">
      <p v-if="!ready" class="rogue-loading" role="status">正在读取账号云存档并加载游戏资源…</p>
      <iframe ref="frame" src="/pokerogue/" title="PokéRogue 宝可梦游戏" allow="autoplay; fullscreen" @error="error = '游戏页面加载失败，请返回后重试'" />
    </div>
    <div v-if="error" class="rogue-error" role="alert"><span>{{ error }}</span><button v-if="started" class="rogue-button" @click="retry">重试同步</button></div>
    <dialog ref="exitDialog" class="rogue-exit" @cancel="leaving && $event.preventDefault()">
      <h3>返回月月大厅？</h3><p>先同步游戏已经生成的存档，再退出。未到游戏保存节点的操作可能需要重玩。</p>
      <p>若正在好友切磋，退出会结束本次对战；切磋临时数据不会覆盖冒险存档。</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <div><button class="rogue-button" :disabled="leaving" @click="exitDialog?.close()">继续冒险</button><button class="rogue-button" :disabled="leaving" @click="saveAndExit">{{ leaving ? '正在同步…' : '同步并返回' }}</button></div>
    </dialog>
  </section>
</template>
<style scoped>
.pokerogue-game { box-sizing: border-box; width: 100%; max-width: none; height: 100%; margin: 0; padding: 0; min-height: 0; align-self: stretch; flex: 1; display: flex; flex-direction: column; color: #f8f8f8; background: #171820; border: 0; border-radius: 0; }
.rogue-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 4px 10px; flex: none; background: #252638; border-bottom: 2px solid #72788e; }
.rogue-toolbar > div { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 14px; }
.rogue-toolbar strong { font-size: 14px; }
.rogue-toolbar span { font-size: 12px; color: #d6e2ea; }
.rogue-button { box-sizing: border-box; min-height: 32px; padding: 5px 12px; border: 2px solid #a6b3c4; border-radius: 0; box-shadow: inset 0 0 0 2px #41475f; background: #30364c; color: #fff; font: inherit; font-size: 13px; cursor: pointer; }
.rogue-button:hover { background: #485471; }
.rogue-button:focus-visible { outline: 2px solid #ffe792; outline-offset: 2px; }
.rogue-button:disabled { opacity: .6; cursor: wait; }
.rogue-loading-screen { flex: 1; display: grid; place-content: center; text-align: center; }
.rogue-stage { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.rogue-stage iframe { display: block; border: 0; width: 100%; flex: 1; min-height: 0; background: #171820; }
.rogue-loading { margin: 0; padding: 6px 12px; font-size: 12px; text-align: center; }
.rogue-error { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 8px; padding: 8px; color: #fff3e5; background: #74352e; font-size: 13px; }
.rogue-exit { box-sizing: border-box; max-width: min(460px, calc(100vw - 32px)); padding: 22px; border: 3px solid #a6b3c4; border-radius: 0; box-shadow: inset 0 0 0 3px #41475f; background: #252638; color: #f8f8f8; }
.rogue-exit::backdrop { background: #100e19bb; }
.rogue-exit p { line-height: 1.7; }
.rogue-exit > div { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 10px; }
@media (max-height: 450px) { .rogue-toolbar { padding: 2px 8px; } }
</style>
