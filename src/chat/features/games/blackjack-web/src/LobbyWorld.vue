<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import GameIcon from './GameIcon.vue';
import type { TableGameType } from './tableGameRules';
import './lobby-world.css';

defineProps<{
  profile: { user_id: string; username: string; avatar_url: string; balance: number } | null;
  nonameAvailable: boolean;
  requestInFlight: boolean;
}>();
const emit = defineEmits<{
  blackjack: [];
  table: [game: TableGameType];
  farm: [];
  rank: [];
  rooms: [];
  profile: [];
  noname: [];
}>();
const destinations = [
  { id: 'blackjack', title: '21点', detail: '独坐 · 同桌', x: 32.8, y: 41.1 },
  { id: 'texas', title: '德州扑克', detail: '2–8人同桌', x: 52, y: 42.7 },
  { id: 'landlord', title: '斗地主', detail: '三人斗智', x: 73, y: 43.5 },
  { id: 'mahjong', title: '四川麻将', detail: '血战到底', x: 29, y: 58.5 },
  { id: 'golden_flower', title: '炸金花', detail: '三张藏机', x: 52.6, y: 61.5 },
  { id: 'guandan', title: '掼蛋', detail: '对家携手', x: 78, y: 62.8 },
  { id: 'farm', title: '修仙灵圃', detail: '灵草 · 灵兽', x: 40.5, y: 76.3 },
  { id: 'leaderboard', title: '排行榜', detail: '当日 · 总计', x: 68.5, y: 80 },
] as const;
const active = ref(true);
const artReady = ref(false);
const missingArt = ref<Record<string, boolean>>({});
function updateVisibility() { active.value = !document.hidden; }
function open(id: typeof destinations[number]['id']) {
  if (id === 'blackjack') emit('blackjack');
  else if (id === 'farm') emit('farm');
  else if (id === 'leaderboard') emit('rank');
  else emit('table', id);
}
onMounted(() => {
  updateVisibility();
  document.addEventListener('visibilitychange', updateVisibility);
});
onBeforeUnmount(() => document.removeEventListener('visibilitychange', updateVisibility));
</script>

<template>
  <section class="lobby-world" :class="{ 'lobby-world-paused': !active, 'lobby-world-art-ready': artReady }" aria-label="月月洞府游戏大厅">
    <div class="lobby-world-underlay" aria-hidden="true"></div>
    <div class="lobby-world-map">
      <img class="lobby-world-scene" :src="'/ui/farm-v2/lobby-world.webp'" alt="" draggable="false" @load="artReady = true">
      <div class="lobby-world-qi" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>
      <nav class="lobby-world-places" aria-label="选择玩法">
        <button v-for="place in destinations" :key="place.id" type="button"
          class="lobby-world-place" :class="`world-place-${place.id}`"
          :style="{ '--place-x': `${place.x}%`, '--place-y': `${place.y}%` }"
          :aria-label="place.title" :disabled="requestInFlight" @click="open(place.id)">
          <span class="lobby-world-token" aria-hidden="true">
            <img v-if="!missingArt[place.id]" :src="place.id === 'farm' ? '/ui/farm-v2/icons/seed.webp' : place.id === 'leaderboard' ? '/ui/farm-v2/icons/guide.webp' : `/ui/farm-v2/games/${place.id}.webp`" alt="" draggable="false" @error="missingArt[place.id] = true">
            <GameIcon v-else-if="place.id !== 'farm'" :name="place.id" />
            <img v-else :src="'/ui/farm/plants/huangjing.webp'" alt="" draggable="false">
          </span>
          <span class="lobby-world-sign"><strong>{{ place.title }}</strong><small>{{ place.detail }}</small></span>
        </button>
        <button v-if="nonameAvailable" type="button" class="lobby-world-noname" :disabled="requestInFlight" @click="emit('noname')">
          <img :src="'/ui/farm-v2/games/noname.webp'" alt="" aria-hidden="true"><span>三国杀</span><small>无名杀 · 免费娱乐</small>
        </button>
      </nav>
    </div>
    <header class="lobby-world-header">
      <button v-if="profile" type="button" class="lobby-world-profile" aria-label="查看个人信息与统计" @click="emit('profile')">
        <img :src="profile.avatar_url || '/ui/player-avatar.svg'" alt="玩家头像" draggable="false">
        <span><strong>{{ profile.username }}</strong><small>{{ profile.balance.toLocaleString() }} 灵石</small></span>
      </button>
      <div class="lobby-world-title"><span>月月茶楼</span><h1>云上闲游</h1></div>
      <div class="lobby-world-tools"><slot name="audio"></slot><button type="button" class="lobby-world-rooms" @click="emit('rooms')">房间列表</button></div>
    </header>
    <div class="lobby-world-mascot"><slot name="mascot"></slot></div>
    <p class="lobby-world-note">点一方茶席，坐下开局<span>棋牌游戏使用账户灵石</span></p>
  </section>
</template>
