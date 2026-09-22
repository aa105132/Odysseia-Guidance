<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import CopyRoomCode from './CopyRoomCode.vue';
const props = defineProps<{ username: string; apiCall: <T>(path: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T> }>();
const emit = defineEmits<{ back: [] }>();
const frame = ref<HTMLIFrameElement>();
const started = ref(false);
const busy = ref(false);
const error = ref('');
const accepted = ref(false);
const online = ref(false);
const phase = ref('loading');
const rooms = ref<{ id: string; host: string; count: number; capacity: number; playing: boolean }[]>([]);
const room = ref({ roomId: '', host: false, players: [] as string[], capacity: 8 });
const roomCode = ref('');
const preset = ref('classic');
const capacity = ref(8);
const pending = ref(false);
let commandTimer: ReturnType<typeof setTimeout> | undefined;
let launch: { type: string; username: string; mode: string };
function reset() {
  clearTimeout(commandTimer); started.value = false; online.value = false; pending.value = false;
  phase.value = 'loading'; rooms.value = []; room.value = { roomId: '', host: false, players: [], capacity: 8 };
}
function command(action: string, id?: string) {
  if (pending.value) return;
  error.value = ''; pending.value = true;
  frame.value?.contentWindow?.postMessage({ type: 'noname-command', action, id, preset: preset.value, capacity: capacity.value }, location.origin);
  clearTimeout(commandTimer);
  commandTimer = setTimeout(() => { pending.value = false; error.value = '操作未完成，请返回重试'; }, 20000);
}
async function start(mode: string) {
  busy.value = true; error.value = '';
  try {
    const status = await props.apiCall<{ available: boolean }>('/api/noname/status', 'GET');
    if (!status.available) throw new Error('三国杀资源正在准备，请稍后再试');
    launch = { type: 'noname-launch', username: props.username, mode };
    online.value = mode === 'online';
    started.value = true;
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '启动失败'; }
  finally { busy.value = false; }
}
async function message(event: MessageEvent) {
  if (event.origin !== location.origin || event.source !== frame.value?.contentWindow) return;
  if (event.data?.type === 'noname-ready') frame.value.contentWindow?.postMessage(launch, location.origin);
  if (event.data?.type === 'noname-ticket') {
    const source = frame.value.contentWindow;
    const reply = (result: { ticket?: string; error?: boolean }) => {
      // 票据只能返回给发起请求的子页面，离开游戏后丢弃迟到响应。
      if (source && source === frame.value?.contentWindow) source.postMessage({ type: 'noname-ticket-result', request: event.data.request, ...result }, location.origin);
    };
    try {
      const session = await props.apiCall<{ ticket: string }>('/api/noname/session', 'POST', undefined, 0);
      reply({ ticket: session.ticket });
    } catch { reply({ error: true }); }
  }
  if (event.data?.type === 'noname-rooms' && Array.isArray(event.data.rooms)) rooms.value = event.data.rooms;
  if (event.data?.type === 'noname-state') {
    if (phase.value !== event.data.phase) { pending.value = false; clearTimeout(commandTimer); }
    phase.value = event.data.phase;
    room.value = event.data;
  }
  if (event.data?.type === 'noname-exit') reset();
  if (event.data?.type === 'noname-error') {
    error.value = String(event.data.message); pending.value = false; clearTimeout(commandTimer);
  }
}
onMounted(() => window.addEventListener('message', message));
onBeforeUnmount(() => { window.removeEventListener('message', message); clearTimeout(commandTimer); });
</script>
<template>
  <section class="noname-game">
    <header><strong>三国杀 · 无名杀</strong><span>娱乐模式 · 不扣灵石</span><button class="game-button" @click="emit('back')">返回大厅</button></header>
    <div v-if="!started" class="noname-entry">
      <svg viewBox="0 0 160 140" aria-hidden="true"><path d="M20 30 80 10l60 20v55l-60 45-60-45z" fill="#684877" stroke="#edc278" stroke-width="5"/><path d="m45 35 72 66m-2-67-70 69" stroke="#ffe5a3" stroke-width="8"/><text x="80" y="85" text-anchor="middle" fill="#fff1ca" font-size="42" font-family="serif">杀</text></svg>
      <h2>群雄相聚，各显神通</h2><p>单机对战 AI，或创建房间邀请好友同桌。</p>
      <label><input v-model="accepted" type="checkbox"> 我已阅读并接受下方 GPL-3.0 许可说明</label>
      <div class="noname-modes"><button class="game-button gold" :disabled="busy || !accepted" @click="start('solo')">单机试玩</button><button class="game-button" :disabled="busy || !accepted" @click="start('online')">好友联机</button></div>
      <p class="noname-license">基于无名杀 GPL-3.0，保留原版玩法与界面。<a href="https://github.com/libnoname/noname" target="_blank" rel="noopener">项目源码</a> · <a href="/noname/LICENSE" target="_blank">许可证</a> · <a href="/noname/upstream-source.tar.gz">对应源码</a></p>
      <small>联机由房主主持；房主退出会结束房间。这版不参与灵石结算。</small>
    </div>
    <div v-else class="noname-stage">
      <iframe ref="frame" :class="{ concealed: online && phase !== 'playing' }" src="/noname/" title="三国杀无名杀游戏" allow="autoplay; fullscreen" />
      <div v-if="online && phase !== 'playing'" class="noname-lobby">
        <div class="room-heading"><h2>{{ phase === 'waiting' ? '等候牌友' : '三国杀 · 好友房' }}</h2><button class="game-button" @click="reset">{{ phase === 'waiting' ? '离开房间' : '返回' }}</button></div>
        <p v-if="phase === 'loading'" role="status">正在连接牌桌…</p>
        <template v-else-if="phase === 'lobby'">
          <div class="room-controls"><label>玩法 <select v-model="preset"><option value="classic">经典身份 · 标准包</option><option value="expanded">扩展身份 · 全武将</option></select></label><label>人数 <select v-model="capacity"><option v-for="n in [2, 4, 6, 8]" :key="n" :value="n">{{ n }} 人</option></select></label><button class="game-button gold" :disabled="pending" @click="command('create')">创建房间</button></div>
          <form class="room-controls" @submit.prevent="command('join', roomCode.trim())"><input v-model="roomCode" placeholder="粘贴房间号" aria-label="三国杀房间号" maxlength="80"><button class="game-button" :disabled="pending || !roomCode.trim()">加入房间</button></form>
          <div class="noname-room-list" aria-label="三国杀房间列表"><article v-for="item in rooms" :key="item.id"><div><strong>{{ item.host }} 的房间</strong><p>{{ item.count }}/{{ item.capacity }} 人 · {{ item.playing ? '对局中' : '等待开局' }}</p></div><button class="game-button" :disabled="pending || item.playing || item.count >= item.capacity" @click="command('join', item.id)">入座</button></article><p v-if="!rooms.length">还没有房间，开一桌等朋友吧。</p></div>
        </template>
        <template v-else-if="phase === 'waiting'">
          <div class="room-controls"><span>房间号</span><CopyRoomCode :room-id="room.roomId" /><span>{{ room.players.length }}/{{ room.capacity }} 人</span></div>
          <div class="noname-seats"><div v-for="(name, index) in room.players" :key="index"><span class="seat-token">{{ name.slice(0, 1) }}</span><strong>{{ name }}</strong><small>{{ index === 0 ? '房主' : '已入座' }}</small></div></div>
          <div class="room-controls start-controls"><button v-if="room.host" class="game-button gold" :disabled="pending || room.players.length < 2" @click="command('start')">开始游戏</button><strong v-else>等待房主开局</strong><span>{{ room.players.length < 2 ? '至少两名玩家入座后可以开始' : '空位由电脑补齐' }}</span></div>
        </template>
        <small>免费娱乐 · 房主离开将结束房间 · 入座后由房主开始</small>
      </div>
    </div>
    <p v-if="error" class="noname-error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.noname-game { width:100%; height:100%; display:flex; flex-direction:column; background:#231d2b; color:#ffe8b8; }
header { display:flex; align-items:center; gap:16px; padding:5px max(9%,48px) 5px 14px; min-height:40px; background:#673f46; }
header span { font-size:11px; flex:1; } header button { min-height:36px; font-size:12px; }
iframe { border:0; width:100%; flex:1; min-height:0; background:#302329; }
.noname-stage { position:relative; flex:1; min-height:0; }
.noname-stage iframe { position:absolute; inset:0; height:100%; }
.concealed { visibility:hidden; pointer-events:none; }
.noname-lobby { position:absolute; inset:0; padding:16px max(9%,32px); overflow:auto; background:radial-gradient(ellipse at top,#665074,#281e2a); }
.room-heading,.room-controls { display:flex; align-items:center; justify-content:center; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.room-heading { justify-content:space-between; } .room-heading h2 { font-size:22px; }
.room-controls label { display:flex; align-items:center; gap:6px; font-size:13px; }
select,input { color:#3f3150; background:#fff1d1; border:1px solid #cda773; border-radius:7px; padding:8px; min-height:36px; box-sizing:border-box; }
.noname-room-list { max-width:760px; margin:auto; } .noname-room-list article { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 16px; margin:8px 0; background:#fff1d110; border:1px solid #b4916860; border-radius:10px; }
.noname-room-list p { margin:5px 0; } .noname-lobby > small { display:block; text-align:center; margin-top:14px; opacity:.8; }
.noname-seats { display:flex; flex-wrap:wrap; justify-content:center; gap:16px; margin:16px 0; }
.noname-seats > div { display:flex; flex-direction:column; align-items:center; gap:6px; width:110px; } .noname-seats strong { max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.seat-token { display:grid; place-items:center; width:48px; height:48px; background:#936555; border:2px solid #e6c084; border-radius:50%; font-family:serif; font-size:25px; }
.start-controls { margin-top:20px; } .start-controls span { font-size:12px; }
.noname-entry { flex:1; min-height:0; overflow:auto; text-align:center; padding:18px; background:radial-gradient(ellipse at center,#604467,#281e2a); }
svg { width:120px; height:100px; } h2 { margin:8px; font-family:serif; } p { font-size:13px; } .noname-modes { display:flex; gap:14px; justify-content:center; margin:18px; }
.noname-license { opacity:.85; } a { color:#ffe1a0; } small { font-size:11px; } .noname-error { background:#732f36; margin:0; padding:8px; }
@media(max-height:450px) { .noname-entry { padding:8px; } svg { width:60px; height:50px; } h2 { font-size:19px; } .noname-modes { margin:10px; } p { margin:6px; } }
</style>
