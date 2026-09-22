<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
const props = defineProps<{ username: string; apiCall: <T>(path: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T> }>();
const emit = defineEmits<{ back: [] }>();
const frame = ref<HTMLIFrameElement>();
const started = ref(false);
const busy = ref(false);
const error = ref('');
const accepted = ref(false);
let launch: { type: string; username: string; mode: string };
async function start(mode: string) {
  busy.value = true; error.value = '';
  try {
    const status = await props.apiCall<{ available: boolean }>('/api/noname/status', 'GET');
    if (!status.available) throw new Error('三国杀资源正在准备，请稍后再试');
    launch = { type: 'noname-launch', username: props.username, mode };
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
  if (event.data?.type === 'noname-error') error.value = `游戏加载异常：${event.data.message}`;
}
onMounted(() => window.addEventListener('message', message));
onBeforeUnmount(() => window.removeEventListener('message', message));
</script>
<template>
  <section class="noname-game">
    <header><strong>三国杀 · 无名杀</strong><span>娱乐模式 · 不扣灵石</span><button class="game-button" @click="emit('back')">返回大厅</button></header>
    <div v-if="!started" class="noname-entry">
      <svg viewBox="0 0 160 140" aria-hidden="true"><path d="M20 30 80 10l60 20v55l-60 45-60-45z" fill="#684877" stroke="#edc278" stroke-width="5"/><path d="m45 35 72 66m-2-67-70 69" stroke="#ffe5a3" stroke-width="8"/><text x="80" y="85" text-anchor="middle" fill="#fff1ca" font-size="42" font-family="serif">杀</text></svg>
      <h2>群雄相聚，各显神通</h2><p>单机对战 AI，或进入联机大厅与好友同桌。</p>
      <label><input v-model="accepted" type="checkbox"> 我已阅读并接受下方 GPL-3.0 许可说明</label>
      <div class="noname-modes"><button class="game-button gold" :disabled="busy || !accepted" @click="start('solo')">单机试玩</button><button class="game-button" :disabled="busy || !accepted" @click="start('online')">好友联机</button></div>
      <p class="noname-license">基于无名杀 GPL-3.0，保留原版玩法与界面。<a href="https://github.com/libnoname/noname" target="_blank" rel="noopener">项目源码</a> · <a href="/noname/LICENSE" target="_blank">许可证</a> · <a href="/noname/upstream-source.tar.gz">对应源码</a></p>
      <small>联机由房主主持；房主退出会结束房间。这版不参与灵石结算。</small>
    </div>
    <iframe v-else ref="frame" src="/noname/" title="三国杀无名杀游戏" allow="autoplay; fullscreen" />
    <p v-if="error" class="noname-error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.noname-game { width:100%; height:100%; display:flex; flex-direction:column; background:#231d2b; color:#ffe8b8; }
header { display:flex; align-items:center; gap:16px; padding:5px max(9%,48px) 5px 14px; min-height:40px; background:#673f46; }
header span { font-size:11px; flex:1; } header button { min-height:36px; font-size:12px; }
iframe { border:0; width:100%; flex:1; min-height:0; background:#302329; }
.noname-entry { flex:1; min-height:0; overflow:auto; text-align:center; padding:18px; background:radial-gradient(ellipse at center,#604467,#281e2a); }
svg { width:120px; height:100px; } h2 { margin:8px; font-family:serif; } p { font-size:13px; } .noname-modes { display:flex; gap:14px; justify-content:center; margin:18px; }
.noname-license { opacity:.85; } a { color:#ffe1a0; } small { font-size:11px; } .noname-error { background:#732f36; margin:0; padding:8px; }
@media(max-height:450px) { .noname-entry { padding:8px; } svg { width:60px; height:50px; } h2 { font-size:19px; } .noname-modes { margin:10px; } p { margin:6px; } }
</style>
