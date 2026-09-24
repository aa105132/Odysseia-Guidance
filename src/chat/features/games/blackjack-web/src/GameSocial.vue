<script setup lang="ts">
import { animate } from 'animejs';
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { playGameVoice, stopGameVoice } from './gameAudio';
import { quickVoiceLines } from './gameVoiceLines';
import { clientPointToActivity } from './activityViewport';

type Member = { user_id: string; username: string; avatar_url?: string; is_bot?: boolean };
type SocialEvent = { event_id: number; user_id: string; username: string; kind: 'chat' | 'interaction'; item_id: string; text: string; target_id?: string | null; target_username?: string | null; timestamp: number };
type Catalog = { chat: { id: string; text: string }[]; interaction: { id: string; text: string }[] };
type Response = { cursor: number; events?: SocialEvent[]; event?: SocialEvent; catalog?: Catalog };
type Notice = { id: number; userId: string; text: string; x: number; y: number; visible: boolean };
type Box = { x: number; y: number; width: number; height: number };
type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
const props = defineProps<{ scopeType: 'table' | 'blackjack' | 'single'; roomId: string; viewerId: string; members: Member[]; apiCall: ApiCall; hideToggle?: boolean }>();
const opened = ref(false);
const targetId = ref<string | null>(null);
const sending = ref(false);
const error = ref('');
const messages = ref<SocialEvent[]>([]);
const catalog = ref<Catalog>({ chat: [...quickVoiceLines], interaction: [{ id: 'tea', text: '倒茶' }, { id: 'flower', text: '鲜花' }, { id: 'incense', text: '烧香' }] });
const menu = ref<HTMLElement | null>(null);
const animations = new Set<ReturnType<typeof animate>>();
const animatedNodes = new Set<HTMLElement>();
const seen = new Set<number>();
const notices = ref<Notice[]>([]);
const noticeLayer = ref<HTMLElement | null>(null);
const noticeTimers = new Map<string, ReturnType<typeof setTimeout>>();
let noticeFrame: number | undefined;
let noticeLayoutAt = 0;
const target = computed(() => props.members.find(member => String(member.user_id) === targetId.value));
let cursor: number | null = null;
let epoch = 0;
let disposed = false;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let localEvent = 0;
let requestPending = false;
function endpoint() { return `/api/game-social/${props.scopeType}/${encodeURIComponent(props.roomId)}`; }
function close() { opened.value = false; targetId.value = null; error.value = ''; }
function openInteraction(userId: string) {
  if (!props.members.some(member => String(member.user_id) === String(userId))) return;
  targetId.value = String(userId); opened.value = false; error.value = '';
  void nextTick(() => menu.value?.querySelector<HTMLButtonElement>('button')?.focus());
}
function openChat() { opened.value = true; targetId.value = null; error.value = ''; }
function toggleChat() { if (opened.value) close(); else openChat(); }
defineExpose({ openInteraction, openChat, toggleChat, chatOpen: opened });
function escape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !opened.value && !targetId.value) return;
  close();
  document.querySelector<HTMLButtonElement>('[aria-controls="game-social-chat"]')?.focus();
}
function clearAnimations(clearNotices = true) {
  animations.forEach(animation => animation.cancel()); animations.clear();
  animatedNodes.forEach(node => node.remove()); animatedNodes.clear();
  if (!clearNotices) return;
  noticeTimers.forEach(timer => clearTimeout(timer)); noticeTimers.clear(); notices.value = [];
  if (noticeFrame !== undefined) cancelAnimationFrame(noticeFrame);
  noticeFrame = undefined;
}
function showNotice(event: SocialEvent) {
  const userId = String(event.user_id);
  if (!avatarBox(userId)) return;
  const text = event.kind === 'chat' ? `${event.username}：${event.text}` : `${event.username} 向 ${event.target_username || '牌友'}${event.text}`;
  // 每个座位只保留最新一条，各座位独立计时，避免说话较快的人挤掉别人。
  notices.value = [...notices.value.filter(notice => notice.userId !== userId), { id: event.event_id, userId, text, x: 0, y: 0, visible: false }];
  clearTimeout(noticeTimers.get(userId));
  noticeTimers.set(userId, setTimeout(() => {
    notices.value = notices.value.filter(notice => notice.userId !== userId);
    noticeTimers.delete(userId);
  }, 3800));
  void nextTick(() => {
    if (disposed || !notices.value.length) return;
    positionNotices();
    if (noticeFrame === undefined) noticeFrame = requestAnimationFrame(followAvatars);
  });
}
function avatarBox(userId: string): Box | null {
  if (!props.members.some(member => String(member.user_id) === userId)) return null;
  const node = Array.from(document.querySelectorAll<HTMLElement>('[data-game-avatar]')).find(item => item.dataset.gameAvatar === userId && item.getClientRects().length > 0);
  if (!node || getComputedStyle(node).visibility === 'hidden') return null;
  const box = node.getBoundingClientRect();
  if (!box.width || !box.height) return null;
  const first = clientPointToActivity(box.left, box.top);
  const last = clientPointToActivity(box.right, box.bottom);
  const result = { x: Math.min(first.x, last.x), y: Math.min(first.y, last.y), width: Math.abs(last.x - first.x), height: Math.abs(last.y - first.y) };
  const surface = document.getElementById('app');
  if (result.x + result.width <= 0 || result.y + result.height <= 0 || result.x >= (surface?.clientWidth || innerWidth) || result.y >= (surface?.clientHeight || innerHeight)) return null;
  return result;
}
function overlap(first: Box, last: Box) {
  return Math.max(0, Math.min(first.x + first.width, last.x + last.width) - Math.max(first.x, last.x))
    * Math.max(0, Math.min(first.y + first.height, last.y + last.height) - Math.max(first.y, last.y));
}
function positionNotices() {
  const surface = document.getElementById('app');
  const width = surface?.clientWidth || innerWidth;
  const height = surface?.clientHeight || innerHeight;
  const avatars = new Map(props.members.map(member => [String(member.user_id), avatarBox(String(member.user_id))]));
  const nodes = new Map(Array.from(noticeLayer.value?.querySelectorAll<HTMLElement>('[data-notice-user]') ?? []).map(node => [node.dataset.noticeUser!, node]));
  const placed: Box[] = [];
  for (const notice of notices.value) {
    const anchor = avatars.get(notice.userId);
    const node = nodes.get(notice.userId);
    if (!anchor || !node) { notice.visible = false; continue; }
    const w = node.offsetWidth, h = node.offsetHeight, gap = 8, edge = 8;
    // 在头像四周择位，先避开头像，再减少气泡重叠；坐标始终使用活动逻辑空间。
    const options = [
      { x: anchor.x + (anchor.width - w) / 2, y: anchor.y - h - gap },
      { x: anchor.x + anchor.width + gap, y: anchor.y + (anchor.height - h) / 2 },
      { x: anchor.x - w - gap, y: anchor.y + (anchor.height - h) / 2 },
      { x: anchor.x + (anchor.width - w) / 2, y: anchor.y + anchor.height + gap },
    ].map((point, preference) => {
      const box = { x: Math.max(edge, Math.min(width - w - edge, point.x)), y: Math.max(edge, Math.min(height - h - edge, point.y)), width: w, height: h };
      const avatarOverlap = [...avatars.values()].reduce((total, avatar) => total + (avatar ? overlap(box, avatar) : 0), 0);
      const bubbleOverlap = placed.reduce((total, other) => total + overlap(box, other), 0);
      return { box, score: avatarOverlap * 100 + bubbleOverlap * 10 + Math.abs(box.x - point.x) + Math.abs(box.y - point.y) + preference };
    });
    options.sort((first, last) => first.score - last.score);
    const best = options[0]!.box;
    notice.x = best.x; notice.y = best.y; notice.visible = true;
    placed.push(best);
  }
  notices.value = notices.value.filter(notice => {
    if (avatars.get(notice.userId)) return true;
    clearTimeout(noticeTimers.get(notice.userId)); noticeTimers.delete(notice.userId);
    return false;
  });
}
function followAvatars(timestamp: number) {
  noticeFrame = undefined;
  if (disposed || !notices.value.length) return;
  // 仅在短暂气泡存活时跟随布局，兼顾自动旋转、窗口缩放和座位变化。
  if (timestamp - noticeLayoutAt >= 80) { positionNotices(); noticeLayoutAt = timestamp; }
  noticeFrame = requestAnimationFrame(followAvatars);
}
function repositionNotices() { if (notices.value.length) void nextTick(positionNotices); }
const interactionArt: Record<string, string> = {
  tea: '<svg viewBox="0 0 120 120" aria-hidden="true"><path d="M28 54h57v24c0 16-57 16-57 0z" fill="#fff4cb" stroke="#a86630" stroke-width="4"/><path d="M85 59h12c19 0 16 24-12 24" fill="none" stroke="#c28b44" stroke-width="6"/><ellipse cx="56" cy="54" rx="29" ry="8" fill="#bd7833"/><path class="pour" d="M47 19q-7 13 8 29" fill="none" stroke="#eac278" stroke-width="5" stroke-linecap="round"/><path class="steam" d="M41 43q-12-12 0-21m24 21q-12-12 0-21" fill="none" stroke="#fff6dd" stroke-width="4" stroke-linecap="round"/><ellipse cx="57" cy="95" rx="42" ry="6" fill="#bd8246"/></svg>',
  flower: '<svg viewBox="0 0 120 120" aria-hidden="true"><path d="M60 55v55m0-17q-36-7-30-25 21-1 30 25m0-10q28-4 29-21-20-2-29 21" fill="#598654" stroke="#416741" stroke-width="3"/><g class="petals" fill="#e68192" stroke="#b94969" stroke-width="2"><ellipse cx="60" cy="26" rx="15" ry="23"/><ellipse cx="83" cy="44" rx="23" ry="15" transform="rotate(-30 83 44)"/><ellipse cx="76" cy="66" rx="15" ry="23" transform="rotate(-35 76 66)"/><ellipse cx="43" cy="64" rx="15" ry="23" transform="rotate(35 43 64)"/><ellipse cx="35" cy="40" rx="23" ry="15" transform="rotate(30 35 40)"/></g><circle cx="59" cy="47" r="15" fill="#f1ca6e"/></svg>',
  incense: '<svg viewBox="0 0 120 120" aria-hidden="true"><path d="M34 79h52l-9 28H43z" fill="#ad7047" stroke="#724729" stroke-width="4"/><ellipse cx="60" cy="80" rx="26" ry="7" fill="#e7c395"/><path d="M49 80V46m11 34V37m11 43V47" stroke="#78493a" stroke-width="4"/><path d="M49 46v-6m11-3v-6m11 16v-6" stroke="#ee9861" stroke-width="4"/><path class="steam" d="M49 34C23 8 73 25 47 0m13 28C84 5 39 17 66-5m5 40C92 19 61 24 79 2" fill="none" stroke="#e9e3d9" stroke-width="4" stroke-linecap="round"/></svg>',
};
function avatarCenter(userId: string | null | undefined) {
  const node = Array.from(document.querySelectorAll<HTMLElement>('[data-game-avatar]')).find(item => item.dataset.gameAvatar === String(userId));
  if (!node) return null;
  const box = node.getBoundingClientRect();
  return clientPointToActivity(box.x + box.width / 2, box.y + box.height / 2);
}
function animateInteraction(event: SocialEvent) {
  showNotice(event);
  if (document.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const art = interactionArt[event.item_id];
  if (!art) return;
  if (animatedNodes.size >= 4) clearAnimations(false);
  const surface = document.getElementById('app');
  const width = surface?.clientWidth || innerWidth;
  const height = surface?.clientHeight || innerHeight;
  const start = avatarCenter(event.user_id) || { x: width - 40, y: height / 2 };
  const end = avatarCenter(event.target_id) || { x: width / 2, y: height / 2 };
  const node = document.createElement('div');
  node.className = 'game-social-animation';
  Object.assign(node.style, { position: 'fixed', left: `${start.x - 42}px`, top: `${start.y - 42}px`, width: '84px', height: '84px', zIndex: '10020', pointerEvents: 'none', filter: 'drop-shadow(0 5px 8px #31221d77)' });
  node.innerHTML = art; (surface || document.body).append(node); animatedNodes.add(node);
  const finish = () => { node.remove(); animatedNodes.delete(node); animations.delete(flight); };
  const flight = animate(node, { x: [{ to: (end.x - start.x) / 2, duration: 400 }, { to: end.x - start.x, duration: 400 }], y: [{ to: Math.min(0, end.y - start.y) - 75, duration: 400 }, { to: end.y - start.y, duration: 400 }], scale: [{ to: 1.2, duration: 400 }, { to: 1, duration: 400 }, { to: 1, duration: 1100 }, { to: 0.8, duration: 300 }], opacity: [{ to: 1, duration: 1900 }, { to: 0, duration: 300 }], ease: 'inOutSine', onComplete: finish });
  animations.add(flight);
  const detail = animate(node.querySelectorAll('.steam,.pour,.petals'), { y: [-3, -10], opacity: [.6, 1], alternate: true, loop: 3, duration: 350, onComplete: () => animations.delete(detail) });
  animations.add(detail);
}
function accept(events: SocialEvent[], silent = false) {
  for (const event of events) {
    if (seen.has(event.event_id)) continue;
    seen.add(event.event_id);
    if (seen.size > 300) seen.delete(seen.values().next().value!);
    messages.value = [...messages.value.slice(-29), event];
    if (silent || document.hidden || Date.now() / 1000 - event.timestamp > 10) continue;
    if (event.kind === 'chat') { showNotice(event); void playGameVoice(event.item_id); }
    else animateInteraction(event);
  }
}
function schedule() { clearTimeout(pollTimer); if (!disposed && props.scopeType !== 'single') pollTimer = setTimeout(() => void poll(), 1500); }
async function poll() {
  if (disposed || props.scopeType === 'single') return;
  if (document.hidden || requestPending) { schedule(); return; }
  const token = epoch;
  const initializing = cursor === null;
  requestPending = true;
  try {
    const response = await props.apiCall<Response>(`${endpoint()}${initializing ? '' : `?after=${cursor}`}`, 'GET', undefined, 0);
    if (disposed || token !== epoch) return;
    cursor = Math.max(cursor ?? 0, response.cursor);
    if (response.catalog) catalog.value = response.catalog;
    if (!initializing) accept(response.events || []);
  } catch { /* 轮询短暂失败由下一次重试，避免遮挡牌局。 */ }
  finally { if (token === epoch) { requestPending = false; schedule(); } }
}
async function send(kind: 'chat' | 'interaction', itemId: string) {
  if (sending.value) return;
  const token = epoch;
  sending.value = true; error.value = '';
  try {
    if (props.scopeType === 'single') {
      const item = (kind === 'chat' ? catalog.value.chat : catalog.value.interaction).find(entry => entry.id === itemId);
      if (!item) return;
      const self = props.members.find(member => String(member.user_id) === String(props.viewerId));
      accept([{ event_id: ++localEvent, user_id: props.viewerId, username: self?.username || '你', kind, item_id: itemId, text: item.text, target_id: targetId.value, target_username: target.value?.username || '月月', timestamp: Date.now() / 1000 }]);
    } else {
      const response = await props.apiCall<Response>(endpoint(), 'POST', { kind, item_id: itemId, ...(kind === 'interaction' ? { target_id: targetId.value } : {}) }, 0);
      if (disposed || token !== epoch) return;
      accept(response.events || (response.event ? [response.event] : []));
      // 发送不推进轮询游标，否则会漏掉发送前尚未拉取的其他玩家消息。
    }
    targetId.value = null;
  } catch (reason) { if (!disposed && token === epoch) error.value = reason instanceof Error ? reason.message : '暂时无法发送，请稍后重试'; }
  finally { if (token === epoch) sending.value = false; }
}
function reset() { epoch++; cursor = null; requestPending = false; sending.value = false; seen.clear(); messages.value = []; clearAnimations(); stopGameVoice(); close(); clearTimeout(pollTimer); void poll(); }
function visibility() {
  // 页面隐藏时废弃在途响应，恢复只取新游标，防止积压事件突然补播。
  epoch++; requestPending = false; sending.value = false; clearTimeout(pollTimer);
  if (document.hidden) { clearAnimations(); stopGameVoice(); }
  else { cursor = null; void poll(); }
}
watch(() => [props.scopeType, props.roomId, props.viewerId], reset);
onMounted(() => { document.addEventListener('keydown', escape); document.addEventListener('visibilitychange', visibility); window.addEventListener('resize', repositionNotices); window.visualViewport?.addEventListener('resize', repositionNotices); void poll(); });
onBeforeUnmount(() => { disposed = true; epoch++; clearTimeout(pollTimer); clearAnimations(); stopGameVoice(); document.removeEventListener('keydown', escape); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('resize', repositionNotices); window.visualViewport?.removeEventListener('resize', repositionNotices); });
</script>

<template>
  <Teleport to="#app">
  <div class="game-social" :class="{ 'has-toggle': !hideToggle }">
    <div ref="noticeLayer" class="social-notices" aria-live="polite"><p v-for="notice in notices" :key="notice.id" :data-notice-user="notice.userId" :style="{ left: `${notice.x}px`, top: `${notice.y}px`, visibility: notice.visible ? 'visible' : 'hidden' }">{{ notice.text }}</p></div>
    <section v-if="opened" id="game-social-chat" class="social-panel" aria-label="牌桌聊天"><header><strong>牌桌聊天</strong><button aria-label="收起聊天" @click="close">收起</button></header><div class="social-messages" aria-label="最近消息"><p v-if="!messages.length">选一句招呼，和牌友聊聊吧。</p><p v-for="message in messages.slice(-5)" :key="message.event_id"><b>{{ message.username }}</b> {{ message.kind === 'interaction' ? `向 ${message.target_username || '牌友'}` : '：' }}{{ message.text }}</p></div><div class="social-quick"><button v-for="line in catalog.chat" :key="line.id" :disabled="sending" @click="send('chat', line.id)">{{ line.text }}</button></div><label class="social-target">互动对象 <select aria-label="互动对象" @change="openInteraction(($event.target as HTMLSelectElement).value)"><option value="">选择牌友</option><option v-for="member in members.filter(item => String(item.user_id) !== String(viewerId))" :key="member.user_id" :value="member.user_id">{{ member.username }}</option></select></label><p v-if="error" class="social-error" role="alert">{{ error }}</p></section>
    <section v-if="target" ref="menu" class="social-panel interaction-menu" role="dialog" aria-label="牌友互动"><header><strong>向 {{ target.username }} 送出</strong><button aria-label="关闭互动菜单" @click="targetId = null">关闭</button></header><div class="interaction-options"><button v-for="item in catalog.interaction" :key="item.id" :disabled="sending" @click="send('interaction', item.id)"><span class="interaction-icon" v-html="interactionArt[item.id] || ''"></span>{{ item.text }}</button></div><small>桌边心意，免费互动</small><p v-if="error" class="social-error" role="alert">{{ error }}</p></section>
    <button v-if="!hideToggle" class="social-toggle" :aria-expanded="opened" aria-controls="game-social-chat" aria-label="打开牌桌聊天" @click="toggleChat">聊天</button>
  </div>
  </Teleport>
</template>

<style scoped>
.game-social { position: absolute; inset: 0; z-index: 10000; font-size: 13px; color: #35434e; pointer-events: none; --social-edge: 14px; --social-bottom: 32px; }
.game-social.has-toggle { --social-bottom: 78px; }
.game-social > .social-toggle, .game-social > .social-panel { pointer-events: auto; }
.game-social button { cursor: pointer; min-height: 34px; font: inherit; color: inherit; border: 1px solid #c6b9a1; border-radius: 9px; background: #f8f3e9; box-shadow: none; text-shadow: none; padding: 7px 10px; }
.game-social button:disabled { opacity: .6; cursor: default; }
.game-social button:focus-visible, .game-social select:focus-visible { outline: 3px solid #477ca3; outline-offset: 2px; }
.game-social .social-toggle { position: absolute; right: var(--social-edge); bottom: 32px; border: 1px solid #a9b5bf; color: #fff8df; background: linear-gradient(#657d90, #3e546d); border-radius: 20px; box-shadow: 0 3px 8px #0005; }
.game-social > .social-panel { position: absolute; left: auto; top: auto; right: var(--social-edge); bottom: var(--social-bottom); margin: 0; transform: none; width: min(330px, calc(var(--activity-width, 100vw) - 28px)); max-height: min(430px, calc(var(--activity-height, 100dvh) - var(--social-bottom) - 14px)); overflow-y: auto; padding: 12px; box-sizing: border-box; border: 1px solid #b6a58b; border-radius: 15px; background: #eee7d9fa; box-shadow: 0 12px 40px #1e172777; }
.social-panel header { display: flex; gap: 8px; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.social-panel header strong { overflow-wrap: anywhere; }
.social-panel header button { padding: 4px 8px; flex: none; }
.social-messages { border-bottom: 1px solid #d9be90; margin-bottom: 8px; color: #756447; max-height: 95px; overflow-y: auto; font-size: 12px; }
.social-messages p { margin: 5px 0; overflow-wrap: anywhere; }
.social-quick { display: grid; grid-template-columns: 1fr; gap: 6px; }
.social-quick button { text-align: left; }
.social-target { display: flex; align-items: center; gap: 6px; margin-top: 12px; font-size: 12px; }
.social-target select { flex: 1; min-width: 0; min-height: 34px; border-radius: 8px; border: 1px solid #c6b9a1; color: inherit; background: #fffaf0; padding: 4px; }
.social-notices { position: absolute; inset: 0; pointer-events: none; }
.social-notices p { position: absolute; width: max-content; max-width: min(240px, calc(var(--activity-width, 100vw) - 16px)); max-height: calc(var(--activity-height, 100dvh) - 16px); overflow: hidden; margin: 0; padding: 7px 10px; border-radius: 12px; border: 1px solid #c6b9a1; background: #f4eee1fa; box-shadow: 0 3px 10px #1e172744; line-height: 1.5; overflow-wrap: anywhere; text-align: left; pointer-events: none; }
.interaction-options { display: flex; gap: 7px; }
.interaction-options button { flex: 1; padding: 6px; display: flex; flex-direction: column; align-items: center; gap: 5px; }
.interaction-icon { width: 60px; height: 60px; display: block; }
.interaction-icon :deep(svg) { width: 100%; height: 100%; }
.interaction-menu small { display: block; margin-top: 10px; color: #86704b; text-align: center; }
.social-error { color: #a03743; line-height: 1.4; }
@container activity-viewport (max-height: 450px) { .game-social { --social-edge: 8px; --social-bottom: 30px; } .game-social.has-toggle { --social-bottom: 72px; } .game-social > .social-panel { width: min(300px, calc(var(--activity-width, 100vw) - 80px)); padding: 10px; } .social-quick { grid-template-columns: 1fr 1fr; } .social-quick button { font-size: 11px; padding: 5px 7px; line-height: 1.4; } .social-messages { max-height: 55px; } .social-notices { font-size: 11px; } .social-notices p { max-width: min(190px, calc(var(--activity-width, 100vw) - 16px)); padding: 5px 8px; } }
@media (prefers-reduced-motion: reduce) { .game-social * { scroll-behavior: auto; } }
</style>
