<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { roomInviteUrl, type RoomInvite } from './roomInvites';
const props = defineProps<{ room: RoomInvite; clientId: string; share?: (room: RoomInvite) => Promise<string> }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLDialogElement | null>(null);
const notice = ref('');
const busy = ref(false);
const manual = ref('');
const field = ref<HTMLInputElement | null>(null);
async function copy(value: string, label: string) {
  try { await navigator.clipboard.writeText(value); notice.value = `已复制${label}`; manual.value = ''; }
  catch { manual.value = value; notice.value = '此客户端不允许自动复制，请长按或选中下方内容复制'; }
}
function copyLink() {
  try { void copy(roomInviteUrl(props.clientId, props.room), '邀请链接'); }
  catch (reason) { notice.value = reason instanceof Error ? reason.message : '无法生成邀请链接'; }
}
async function recruit() {
  if (!props.share || busy.value) return;
  busy.value = true;
  try { notice.value = await props.share(props.room); }
  catch { notice.value = '当前客户端未能打开分享，请复制邀请链接或房间号发送给好友'; }
  finally { busy.value = false; }
}
onMounted(() => panel.value?.showModal());
</script>

<template>
  <Teleport to="body"><dialog ref="panel" class="room-invite" aria-labelledby="room-invite-title" @cancel.prevent="emit('close')">
    <header><div><small>好友相聚 · 同桌开局</small><h2 id="room-invite-title">招募队友</h2></div><button class="game-button quiet" @click="emit('close')">关闭</button></header>
    <div class="invite-code"><span>房间号</span><strong>{{ room.room_id }}</strong><button class="game-button" @click="copy(room.room_id, '房间号')">复制房间号</button></div>
    <p>好友点击邀请链接，通过 Discord 打开小活动后自动尝试加入这间房。</p>
    <div class="invite-actions"><button v-if="share" class="game-button gold" :disabled="busy" @click="recruit">{{ busy ? '正在打开…' : '选择好友或频道' }}</button><button class="game-button" @click="copyLink">复制邀请链接</button></div>
    <p v-if="notice" role="status">{{ notice }}</p>
    <input v-if="manual" ref="field" :value="manual" readonly aria-label="手动复制内容" @focus="field?.select()" @click="field?.select()">
    <small>加入不会自动下注。房间已满或关闭时会显示原因。</small>
  </dialog></Teleport>
</template>

<style scoped>
.room-invite { box-sizing: border-box; width: min(510px, calc(100vw - 24px)); max-height: calc(100dvh - 20px); overflow: auto; margin: auto; padding: 20px; border: 3px solid #eac77c; border-radius: 18px; background: url('/ui/guochao/cloud-pattern.svg') center / 180px, #fff0d5; color: #4b3e68; box-shadow: 0 10px 45px #17132899; }
.room-invite::backdrop { background: #171328a6; }
header, .invite-code, .invite-actions { display: flex; align-items: center; gap: 12px; }
header { justify-content: space-between; margin-bottom: 15px; } h2 { margin: 2px 0 0; font-size: 23px; }
.invite-code { flex-wrap: wrap; padding: 12px; background: #fff9eb; border: 1px solid #dcb570; border-radius: 10px; }
.invite-code strong { flex: 1; font-size: 26px; letter-spacing: 3px; }
.room-invite p { font-size: 13px; line-height: 1.5; }.room-invite small { color: #866743; font-size: 11px; }
.room-invite .game-button { min-height: 38px; font-size: 13px; } .room-invite input { box-sizing: border-box; width: 100%; padding: 8px; margin-bottom: 10px; }
@media (max-height: 400px) { .room-invite { padding: 10px 16px; } header { margin-bottom: 7px; } h2 { font-size: 18px; } .invite-code { padding: 7px; } .room-invite p { margin: 8px 0; } }
</style>
