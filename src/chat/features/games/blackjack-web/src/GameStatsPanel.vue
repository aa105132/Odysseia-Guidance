<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
type Stats = { rounds: number; wins: number; losses: number; draws: number; win_rate: number; net_profit: number; today_profit: number; legacy_rounds: number };
type Entry = { rank: number; user_id: string; username: string; avatar_url: string; net_profit: number; rounds: number };
type StatsResponse = { stats: Stats; timezone: string };
type BoardResponse = { entries: Entry[]; self: Entry | null; timezone: string; legacy_rounds: number };
const props = defineProps<{
  profile: { user_id: string; username: string; avatar_url: string; balance: number };
  apiCall: ApiCall;
  initialGameType?: string;
  initialTab?: 'stats' | 'leaderboard';
}>();
const emit = defineEmits<{ close: [] }>();
const names: Record<string, string> = { all: '全部玩法', blackjack: '21点', texas: '德州扑克', golden_flower: '炸金花', landlord: '斗地主', mahjong: '基础麻将', sichuan_mahjong: '四川血战' };
const panel = ref<HTMLDialogElement | null>(null);
const tab = ref(props.initialTab ?? 'stats');
const gameType = ref(props.initialGameType && props.initialGameType in names ? props.initialGameType : 'all');
const period = ref<'today' | 'all'>('today');
const stats = ref<Stats | null>(null);
const board = ref<BoardResponse | null>(null);
const loading = ref(false);
const error = ref('');
let sequence = 0;
let disposed = false;
const legacyRounds = computed(() => tab.value === 'stats' ? stats.value?.legacy_rounds ?? 0 : board.value?.legacy_rounds ?? 0);
function number(value: number) { return value.toLocaleString('zh-CN'); }
function profit(value: number) { return `${value > 0 ? '+' : ''}${number(value)}`; }
function profitClass(value: number) { return value > 0 ? 'profit-positive' : value < 0 ? 'profit-negative' : ''; }
function avatarFallback(event: Event) { const image = event.target as HTMLImageElement; image.onerror = null; image.src = '/character/normal.webp'; }
async function refresh() {
  const current = ++sequence;
  const requestedTab = tab.value;
  loading.value = true;
  error.value = '';
  // 切换筛选后隐藏旧结果，避免把另一玩法的数据显示在当前标题下。
  stats.value = null;
  board.value = null;
  const query = new URLSearchParams({ game_type: gameType.value });
  if (requestedTab === 'leaderboard') query.set('period', period.value);
  try {
    const result = await props.apiCall<StatsResponse | BoardResponse>(`/api/tables/${requestedTab === 'stats' ? 'stats' : 'leaderboard'}?${query}`, 'GET', undefined, 0);
    if (disposed || current !== sequence) return;
    if (requestedTab === 'stats') stats.value = (result as StatsResponse).stats;
    else board.value = result as BoardResponse;
  } catch (reason) {
    if (!disposed && current === sequence) error.value = reason instanceof Error ? reason.message : '暂时无法加载，请稍后重试';
  } finally {
    if (!disposed && current === sequence) loading.value = false;
  }
}
watch([tab, gameType, period], () => { void refresh(); });
onMounted(() => { panel.value?.showModal(); void refresh(); });
onBeforeUnmount(() => { disposed = true; sequence++; panel.value?.close(); });
</script>

<template>
  <Teleport to="body">
    <dialog ref="panel" class="game-stats-panel" aria-labelledby="game-stats-title" @cancel.prevent="emit('close')">
      <header class="stats-heading"><div><small>月月茶楼 · 牌局记事</small><h2 id="game-stats-title">{{ tab === 'stats' ? '个人统计' : '盈利排行榜' }}</h2></div><button class="game-button quiet" aria-label="关闭统计面板" @click="emit('close')">关闭</button></header>
      <div class="stats-controls">
        <nav class="stats-tabs" aria-label="统计面板栏目"><button class="game-button" :aria-pressed="tab === 'stats'" @click="tab = 'stats'">个人统计</button><button class="game-button" :aria-pressed="tab === 'leaderboard'" @click="tab = 'leaderboard'">盈利排行</button></nav>
        <label class="stats-game-filter">玩法 <select v-model="gameType" aria-label="统计玩法"><option v-for="(name, id) in names" :key="id" :value="id">{{ name }}</option></select></label>
        <button class="game-button quiet stats-refresh" :disabled="loading" @click="refresh">刷新</button>
      </div>
      <div v-if="tab === 'leaderboard'" class="stats-period" aria-label="排行榜周期"><button class="game-button" :aria-pressed="period === 'today'" @click="period = 'today'">当日盈利</button><button class="game-button" :aria-pressed="period === 'all'" @click="period = 'all'">总计盈利</button><span>按净盈利排序 · 前 20 名</span></div>
      <div class="stats-content" :aria-busy="loading">
        <p v-if="loading" class="stats-empty" role="status">正在加载牌局记录…</p>
        <div v-else-if="error" class="stats-empty stats-error" role="alert"><p>{{ error }}</p><button class="game-button" @click="refresh">重试</button></div>
        <template v-else-if="tab === 'stats' && stats">
          <div class="stats-profile"><img :src="profile.avatar_url || '/character/normal.webp'" alt="" @error="avatarFallback"><div><strong>{{ profile.username }}</strong><span>{{ names[gameType] }} · 已结算牌局</span></div><span class="stats-balance">余额 {{ number(profile.balance) }} 灵石</span></div>
          <div v-if="!stats.rounds" class="stats-no-rounds" role="status">还没有该玩法的已结算记录，完成一局后就会显示在这里。</div>
          <dl class="stats-metrics">
            <div><dt>总场次</dt><dd>{{ number(stats.rounds) }}<small>场</small></dd></div>
            <div><dt>胜率</dt><dd>{{ stats.win_rate.toFixed(1) }}<small>%</small></dd></div>
            <div><dt>当日净盈利</dt><dd :class="profitClass(stats.today_profit)">{{ profit(stats.today_profit) }}<small>灵石</small></dd></div>
            <div><dt>累计净盈利</dt><dd :class="profitClass(stats.net_profit)">{{ profit(stats.net_profit) }}<small>灵石</small></dd></div>
          </dl>
          <dl class="stats-outcomes"><div><dt>胜场</dt><dd>{{ number(stats.wins) }}</dd></div><div><dt>负场</dt><dd>{{ number(stats.losses) }}</dd></div><div><dt>平局</dt><dd>{{ number(stats.draws) }}</dd></div></dl>
          <p class="stats-explanation">净盈利为实际结算后的灵石变化。盈利记胜场，亏损记负场，持平记平局；胜率 = 胜场 ÷ 总场次。</p>
        </template>
        <template v-else-if="board">
          <ol v-if="board.entries.length" class="stats-ranking" aria-label="盈利排名">
            <li v-for="entry in board.entries" :key="entry.user_id" :class="{ 'stats-is-self': entry.user_id === String(profile.user_id) }">
              <span class="stats-rank" :class="{ 'stats-top-three': entry.rank <= 3 }">{{ entry.rank }}</span><img :src="entry.avatar_url || '/character/normal.webp'" alt="" @error="avatarFallback"><div class="stats-player"><strong>{{ entry.username || '牌友' }}<small v-if="entry.user_id === String(profile.user_id)">你</small></strong><span>{{ number(entry.rounds) }} 场</span></div><span class="stats-profit" :class="profitClass(entry.net_profit)">{{ profit(entry.net_profit) }}<small>灵石</small></span>
            </li>
          </ol>
          <p v-else class="stats-empty" role="status">{{ period === 'today' ? '今天还没有已结算的牌局' : '还没有该玩法的结算记录' }}</p>
          <div class="stats-self-rank" aria-label="我的排名"><span>我的排名 <strong>{{ board.self ? `第 ${board.self.rank} 名` : '暂无排名' }}</strong></span><span v-if="board.self" :class="profitClass(board.self.net_profit)">{{ profit(board.self.net_profit) }} 灵石 · {{ number(board.self.rounds) }} 场</span><span v-else>完成一局后参与排名</span></div>
        </template>
        <footer class="stats-notes"><p>当日以北京时间 00:00 为界。仅统计已完成的真人牌局，未结束和退款不计入。21点从统计功能上线起记录。</p><p v-if="legacyRounds">含 {{ number(legacyRounds) }} 场旧结算记录：旧记录只计入全部玩法的累计统计，不计入当日或单项玩法。</p></footer>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.game-stats-panel { box-sizing: border-box; width: min(760px, calc(100vw - 24px)); max-width: none; max-height: calc(100dvh - 24px); margin: auto; padding: 20px; border: 3px solid #efc882; border-radius: 20px; color: #463e69; background: url('/ui/guochao/cloud-pattern.svg') center / 190px, linear-gradient(125deg, #fff3d9, #eed3ac); box-shadow: inset 0 0 0 2px #b98557, 0 14px 65px #17152bb0; }
.game-stats-panel[open] { display: flex; flex-direction: column; gap: 14px; }
.game-stats-panel::backdrop { background: #151125b5; }
.game-stats-panel .game-button { min-height: 36px; font-size: 13px; padding: 7px 15px; }
.stats-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex: none; }
.stats-heading small { color: #94623e; font-size: 11px; letter-spacing: 2px; }
.stats-heading h2 { margin: 2px 0 0; font-size: 25px; }
.stats-controls { display: flex; align-items: center; gap: 12px; flex: none; flex-wrap: wrap; }
.stats-tabs, .stats-period { display: flex; gap: 8px; align-items: center; flex: none; }
.game-stats-panel button[aria-pressed='true'] { background: linear-gradient(#d9b56b, #a97638); color: #fff9df; }
.stats-game-filter { display: flex; align-items: center; gap: 6px; margin-left: auto; font-size: 13px; }
.stats-game-filter select { box-sizing: border-box; min-height: 36px; padding: 7px; max-width: 132px; border: 1px solid #b28b66; border-radius: 8px; background: #fff8e8; color: #493d67; font-size: 13px; }
.stats-period span { color: #79674e; font-size: 12px; margin-left: auto; }
.stats-content { min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding-right: 4px; }
.stats-profile { display: flex; align-items: center; gap: 12px; padding: 12px; border-radius: 12px; background: #fff8e8bb; }
.stats-profile img { width: 48px; height: 48px; border: 2px solid #ccab73; border-radius: 10px; object-fit: cover; }
.stats-profile > div { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.stats-profile strong { overflow-wrap: anywhere; }
.stats-profile span { font-size: 12px; color: #79674e; }
.stats-balance { margin-left: auto; white-space: nowrap; }
.stats-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 14px 0; }
.stats-metrics > div { padding: 15px; border: 1px solid #cba872; border-radius: 12px; background: #fff8e8cc; }
.stats-metrics dt { font-size: 13px; color: #79674e; }
.stats-metrics dd { margin: 7px 0 0; font-size: 28px; font-weight: 700; overflow-wrap: anywhere; }
.stats-metrics small, .stats-profit small { font-size: 12px; font-weight: 400; margin-left: 5px; }
.stats-outcomes { display: flex; justify-content: space-around; padding: 12px; border-radius: 10px; background: #ece0cbb3; margin: 0; }
.stats-outcomes > div { display: flex; align-items: baseline; gap: 8px; }
.stats-outcomes dt { color: #79674e; font-size: 13px; }
.stats-outcomes dd { margin: 0; font-size: 19px; font-weight: 700; }
.stats-explanation, .stats-notes { color: #79674e; font-size: 12px; line-height: 1.6; }
.stats-notes { margin-top: 14px; }
.stats-notes p { margin: 5px 0 0; }
.stats-ranking { list-style: none; margin: 0; padding: 0; }
.stats-ranking li { display: flex; align-items: center; gap: 12px; padding: 10px 12px; margin-bottom: 7px; border: 1px solid #d6bc91; border-radius: 10px; background: #fff8e8cc; }
.stats-ranking li.stats-is-self { border: 2px solid #b38338; background: #ffe8b4dd; }
.stats-rank { width: 32px; flex: none; text-align: center; font-size: 19px; font-weight: 700; }
.stats-top-three { color: #9a5b12; }
.stats-ranking img { width: 38px; height: 38px; border-radius: 8px; object-fit: cover; }
.stats-player { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 4px; }
.stats-player strong { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stats-player strong small { margin-left: 7px; font-weight: 400; }
.stats-player > span { color: #79674e; font-size: 12px; }
.stats-profit { font-size: 18px; font-weight: 700; text-align: right; overflow-wrap: anywhere; max-width: 45%; }
.profit-positive { color: #287052; }
.profit-negative { color: #a03743; }
.stats-self-rank { display: flex; justify-content: space-between; gap: 12px; padding: 12px; border: 1px solid #b38338; border-radius: 10px; background: #f7e2baff; font-size: 13px; flex-wrap: wrap; }
.stats-self-rank strong { margin-left: 8px; }
.stats-empty { text-align: center; padding: 32px 12px; color: #79674e; }
.stats-error { color: #a03743; }
.stats-no-rounds { margin-top: 12px; padding: 10px; font-size: 13px; background: #fff8e8bb; border-radius: 8px; }
@media (max-height: 450px) { .game-stats-panel { padding: 10px 14px; border-radius: 14px; } .game-stats-panel[open] { gap: 8px; } .stats-heading small { display: none; } .stats-heading h2 { font-size: 19px; } .stats-metrics { gap: 8px; margin: 10px 0; } .stats-metrics > div { padding: 10px; } .stats-metrics dd { font-size: 24px; } }
@media (max-width: 620px) { .stats-controls { gap: 8px; } .game-stats-panel .game-button { padding: 7px 10px; } .stats-tabs { gap: 5px; } .stats-game-filter { gap: 4px; } .stats-game-filter select { max-width: 112px; } .stats-period span { font-size: 11px; } .stats-balance { white-space: normal; text-align: right; } .stats-ranking li { gap: 8px; padding: 9px 8px; } }
@media (max-width: 430px) { .stats-controls { justify-content: space-between; } .stats-game-filter { margin-left: 0; } .stats-refresh { margin-left: auto; } .stats-period { flex-wrap: wrap; } .stats-period span { width: 100%; margin-left: 0; } .stats-metrics dd { font-size: 22px; } }
</style>
