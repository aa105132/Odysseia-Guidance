<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
type Stats = { rounds: number; wins: number; losses: number; draws: number; win_rate: number; net_profit: number; today_profit: number; legacy_rounds: number; max_win?: number; max_loss?: number; total_won?: number; total_lost?: number };
type Entry = { rank: number; user_id: string; username: string; avatar_url: string; net_profit: number; rounds: number };
type StatsResponse = { stats: Stats; timezone: string };
type BoardResponse = { entries: Entry[]; self: Entry | null; timezone: string; legacy_rounds: number };
type RoundEntry = { round_key: string; game_type: string | null; profit: number; settled_at: string | null; legacy: boolean; stake: number | null; payout: number | null; has_details: boolean };
type RoundAction = { user_id: string; action: string; name?: string; combo?: string; time?: number; amount?: number; cards?: string[]; tile?: string; tiles?: string[]; bid?: number; target_id?: string; phase?: string; pot?: number; stack?: number; round_bet?: number; total_bet?: number };
type RoundDetails = { room_id?: string; started_at?: number; players?: { user_id: string; username: string; is_bot?: boolean }[]; final_state?: { community_cards?: string[]; players?: { user_id: string; hand?: string[]; hand_name?: string; score_delta?: number }[] }; actions?: RoundAction[]; history_truncated?: boolean };
type RoundDetail = RoundEntry & { details: RoundDetails };
type HistoryResponse = { entries: RoundEntry[]; total: number; has_more: boolean; timezone: string };
type TransactionEntry = { id: number; amount: number; reason: string; timestamp: string | null };
type TransactionsResponse = { entries: TransactionEntry[]; total: number; has_more: boolean; balance: number; timezone: string };
const props = defineProps<{
  profile: { user_id: string; username: string; avatar_url: string; balance: number };
  apiCall: ApiCall;
  initialGameType?: string;
  initialTab?: 'stats' | 'leaderboard';
}>();
const emit = defineEmits<{ close: [] }>();
const names: Record<string, string> = { all: '全部玩法', blackjack: '21点', texas: '德州扑克', golden_flower: '炸金花', landlord: '斗地主', mahjong: '基础麻将', sichuan_mahjong: '四川血战', guandan: '掼蛋' };
const panel = ref<HTMLDialogElement | null>(null);
const scrollContent = ref<HTMLDivElement | null>(null);
const tab = ref<'stats' | 'leaderboard' | 'transactions'>(props.initialTab ?? 'stats');
const gameType = ref(props.initialGameType && props.initialGameType in names ? props.initialGameType : 'all');
const period = ref<'today' | 'all'>('today');
const stats = ref<Stats | null>(null);
const board = ref<BoardResponse | null>(null);
const loading = ref(false);
const error = ref('');
const history = ref<RoundEntry[]>([]);
const historyTotal = ref(0);
const historyMore = ref(false);
const historyLoading = ref(false);
const historyError = ref('');
const transactions = ref<TransactionEntry[]>([]);
const transactionsTotal = ref(0);
const transactionsMore = ref(false);
const transactionBalance = ref(props.profile.balance);
const moreLoading = ref(false);
const moreError = ref('');
const detailKey = ref<string | null>(null);
const detail = ref<RoundDetail | null>(null);
const detailLoading = ref(false);
const detailError = ref('');
let detailSequence = 0;
let sequence = 0;
let disposed = false;
const legacyRounds = computed(() => tab.value === 'stats' ? stats.value?.legacy_rounds ?? 0 : board.value?.legacy_rounds ?? 0);
function number(value: number) { return (Number.isFinite(value) ? value : 0).toLocaleString('zh-CN'); }
function profit(value: number) { return `${value > 0 ? '+' : ''}${number(value)}`; }
function profitClass(value: number) { return value > 0 ? 'profit-positive' : value < 0 ? 'profit-negative' : ''; }
const defaultAvatar = '/ui/player-avatar.svg';
function avatarFallback(event: Event) { const image = event.target as HTMLImageElement; if (!image.src.endsWith(defaultAvatar)) image.src = defaultAvatar; }
function playerName(entry: Entry) { return !entry.username || entry.username === entry.user_id ? `牌友 · ${entry.user_id.slice(-6)}` : entry.username; }
function dateLabel(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '时间未保存';
  const input = typeof value === 'number' ? value * 1000 : /^\d{4}-\d\d-\d\d \d\d:/.test(value) ? `${value.replace(' ', 'T')}Z` : value;
  const date = new Date(input);
  return Number.isNaN(date.getTime()) ? '时间未保存' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}
function roundName(entry: RoundEntry) { return entry.game_type && names[entry.game_type] ? names[entry.game_type] : '历史牌局'; }
function roundPlayerName(userId: string) {
  const player = detail.value?.details.players?.find(item => String(item.user_id) === String(userId));
  return player?.username || (String(userId) === String(props.profile.user_id) ? props.profile.username : `牌友 · ${String(userId).slice(-6)}`);
}
const actionNames: Record<string, string> = { fold: '弃牌', call: '跟注', check: '过牌', raise: '加注', all_in: '全下', look: '看牌', compare: '比牌', bid: '叫分', play: '出牌', pass: '跳过', discard: '打牌', chow: '吃牌', pung: '碰牌', kong: '杠牌', win: '胡牌', dingque: '定缺', hit: '要牌', stand: '停牌', bet: '下注' };
function cardLabel(card: string) {
  card = card.replace(/#[01]$/, '');
  const suits: Record<string, string> = { Club: '♣', Clubs: '♣', Diamond: '♦', Diamonds: '♦', Heart: '♥', Hearts: '♥', Spade: '♠', Spades: '♠' };
  if (card === 'Hidden') return '暗牌';
  if (card === 'JokerSmall') return '小王';
  if (card === 'JokerBig') return '大王';
  const poker = card.match(/^(Clubs?|Diamonds?|Hearts?|Spades?)(\d+|[JQKA])$/);
  if (poker) return `${suits[poker[1]!]}${poker[2]}`;
  const tile = card.match(/^([mpsz])(\d)$/);
  if (tile) return tile[1] === 'z' ? ['东', '南', '西', '北', '中', '发', '白'][Number(tile[2]) - 1] || card : `${tile[2]}${({ m: '万', p: '筒', s: '条' } as Record<string, string>)[tile[1]!]}`;
  return card;
}
function actionLabel(action: RoundAction) {
  let label = actionNames[action.action] || '牌局操作';
  if (action.name) label += ` · ${action.name}`;
  if (action.amount !== undefined) label += `至 ${number(action.amount)} 灵石`;
  if (action.bid !== undefined) label += action.bid ? ` ${action.bid} 分` : ' · 不叫';
  if (action.target_id) label += ` · ${roundPlayerName(action.target_id)}`;
  const cards = action.cards || action.tiles || (action.tile ? [action.tile] : []);
  if (cards.length) label += ` · ${cards.map(cardLabel).join(' ')}`;
  return label;
}
function scrollToTop() { void nextTick(() => { if (scrollContent.value) scrollContent.value.scrollTop = 0; }); }
function closeDetail() { detailSequence++; detailKey.value = null; detail.value = null; detailError.value = ''; detailLoading.value = false; scrollToTop(); }
async function openDetail(entry: RoundEntry) {
  const request = ++detailSequence;
  detailKey.value = entry.round_key;
  detail.value = null;
  detailError.value = '';
  detailLoading.value = true;
  scrollToTop();
  try {
    const response = await props.apiCall<{ round: RoundDetail }>(`/api/tables/history/${encodeURIComponent(entry.round_key)}`, 'GET', undefined, 0);
    if (!disposed && request === detailSequence) { detail.value = response.round; scrollToTop(); }
  } catch (reason) {
    if (!disposed && request === detailSequence) detailError.value = reason instanceof Error ? reason.message : '暂时无法加载牌局详情';
  } finally {
    if (!disposed && request === detailSequence) detailLoading.value = false;
  }
}
async function loadHistory(append = false, request = sequence) {
  if (append && historyLoading.value) return;
  historyLoading.value = true;
  historyError.value = '';
  const offset = append ? history.value.length : 0;
  const query = new URLSearchParams({ game_type: gameType.value, limit: '20', offset: String(offset) });
  try {
    const response = await props.apiCall<HistoryResponse>(`/api/tables/history?${query}`, 'GET', undefined, 0);
    if (disposed || request !== sequence) return;
    history.value = append ? [...history.value, ...response.entries] : response.entries;
    historyTotal.value = response.total;
    historyMore.value = response.has_more;
  } catch (reason) {
    if (!disposed && request === sequence) historyError.value = reason instanceof Error ? reason.message : '暂时无法加载最近牌局';
  } finally {
    if (!disposed && request === sequence) historyLoading.value = false;
  }
}
async function loadMoreTransactions() {
  if (moreLoading.value || !transactionsMore.value) return;
  const request = sequence;
  moreLoading.value = true;
  moreError.value = '';
  try {
    const response = await props.apiCall<TransactionsResponse>(`/api/tables/transactions?limit=20&offset=${transactions.value.length}`, 'GET', undefined, 0);
    if (disposed || request !== sequence) return;
    transactions.value = [...transactions.value, ...response.entries];
    transactionsTotal.value = response.total;
    transactionsMore.value = response.has_more;
    transactionBalance.value = response.balance;
  } catch (reason) {
    if (!disposed && request === sequence) moreError.value = reason instanceof Error ? reason.message : '暂时无法加载更多流水';
  } finally {
    if (!disposed && request === sequence) moreLoading.value = false;
  }
}
async function refresh() {
  const current = ++sequence;
  const requestedTab = tab.value;
  loading.value = true;
  error.value = '';
  // 切换筛选后隐藏旧结果，避免把另一玩法的数据显示在当前标题下。
  stats.value = null;
  board.value = null;
  history.value = [];
  historyTotal.value = 0;
  historyMore.value = false;
  historyError.value = '';
  historyLoading.value = false;
  transactions.value = [];
  moreError.value = '';
  moreLoading.value = false;
  closeDetail();
  const query = new URLSearchParams({ game_type: gameType.value });
  if (requestedTab === 'leaderboard') { query.set('period', period.value); query.set('limit', '100'); }
  if (requestedTab === 'stats') void loadHistory(false, current);
  try {
    const endpoint = requestedTab === 'transactions' ? '/api/tables/transactions?limit=20&offset=0' : `/api/tables/${requestedTab === 'stats' ? 'stats' : 'leaderboard'}?${query}`;
    const result = await props.apiCall<StatsResponse | BoardResponse | TransactionsResponse>(endpoint, 'GET', undefined, 0);
    if (disposed || current !== sequence) return;
    if (requestedTab === 'stats') stats.value = (result as StatsResponse).stats;
    else if (requestedTab === 'leaderboard') board.value = result as BoardResponse;
    else {
      const response = result as TransactionsResponse;
      transactions.value = response.entries;
      transactionsTotal.value = response.total;
      transactionsMore.value = response.has_more;
      transactionBalance.value = response.balance;
    }
  } catch (reason) {
    if (!disposed && current === sequence) error.value = reason instanceof Error ? reason.message : '暂时无法加载，请稍后重试';
  } finally {
    if (!disposed && current === sequence) loading.value = false;
  }
}
watch([tab, gameType, period], () => { void refresh(); });
onMounted(() => { panel.value?.showModal(); void refresh(); });
onBeforeUnmount(() => { disposed = true; sequence++; detailSequence++; panel.value?.close(); });
</script>

<template>
  <Teleport to="body">
    <dialog ref="panel" class="game-stats-panel" aria-labelledby="game-stats-title" @cancel.prevent="emit('close')">
      <header class="stats-heading"><div><small>月月茶楼 · 牌局记事</small><h2 id="game-stats-title">{{ tab === 'stats' ? '个人统计' : tab === 'transactions' ? '灵石明细' : '盈利排行榜' }}</h2></div><button class="game-button quiet" aria-label="关闭统计面板" @click="emit('close')">关闭</button></header>
      <div class="stats-controls">
        <nav class="stats-tabs" aria-label="统计面板栏目"><button class="game-button" :aria-pressed="tab === 'stats'" @click="tab = 'stats'">个人统计</button><button class="game-button" :aria-pressed="tab === 'transactions'" @click="tab = 'transactions'">灵石明细</button><button class="game-button" :aria-pressed="tab === 'leaderboard'" @click="tab = 'leaderboard'">盈利排行</button></nav>
        <label v-if="tab !== 'transactions'" class="stats-game-filter">玩法 <select v-model="gameType" aria-label="统计玩法"><option v-for="(name, id) in names" :key="id" :value="id">{{ name }}</option></select></label>
        <button class="game-button quiet stats-refresh" :disabled="loading" @click="refresh">刷新</button>
      </div>
      <div v-if="tab === 'leaderboard'" class="stats-period" aria-label="排行榜周期"><button class="game-button" :aria-pressed="period === 'today'" @click="period = 'today'">当日盈利</button><button class="game-button" :aria-pressed="period === 'all'" @click="period = 'all'">总计盈利</button><span>按净盈利排序 · 前 100 名</span></div>
      <div ref="scrollContent" class="stats-content" :aria-busy="loading">
        <p v-if="loading" class="stats-empty" role="status">正在加载牌局记录…</p>
        <div v-else-if="error" class="stats-empty stats-error" role="alert"><p>{{ error }}</p><button class="game-button" @click="refresh">重试</button></div>
        <template v-else-if="tab === 'stats' && detailKey">
          <div class="history-section-heading"><button class="game-button quiet" @click="closeDetail">返回最近牌局</button><h3>牌局详情</h3></div>
          <p v-if="detailLoading" class="stats-empty">正在加载牌局详情…</p>
          <div v-else-if="detailError" class="stats-empty stats-error" role="alert"><p>{{ detailError }}</p><button class="game-button" @click="openDetail({ round_key: detailKey } as RoundEntry)">重试详情</button></div>
          <template v-else-if="detail">
            <div class="round-detail-summary"><strong>{{ roundName(detail) }}</strong><time>{{ dateLabel(detail.settled_at) }}</time><b :class="profitClass(detail.profit)">{{ profit(detail.profit) }} 灵石</b></div>
            <dl class="stats-outcomes round-funds"><div><dt>本局投入</dt><dd>{{ detail.stake === null ? '未保存' : `${number(detail.stake)} 灵石` }}</dd></div><div><dt>结算返还</dt><dd>{{ detail.payout === null ? '未保存' : `${number(detail.payout)} 灵石` }}</dd></div></dl>
            <p class="stats-explanation">结算返还包含退回本金及赢得的灵石，净盈利为返还减去投入。</p>
            <p v-if="!detail.has_details" class="history-notice">该历史牌局仅有结算数据，未保存出牌过程。</p>
            <template v-else>
              <p v-if="detail.details.players?.length" class="stats-explanation">同桌：{{ detail.details.players.map(item => item.username || '牌友').join('、') }}</p>
              <div v-if="detail.details.final_state?.community_cards?.length" class="recorded-hand"><strong>结算公共牌</strong><span v-for="(card, index) in detail.details.final_state.community_cards" :key="index">{{ cardLabel(card) }}</span></div>
              <div v-for="player in (detail.details.final_state?.players || []).filter(item => item.hand?.length && item.hand.some(card => card !== 'Hidden'))" :key="player.user_id" class="recorded-hand"><strong>{{ roundPlayerName(player.user_id) }}</strong><span v-for="(card, index) in player.hand" :key="index">{{ cardLabel(card) }}</span><small v-if="player.hand_name">{{ player.hand_name }}</small></div>
              <h3 class="history-actions-title">公开行动记录</h3>
              <ol v-if="detail.details.actions?.length" class="round-actions" aria-label="牌局行动记录"><li v-for="(action, index) in detail.details.actions" :key="index"><span class="action-index">{{ index + 1 }}</span><div><strong>{{ roundPlayerName(action.user_id) }}</strong><p>{{ actionLabel(action) }}</p><small v-if="action.pot !== undefined">底池 {{ number(action.pot) }} 灵石</small></div><time v-if="action.time">{{ dateLabel(action.time) }}</time></li></ol>
              <p v-else class="history-notice">本局未保存公开行动记录。</p>
              <p v-if="detail.details.history_truncated" class="history-notice">牌局行动较多，仅保留部分记录。</p>
            </template>
          </template>
        </template>
        <template v-else-if="tab === 'stats' && stats">
          <div class="stats-profile"><img :src="profile.avatar_url || defaultAvatar" alt="" @error="avatarFallback"><div><strong>{{ profile.username }}</strong><span>{{ names[gameType] }} · 已结算牌局</span></div><span class="stats-balance">余额 {{ number(profile.balance) }} 灵石</span></div>
          <div v-if="!stats.rounds" class="stats-no-rounds" role="status">还没有该玩法的已结算记录，完成一局后就会显示在这里。</div>
          <dl class="stats-metrics">
            <div><dt>总场次</dt><dd>{{ number(stats.rounds) }}<small>场</small></dd></div>
            <div><dt>胜率</dt><dd>{{ stats.win_rate.toFixed(1) }}<small>%</small></dd></div>
            <div><dt>当日净盈利</dt><dd :class="profitClass(stats.today_profit)">{{ profit(stats.today_profit) }}<small>灵石</small></dd></div>
            <div><dt>累计净盈利</dt><dd :class="profitClass(stats.net_profit)">{{ profit(stats.net_profit) }}<small>灵石</small></dd></div>
          </dl>
          <dl class="stats-outcomes"><div><dt>胜场</dt><dd>{{ number(stats.wins) }}</dd></div><div><dt>负场</dt><dd>{{ number(stats.losses) }}</dd></div><div><dt>平局</dt><dd>{{ number(stats.draws) }}</dd></div></dl>
          <dl class="stats-extremes"><div><dt>单局最高赢</dt><dd class="profit-positive">{{ number(stats.max_win ?? 0) }}<small>灵石</small></dd></div><div><dt>单局最大亏</dt><dd class="profit-negative">{{ number(stats.max_loss ?? 0) }}<small>灵石</small></dd></div><div><dt>累计赢</dt><dd class="profit-positive">{{ number(stats.total_won ?? 0) }}<small>灵石</small></dd></div><div><dt>累计亏</dt><dd class="profit-negative">{{ number(stats.total_lost ?? 0) }}<small>灵石</small></dd></div></dl>
          <p class="stats-explanation">净盈利为实际结算后的灵石变化。盈利记胜场，亏损记负场，持平记平局；胜率 = 胜场 ÷ 总场次。</p>
          <section class="history-section" aria-label="最近牌局"><div class="history-section-heading"><h3>最近牌局</h3><span>共 {{ number(historyTotal) }} 场</span></div><ol v-if="history.length" class="history-list"><li v-for="entry in history" :key="entry.round_key"><button class="history-round" @click="openDetail(entry)"><span><strong>{{ roundName(entry) }}</strong><time>{{ dateLabel(entry.settled_at) }}</time></span><span :class="profitClass(entry.profit)"><b>{{ profit(entry.profit) }} 灵石</b><small>{{ entry.has_details ? '查看详情 ›' : '结算记录 ›' }}</small></span></button></li></ol><p v-else-if="!historyLoading && !historyError" class="history-notice">暂无最近牌局记录。</p><p v-if="historyLoading" class="history-notice">正在加载最近牌局…</p><div v-if="historyError" class="history-notice stats-error"><p>{{ historyError }}</p><button class="game-button quiet" @click="loadHistory(history.length > 0)">重试最近牌局</button></div><button v-else-if="historyMore && !historyLoading" class="game-button history-more" @click="loadHistory(true)">加载更多牌局</button></section>
        </template>
        <template v-else-if="tab === 'transactions'">
          <div class="transactions-heading"><span>当前余额</span><strong>{{ number(transactionBalance) }} <small>灵石</small></strong></div><p class="stats-explanation">展示账户所有来源的真实灵石流水，共 {{ number(transactionsTotal) }} 笔。历史余额未保存，不作推算。</p>
          <ol v-if="transactions.length" class="transaction-list" aria-label="灵石流水"><li v-for="entry in transactions" :key="entry.id"><div><strong>{{ entry.reason || '灵石变动' }}</strong><time>{{ dateLabel(entry.timestamp) }}</time></div><b :class="profitClass(entry.amount)">{{ profit(entry.amount) }}<small>灵石</small></b></li></ol><p v-else class="stats-empty">暂无灵石流水。</p><p v-if="moreError" class="history-notice stats-error">{{ moreError }}</p><button v-if="transactionsMore" class="game-button history-more" :disabled="moreLoading" @click="loadMoreTransactions">{{ moreLoading ? '正在加载…' : moreError ? '重试加载流水' : '加载更多流水' }}</button>
        </template>
        <template v-else-if="board">
          <ol v-if="board.entries.length" class="stats-ranking" aria-label="盈利排名">
            <li v-for="entry in board.entries" :key="entry.user_id" :class="{ 'stats-is-self': entry.user_id === String(profile.user_id) }">
              <span class="stats-rank" :class="{ 'stats-top-three': entry.rank <= 3 }">{{ entry.rank }}</span><img :src="entry.avatar_url || defaultAvatar" alt="" @error="avatarFallback"><div class="stats-player"><strong :title="entry.user_id">{{ playerName(entry) }}<small v-if="entry.user_id === String(profile.user_id)">你</small></strong><span>{{ number(entry.rounds) }} 场</span></div><span class="stats-profit" :class="profitClass(entry.net_profit)">{{ profit(entry.net_profit) }}<small>灵石</small></span>
            </li>
          </ol>
          <p v-else class="stats-empty" role="status">{{ period === 'today' ? '今天还没有已结算的牌局' : '还没有该玩法的结算记录' }}</p>
          <div class="stats-self-rank" aria-label="我的排名"><span>我的排名 <strong>{{ board.self ? `第 ${board.self.rank} 名` : '暂无排名' }}</strong></span><span v-if="board.self" :class="profitClass(board.self.net_profit)">{{ profit(board.self.net_profit) }} 灵石 · {{ number(board.self.rounds) }} 场</span><span v-else>完成一局后参与排名</span></div>
        </template>
        <footer v-if="tab !== 'transactions'" class="stats-notes"><p>当日以北京时间 00:00 为界。仅统计已完成的真人牌局，未结束和退款不计入。21点从统计功能上线起记录。</p><p v-if="legacyRounds">含 {{ number(legacyRounds) }} 场旧结算记录：旧记录只计入全部玩法的累计统计，不计入当日或单项玩法。</p></footer>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.game-stats-panel { box-sizing: border-box; width: min(760px, calc(var(--activity-width, 100vw) - 24px)); max-width: none; max-height: calc(var(--activity-height, 100dvh) - 24px); margin: auto; padding: 20px; border: 3px solid #efc882; border-radius: 20px; color: #463e69; background: url('/ui/guochao/cloud-pattern.svg') center / 190px, linear-gradient(125deg, #fff3d9, #eed3ac); box-shadow: inset 0 0 0 2px #b98557, 0 14px 65px #17152bb0; }
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
.stats-extremes { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }
.stats-extremes > div { border: 1px solid #d6bc91; border-radius: 10px; padding: 10px; background: #fff8e888; min-width: 0; }
.stats-extremes dt { font-size: 12px; color: #79674e; }
.stats-extremes dd { margin: 6px 0 0; font-size: 19px; font-weight: 700; overflow-wrap: anywhere; }
.stats-extremes small { font-size: 11px; margin-left: 4px; font-weight: 400; }
.history-section { margin-top: 20px; border-top: 1px solid #c9aa7e; padding-top: 8px; }
.history-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 5px 0 10px; }
.history-section-heading h3, .history-actions-title { margin: 6px 0; font-size: 17px; }
.history-section-heading > span { font-size: 12px; color: #79674e; }
.history-list, .transaction-list, .round-actions { margin: 0; padding: 0; list-style: none; }
.history-list li { margin: 8px 0; }
.game-stats-panel .history-round { width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px; border: 1px solid #d6bc91; border-radius: 10px; background: #fff8e8cc; color: inherit; box-shadow: none; text-shadow: none; text-align: left; cursor: pointer; }
.history-round > span { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.history-round > span:last-child { text-align: right; }
.history-round strong { font-size: 14px; }
.history-round time, .history-round small, .transaction-list time, .round-detail-summary time { font-size: 12px; color: #79674e; }
.history-round b { font-size: 15px; overflow-wrap: anywhere; }
.history-notice { padding: 12px; border-radius: 8px; background: #fff8e888; font-size: 12px; color: #79674e; line-height: 1.6; }
.history-notice p { margin: 0 0 8px; }
.history-more { display: block; margin: 12px auto; }
.transactions-heading { display: flex; justify-content: space-between; align-items: center; background: #fff8e8cc; border-radius: 12px; padding: 16px; }
.transactions-heading strong { font-size: 26px; }
.transactions-heading small { font-size: 13px; }
.transaction-list li { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid #ccb38b; padding: 14px 5px; }
.transaction-list li > div { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.transaction-list strong { font-size: 13px; overflow-wrap: anywhere; font-weight: 500; }
.transaction-list b { flex: none; font-size: 17px; }
.transaction-list small { font-size: 11px; font-weight: 400; margin-left: 4px; }
.round-detail-summary { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 14px; margin-bottom: 10px; background: #fff8e8cc; border-radius: 10px; }
.round-detail-summary b { margin-left: auto; font-size: 20px; }
.round-funds { flex-wrap: wrap; gap: 14px; }
.round-funds dd { font-size: 16px; }
.recorded-hand { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin: 12px 0; }
.recorded-hand strong { width: 100%; font-size: 13px; margin-bottom: 3px; }
.recorded-hand span { display: inline-block; padding: 6px; border: 1px solid #d6bc91; border-radius: 5px; background: #fffaf0; font-size: 14px; }
.recorded-hand small { font-size: 12px; }
.round-actions li { display: flex; gap: 10px; padding: 12px 4px; border-bottom: 1px solid #d6bc91; }
.action-index { flex: none; font-size: 12px; color: #96754a; min-width: 18px; }
.round-actions li > div { flex: 1; min-width: 0; }
.round-actions strong { font-size: 12px; }
.round-actions p { margin: 5px 0; font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.round-actions small, .round-actions time { color: #79674e; font-size: 11px; }
.round-actions time { text-align: right; flex: 0 0 125px; }
@container activity-viewport (max-height: 450px) { .game-stats-panel { padding: 10px 14px; border-radius: 14px; } .game-stats-panel[open] { gap: 8px; } .stats-heading small { display: none; } .stats-heading h2 { font-size: 19px; } .stats-metrics { gap: 8px; margin: 10px 0; } .stats-metrics > div { padding: 10px; } .stats-metrics dd { font-size: 24px; } }
@container activity-viewport (max-width: 620px) { .stats-controls { gap: 8px; } .game-stats-panel .game-button { padding: 7px 10px; } .stats-tabs { gap: 5px; } .stats-game-filter { gap: 4px; } .stats-game-filter select { max-width: 112px; } .stats-period span { font-size: 11px; } .stats-balance { white-space: normal; text-align: right; } .stats-ranking li { gap: 8px; padding: 9px 8px; } }
@container activity-viewport (max-width: 430px) { .stats-controls { justify-content: space-between; } .stats-game-filter { margin-left: 0; } .stats-refresh { margin-left: auto; } .stats-period { flex-wrap: wrap; } .stats-period span { width: 100%; margin-left: 0; } .stats-metrics dd { font-size: 22px; } }
@container activity-viewport (max-width: 620px) { .stats-extremes { grid-template-columns: repeat(2, minmax(0, 1fr)); } .round-actions time { flex-basis: 90px; } .stats-controls { column-gap: 6px; } .stats-controls .game-button { padding-left: 8px; padding-right: 8px; } }
</style>
