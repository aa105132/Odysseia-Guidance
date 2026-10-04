<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import SpiritStoneIcon from './SpiritStoneIcon.vue';
import GameIcon from './GameIcon.vue';
import './guochao-dialog.css';

type ListedRoom = {
  room_id: string; game_type: string; host_username: string; player_count: number;
  max_players: number; state: string; can_join: boolean; room_tier: string;
  base_stake: number | null; entry_min: number; loss_limit: number | null; is_member: boolean;
};
type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
const props = defineProps<{ apiCall: ApiCall; balance: number; gameType?: string; restrictGame?: boolean; embedded?: boolean; maintenance?: Record<string, string>; joinRoom: (room: ListedRoom) => Promise<boolean> }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement | null>(null);
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
const names: Record<string, string> = { blackjack: '21点', texas: '德州扑克', golden_flower: '炸金花', landlord: '斗地主', mahjong: '基础麻将', sichuan_mahjong: '四川血战', guandan: '掼蛋', noname: '三国杀' };
const tiers: Record<string, string> = { beginner: '初级场', intermediate: '中级场', advanced: '高级场', custom: '自定义' };
const visibleRooms = computed(() => rooms.value.filter(room => (!filter.value || room.game_type === filter.value) && (!onlyOpen.value || canJoin(room))));
function icon(game: string) { return (game === 'sichuan_mahjong' ? 'mahjong' : game) as 'blackjack' | 'texas' | 'golden_flower' | 'landlord' | 'mahjong' | 'guandan' | 'noname'; }
function maintenanceReason(room: ListedRoom) { return props.maintenance?.[room.game_type] || ''; }
// Returning also calls the join API; do not offer a return that maintenance rejects.
function canJoin(room: ListedRoom) { return !maintenanceReason(room) && room.can_join && (room.is_member || props.balance >= room.entry_min); }
function buttonLabel(room: ListedRoom) {
  if (joining.value === room.room_id) return '入座中…';
  if (maintenanceReason(room)) return '维护中';
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
    const data = await props.apiCall<{ rooms: ListedRoom[]; total: number; warning?: string }>(`/api/rooms?${query}`, 'GET', undefined, 0);
    if (!disposed && current === sequence) { rooms.value = data.rooms; total.value = data.total; error.value = data.warning || ''; }
  } catch (reason) {
    if (!disposed && current === sequence) error.value = reason instanceof Error ? reason.message : '房间列表暂时无法加载';
  } finally { if (!disposed && current === sequence) loading.value = false; }
}
async function join(room: ListedRoom) {
  if (maintenanceReason(room)) { error.value = maintenanceReason(room); return; }
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
onMounted(() => { if (panel.value instanceof HTMLDialogElement) panel.value.showModal(); void refresh(); timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000); });
onBeforeUnmount(() => { disposed = true; sequence++; clearInterval(timer); if (panel.value instanceof HTMLDialogElement) panel.value.close(); });
</script>

<template>
  <Teleport to="#app" :disabled="embedded">
    <component :is="embedded ? 'section' : 'dialog'" ref="panel" class="room-directory gc-directory" :class="{ 'room-directory-embedded': embedded, 'gc-dialog': !embedded }" aria-labelledby="room-directory-title" @cancel.prevent="close">
      <header v-if="!embedded" class="directory-heading gc-heading"><GameIcon name="room" /><div><small>月月茶楼 · 寻桌入席</small><h2 id="room-directory-title">房间列表</h2></div><button class="game-button quiet gc-close" :disabled="Boolean(joining)" aria-label="关闭房间列表" @click="close">关闭</button></header>
      <h2 v-else id="room-directory-title" class="embedded-directory-title">寻桌入席 <small>找一桌喜欢的玩法，和牌友一起开局</small></h2>
      <div class="directory-filters"><label>玩法 <select v-model="filter" :disabled="loading || Boolean(joining) || restrictGame" aria-label="筛选房间玩法"><option value="">全部玩法</option><option v-for="(name, id) in names" :key="id" :value="id">{{ name }}</option></select></label><label class="directory-open"><input v-model="onlyOpen" type="checkbox">只看可加入</label><button class="game-button" :disabled="loading || Boolean(joining)" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button></div>
      <p v-if="error" class="directory-error" role="alert">{{ error }}</p>
      <div class="directory-list" :aria-busy="loading" aria-label="公开多人房间">
        <article v-for="room in visibleRooms" :key="`${room.game_type}:${room.room_id}`" class="directory-room" :data-room-id="room.room_id">
          <GameIcon :name="icon(room.game_type)" />
          <div class="directory-room-info"><h3>{{ names[room.game_type] }} <span>{{ room.game_type === 'noname' ? '经典身份' : tiers[room.room_tier] ?? '自由下注' }}</span></h3><p class="directory-host" :title="room.host_username">{{ room.host_username }} 的房间 · {{ room.room_id }}</p><p class="directory-terms"><span>{{ room.player_count }}/{{ room.max_players }} 人</span><span v-if="room.game_type === 'noname'">免费娱乐 · 不扣灵石</span><template v-else><span v-if="room.base_stake">底分 <SpiritStoneIcon />{{ room.base_stake }}</span><span>准入 <SpiritStoneIcon />{{ room.entry_min }} 灵石</span><span v-if="room.loss_limit">单局上限 <SpiritStoneIcon />{{ room.loss_limit }}</span></template></p></div>
          <button class="game-button gold" :disabled="Boolean(joining) || !canJoin(room)" :aria-label="`${buttonLabel(room)} ${room.room_id}`" @click="join(room)">{{ buttonLabel(room) }}</button>
          <p v-if="maintenanceReason(room)" class="directory-maintenance" role="status">{{ maintenanceReason(room) }}</p>
        </article>
        <div v-if="!visibleRooms.length" class="directory-empty" role="status"><GameIcon name="friends" /><strong>{{ loading ? '正在找桌…' : '暂时没有合适的房间' }}</strong><p>换个玩法看看，或返回大厅开一桌。</p></div>
      </div>
      <nav v-if="total > 100 || offset" class="directory-pages" aria-label="房间列表分页"><button class="game-button" :disabled="!offset || loading || Boolean(joining)" @click="changePage(-1)">上一页</button><span>第 {{ offset / 100 + 1 }} 页 · 共 {{ total }} 桌</span><button class="game-button" :disabled="offset + 100 >= total || loading || Boolean(joining)" @click="changePage(1)">下一页</button></nav>
      <footer>多人房间对大厅公开 · 每 5 秒更新 · 入座不会自动下注</footer>
    </component>
  </Teleport>
</template>

<style scoped>
.directory-maintenance { grid-column: 1 / -1; margin: 0; padding: 8px 10px; border-left: 3px solid #a43d32; border-radius: 4px; background: #fff0d5; color: #783a28; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
</style>
