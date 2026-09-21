<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import GameIcon from './GameIcon.vue';

type ListedRoom = {
  room_id: string; game_type: string; host_username: string; player_count: number;
  max_players: number; state: string; can_join: boolean; room_tier: string;
  base_stake: number | null; entry_min: number; loss_limit: number | null; is_member: boolean;
};
type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
const props = defineProps<{ apiCall: ApiCall; balance: number; gameType?: string; restrictGame?: boolean; joinRoom: (room: ListedRoom) => Promise<boolean> }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLDialogElement | null>(null);
const rooms = ref<ListedRoom[]>([]);
const loading = ref(false);
const joining = ref('');
const error = ref('');
const filter = ref(props.gameType ?? '');
const onlyOpen = ref(false);
const offset = ref(0);
const total = ref(0);
let disposed = false;
let sequence = 0;
let timer: ReturnType<typeof setInterval> | undefined;
const names: Record<string, string> = { blackjack: '21点', texas: '德州扑克', golden_flower: '炸金花', landlord: '斗地主', mahjong: '基础麻将', sichuan_mahjong: '四川血战' };
const tiers: Record<string, string> = { beginner: '初级场', intermediate: '中级场', advanced: '高级场', custom: '自定义' };
const visibleRooms = computed(() => rooms.value.filter(room => (!filter.value || room.game_type === filter.value) && (!onlyOpen.value || canJoin(room))));
function icon(game: string) { return (game === 'sichuan_mahjong' ? 'mahjong' : game) as 'blackjack' | 'texas' | 'golden_flower' | 'landlord' | 'mahjong'; }
function canJoin(room: ListedRoom) { return room.can_join && (room.is_member || props.balance >= room.entry_min); }
function buttonLabel(room: ListedRoom) {
  if (joining.value === room.room_id) return '入座中…';
  if (room.is_member) return '返回牌桌';
  if (room.state === 'playing' || room.state === 'dealer_turn') return '对局中';
  if (!room.can_join) return room.player_count >= room.max_players ? '已满员' : '暂不可加入';
  return props.balance < room.entry_min ? '灵石不足' : '入座';
}
async function refresh() {
  if (loading.value || joining.value || disposed) return;
  const current = ++sequence;
  loading.value = true;
  try {
    const query = new URLSearchParams({ offset: String(offset.value), limit: '100' });
    if (filter.value) query.set('game_type', filter.value);
    const data = await props.apiCall<{ rooms: ListedRoom[]; total: number }>(`/api/rooms?${query}`, 'GET', undefined, 0);
    if (!disposed && current === sequence) { rooms.value = data.rooms; total.value = data.total; error.value = ''; }
  } catch (reason) {
    if (!disposed && current === sequence) error.value = reason instanceof Error ? reason.message : '房间列表暂时无法加载';
  } finally { if (!disposed && current === sequence) loading.value = false; }
}
async function join(room: ListedRoom) {
  if (joining.value || !canJoin(room)) return;
  joining.value = room.room_id;
  error.value = '';
  try {
    if (await props.joinRoom(room)) emit('close');
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '入座失败，请刷新房间列表';
  } finally { joining.value = ''; }
}
function close() { if (!joining.value) emit('close'); }
watch(filter, () => { offset.value = 0; void refresh(); });
function changePage(direction: number) { offset.value = Math.max(0, offset.value + direction * 100); void refresh(); }
onMounted(() => { panel.value?.showModal(); void refresh(); timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000); });
onBeforeUnmount(() => { disposed = true; sequence++; clearInterval(timer); panel.value?.close(); });
</script>

<template>
  <Teleport to="body">
    <dialog ref="panel" class="room-directory" aria-labelledby="room-directory-title" @cancel.prevent="close">
      <header class="directory-heading"><GameIcon name="room" /><div><small>月月茶楼 · 寻桌入席</small><h2 id="room-directory-title">房间列表</h2></div><button class="game-button quiet" :disabled="Boolean(joining)" aria-label="关闭房间列表" @click="close">关闭</button></header>
      <div class="directory-filters"><label>玩法 <select v-model="filter" :disabled="loading || Boolean(joining) || restrictGame" aria-label="筛选房间玩法"><option value="">全部玩法</option><option v-for="(name, id) in names" :key="id" :value="id">{{ name }}</option></select></label><label class="directory-open"><input v-model="onlyOpen" type="checkbox">只看可加入</label><button class="game-button" :disabled="loading || Boolean(joining)" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button></div>
      <p v-if="error" class="directory-error" role="alert">{{ error }}</p>
      <div class="directory-list" :aria-busy="loading" aria-label="公开多人房间">
        <article v-for="room in visibleRooms" :key="room.room_id" class="directory-room" :data-room-id="room.room_id">
          <GameIcon :name="icon(room.game_type)" />
          <div class="directory-room-info"><h3>{{ names[room.game_type] }} <span>{{ tiers[room.room_tier] ?? '自由下注' }}</span></h3><p class="directory-host" :title="room.host_username">{{ room.host_username }} 的房间 · {{ room.room_id }}</p><p class="directory-terms"><span>{{ room.player_count }}/{{ room.max_players }} 人</span><span v-if="room.base_stake">底分 {{ room.base_stake }}</span><span>准入 {{ room.entry_min }} 灵石</span><span v-if="room.loss_limit">单局上限 {{ room.loss_limit }}</span></p></div>
          <button class="game-button gold" :disabled="Boolean(joining) || !canJoin(room)" :aria-label="`${buttonLabel(room)} ${room.room_id}`" @click="join(room)">{{ buttonLabel(room) }}</button>
        </article>
        <div v-if="!visibleRooms.length" class="directory-empty" role="status"><GameIcon name="friends" /><strong>{{ loading ? '正在找桌…' : '暂时没有合适的房间' }}</strong><p>换个玩法看看，或返回大厅开一桌。</p></div>
      </div>
      <nav v-if="total > 100 || offset" class="directory-pages" aria-label="房间列表分页"><button class="game-button" :disabled="!offset || loading || Boolean(joining)" @click="changePage(-1)">上一页</button><span>第 {{ offset / 100 + 1 }} 页 · 共 {{ total }} 桌</span><button class="game-button" :disabled="offset + 100 >= total || loading || Boolean(joining)" @click="changePage(1)">下一页</button></nav>
      <footer>多人房间对大厅公开 · 每 5 秒更新 · 入座不会自动下注</footer>
    </dialog>
  </Teleport>
</template>

<style scoped>
.room-directory { box-sizing: border-box; width: min(850px, calc(100vw - 28px)); max-width: none; height: min(660px, calc(100dvh - 24px)); max-height: none; margin: auto; padding: 18px; border: 3px solid #efc882; border-radius: 20px; color: #463e69; background: url('/ui/guochao/cloud-pattern.svg') center / 190px, linear-gradient(125deg, #fff3d9, #eed3ac); box-shadow: inset 0 0 0 2px #b98557, 0 14px 65px #17152bb0; }
.room-directory[open] { display: flex; flex-direction: column; gap: 12px; }
.room-directory::backdrop { background: #151125b5; }
.directory-heading { display: flex; align-items: center; gap: 12px; flex: none; }
.directory-heading > .game-icon { width: 58px; height: 46px; }
.directory-heading > div { flex: 1; }
.directory-heading small { color: #94623e; font-size: 11px; letter-spacing: 2px; }
.directory-heading h2 { margin: 2px 0 0; font-size: 24px; }
.directory-filters { display: flex; align-items: center; gap: 16px; flex: none; font-size: 13px; }
.directory-filters label { display: flex; align-items: center; gap: 6px; }
.directory-filters select { border: 1px solid #b28b66; border-radius: 8px; padding: 8px; background: #fff8e8; color: #493d67; max-width: 180px; }
.directory-open { flex: 1; }
.directory-list { overflow-y: auto; min-height: 0; flex: 1; padding: 2px 4px 8px 0; overscroll-behavior: contain; }
.directory-room { display: flex; align-items: center; gap: 12px; padding: 12px; margin-bottom: 9px; border: 1px solid #c8a06a; border-radius: 12px; background: #fff8e8cc; box-shadow: 0 2px 0 #c69c6740; }
.directory-room > .game-icon { width: 66px; height: 63px; object-fit: contain; flex: none; }
.directory-room-info { flex: 1; min-width: 0; }
.directory-room h3 { margin: 0 0 5px; font-size: 16px; }
.directory-room h3 span { display: inline-block; margin-left: 5px; color: #997140; font-size: 11px; font-weight: 500; }
.directory-host { margin: 0 0 5px; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.directory-terms { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 0; color: #776441; font-size: 11px; }
.directory-room > button { flex: none; min-width: 82px; }
.room-directory .game-button { min-height: 38px; font-size: 13px; }
.directory-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100%; gap: 6px; padding: 12px; box-sizing: border-box; }
.directory-empty .game-icon { width: 95px; height: 76px; }
.directory-empty p, .room-directory footer { margin: 0; color: #846546; font-size: 11px; }
.directory-error { color: #a12a31; margin: 0; font-size: 12px; }
.directory-pages { display: flex; justify-content: center; align-items: center; gap: 12px; font-size: 12px; }
@media (max-height: 450px) { .room-directory { padding: 10px 14px; border-radius: 14px; } .room-directory[open] { gap: 6px; } .directory-heading h2 { font-size: 18px; } .directory-heading > .game-icon { height: 36px; width: 43px; } .directory-heading small { font-size: 9px; } .directory-room { padding: 8px; gap: 9px; } .directory-room > .game-icon { width: 45px; height: 44px; } .directory-room h3 { font-size: 14px; } }
@media (max-width: 620px) { .directory-filters { gap: 8px; font-size: 11px; } .directory-filters select { max-width: 120px; } .directory-room > .game-icon { width: 40px; } .directory-room > button { min-width: 66px; } }
</style>
