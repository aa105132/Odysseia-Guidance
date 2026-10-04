<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import SpiritStoneIcon from './SpiritStoneIcon.vue';
import './guochao-dialog.css';
import type { CropQuality } from './farmTypes';
type Material = { crop_id: string; quality: CropQuality; quantity: number; crop_name: string; quality_label: string };
type Mail = { id: number; title: string; body: string; amount: number; materials?: Material[]; created_at: string; read_at: string | null; claimed_at: string | null };
type Inbox = { messages: Mail[]; unread_count: number; claimable_count: number; next_before_id: number | null };
const props = defineProps<{ userId: string | number; apiCall: <T>(path: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T> }>();
const emit = defineEmits<{ balance: [value: number] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const opened = ref(false), busy = ref(false), loading = ref(false);
const messages = ref<Mail[]>([]), selected = ref<Mail | null>(null);
const unread = ref(0), claimable = ref(0), next = ref<number | null>(null);
const error = ref(''), notice = ref('');
let epoch = 0;
const badge = computed(() => unread.value || claimable.value);
const format = (n: number) => n.toLocaleString('zh-CN');
const hasAttachments = (mail: Mail) => mail.amount > 0 || !!mail.materials?.length;
const materialLabel = (item: Material) => `${item.crop_name}（${item.quality_label}）× ${format(item.quantity)}`;
const attachmentLabel = (mail: Mail) => [mail.amount > 0 ? `${format(mail.amount)} 灵石` : '', ...(mail.materials ?? []).map(materialLabel)].filter(Boolean).join('、');
const date = (s: string) => { const d = new Date(s.includes('T') ? s : s.replace(' ', 'T') + 'Z'); return Number.isNaN(d.valueOf()) ? s : d.toLocaleString('zh-CN', { hour12: false }); };
function replaceMail(mail: Mail) {
  messages.value = messages.value.map(m => m.id === mail.id ? mail : m);
  if (selected.value?.id === mail.id) selected.value = mail;
}
async function load(append = false, quiet = false) {
  if (busy.value || loading.value) return;
  const id = ++epoch;
  loading.value = true;
  if (!quiet) error.value = '';
  try {
    const data = await props.apiCall<Inbox>(`/api/mail?limit=20${append && next.value ? `&before_id=${next.value}` : ''}`, 'GET', undefined, 0);
    if (id !== epoch) return;
    messages.value = append ? [...messages.value, ...data.messages.filter(m => !messages.value.some(old => old.id === m.id))] : data.messages;
    unread.value = data.unread_count; claimable.value = data.claimable_count; next.value = data.next_before_id;
  } catch (e) { if (id === epoch && !quiet) error.value = e instanceof Error ? e.message : '邮件加载失败，请重试'; }
  finally { if (id === epoch) loading.value = false; }
}
async function open() {
  opened.value = true; selected.value = null; error.value = ''; notice.value = '';
  await nextTick(); dialog.value?.showModal(); await load();
}
function close() {
  if (busy.value) return;
  ++epoch; loading.value = false; opened.value = false;
  dialog.value?.close(); trigger.value?.focus();
}
async function read(mail: Mail) {
  if (busy.value || loading.value) return;
  selected.value = mail; error.value = ''; notice.value = '';
  if (mail.read_at) return;
  busy.value = true; const id = epoch;
  try {
    const data = await props.apiCall<{ message: Mail }>(`/api/mail/${mail.id}/read`, 'POST', undefined, 0);
    if (id !== epoch) return;
    replaceMail(data.message); unread.value = Math.max(0, unread.value - 1);
  } catch (e) { if (id === epoch) error.value = e instanceof Error ? e.message : '标记已读失败，请重试'; }
  finally { if (id === epoch) busy.value = false; }
}
async function claim() {
  if (busy.value || loading.value || !selected.value || selected.value.claimed_at || !hasAttachments(selected.value)) return;
  const mail = selected.value; const id = epoch;
  busy.value = true; error.value = ''; notice.value = '';
  try {
    const data = await props.apiCall<{ message: Mail; balance: number; already_claimed: boolean }>(`/api/mail/${mail.id}/claim`, 'POST', undefined, 0);
    if (id !== epoch) return;
    replaceMail(data.message); claimable.value = Math.max(0, claimable.value - 1);
    if (!mail.read_at) unread.value = Math.max(0, unread.value - 1);
    emit('balance', data.balance);
    notice.value = data.already_claimed ? '这份附件已经领取，没有重复入账。' : `${attachmentLabel(data.message)}已到账${data.message.materials?.length ? '，灵草已存入灵圃背包' : ''}。`;
  } catch (e) { if (id === epoch) error.value = e instanceof Error ? e.message : '领取失败，请重试；不会重复入账'; }
  finally { if (id === epoch) busy.value = false; }
}
watch(() => props.userId, () => {
  ++epoch; busy.value = false; loading.value = false; opened.value = false; dialog.value?.close();
  messages.value = []; selected.value = null; unread.value = 0; claimable.value = 0; next.value = null;
  void load(false, true);
}, { immediate: true });
onBeforeUnmount(() => { ++epoch; dialog.value?.close(); });
</script>

<template>
  <button ref="trigger" type="button" class="game-button quiet mail-trigger" aria-label="打开邮箱" @click="open">
    <img class="mail-trigger-icon" src="/ui/currency/mail-envelope.webp" alt="" aria-hidden="true" draggable="false" />
    <span>邮箱</span><b v-if="badge" class="mail-badge">{{ badge > 99 ? '99+' : badge }}</b>
  </button>
  <Teleport to="body">
    <dialog ref="dialog" class="game-mail-dialog gc-dialog" aria-labelledby="mail-heading" @cancel.prevent="close" @click="($event.target === dialog) && close()">
      <section v-if="opened" class="mail-shell">
        <header class="mail-heading gc-heading"><span class="gc-seal" aria-hidden="true">信</span><div><small>月月茶馆 · 来信</small><h2 id="mail-heading">我的邮箱</h2></div><button class="gc-close" type="button" :disabled="busy" aria-label="关闭邮箱" @click="close">关闭</button></header>
        <div v-if="error" class="mail-error" role="alert">{{ error }} <button v-if="!selected" :disabled="busy || loading" @click="load()">重试</button><button v-else-if="!selected.read_at" :disabled="busy" @click="read(selected)">重试已读</button></div>
        <p v-if="notice" class="mail-notice" role="status">{{ notice }}</p>
        <article v-if="selected" class="mail-detail gc-paper">
          <button class="mail-back" :disabled="busy" @click="selected = null; error = ''; notice = ''">返回邮件列表</button>
          <h3>{{ selected.title }}</h3><time>{{ date(selected.created_at) }}</time>
          <p class="mail-body">{{ selected.body }}</p>
          <div v-if="hasAttachments(selected)" class="mail-attachment"><div>
            <template v-if="selected.amount > 0"><small>附件 · 灵石</small><strong><SpiritStoneIcon />{{ format(selected.amount) }}</strong><p>补偿不计入游戏盈利排行榜</p></template>
            <template v-if="selected.materials?.length"><small>附件 · 灵草</small><ul class="mail-materials"><li v-for="item in selected.materials" :key="`${item.crop_id}:${item.quality}`">{{ materialLabel(item) }}</li></ul><p>{{ selected.claimed_at ? '灵草已存入灵圃背包' : '领取后存入灵圃背包' }}</p></template>
          </div><button :disabled="busy || !!selected.claimed_at" @click="claim">{{ selected.claimed_at ? '已领取' : busy ? '处理中…' : '领取附件' }}</button></div>
        </article>
        <div v-else class="mail-list gc-paper" :aria-busy="loading">
          <p v-if="loading && !messages.length" role="status">正在取信…</p>
          <p v-else-if="!messages.length && !error" class="mail-empty">暂无来信。补偿和系统通知会送到这里。</p>
          <button v-for="mail in messages" :key="mail.id" class="mail-row" :disabled="loading || busy" @click="read(mail)">
            <span class="mail-seal" :class="{ unread: !mail.read_at }" aria-hidden="true">信</span><span class="mail-summary"><strong>{{ mail.title }}</strong><time>{{ date(mail.created_at) }}</time><span v-if="hasAttachments(mail)"><SpiritStoneIcon v-if="mail.amount > 0" />{{ attachmentLabel(mail) }} · {{ mail.claimed_at ? '已领取' : '待领取' }}</span></span><span v-if="!mail.read_at" class="mail-unread">未读</span>
          </button>
          <button v-if="next" class="mail-more" :disabled="loading || busy" @click="load(true)">{{ loading ? '加载中…' : '更早的邮件' }}</button>
          <button class="mail-refresh" :disabled="loading || busy" @click="load()">刷新邮箱</button>
        </div>
      </section>
    </dialog>
  </Teleport>
</template>

<style scoped>
.mail-trigger { position: relative; display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; }
.mail-trigger-icon { width: 1.35em; height: 1.35em; object-fit: contain; flex: 0 0 1.35em; }
.mail-badge { position: absolute; top: -7px; right: -5px; min-width: 19px; height: 19px; padding: 0 4px; border-radius: 12px; background: #a6372f; color: #fff; font-size: 11px; line-height: 19px; }
.mail-materials { margin: 8px 0; padding-left: 1.2em; overflow-wrap: anywhere; }
.mail-materials li + li { margin-top: 4px; }
@media (max-width: 600px) { .mail-trigger { padding: 8px; } .mail-trigger > span { display: none; } }
</style>
