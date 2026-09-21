<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { tableGameRules, type TableGameType, type TableRoomGameType } from './tableGameRules';
import GameIcon from './GameIcon.vue';
import RoundFeedback from './RoundFeedback.vue';
import RoomDirectory from './RoomDirectory.vue';
import CopyRoomCode from './CopyRoomCode.vue';

type Profile = { user_id: string; username: string; avatar_url: string; balance: number };
type Member = { user_id: string; username: string; avatar_url: string; is_bot: boolean; is_ready: boolean; connected: boolean };
type SeatAction = { action: 'play' | 'pass' | 'bid'; cards: string[]; label: string; bid?: number };
type MissingSuit = 'm' | 'p' | 's';
type MahjongWinEvent = { id: number | string; user_id: string; source_id: string | null; kind: string; fan: number; label: string; amount: number; tile?: string };
type GamePlayer = {
  user_id: string; hand: string[]; hand_count: number; chips?: number; stack?: number;
  folded?: boolean; role?: string; bet?: number; score?: number; looked?: boolean;
  melds?: unknown[]; discards?: string[]; score_delta?: number; hand_name?: string;
  missing_suit?: MissingSuit | null; has_won?: boolean; win_order?: number | null;
  win_fan?: number | null; win_label?: string | null; winning_tile?: string | null;
};
type GameState = {
  phase: string; finished: boolean; current_player_id: string | null;
  players: GamePlayer[]; legal_actions: string[]; message: string; winners: string[];
  community_cards?: string[]; pot?: number; current_bet?: number;
  last_play?: { user_id?: string; cards?: string[] } | string[] | null;
  seat_actions?: Record<string, SeatAction>;
  discards?: Record<string, string[]> | string[];
  melds?: Record<string, unknown[]>;
  min_raise_to?: number; max_raise_to?: number; call_amount?: number; compare_cost?: number;
  bid_options?: number[]; bottom_cards?: string[]; wall_count?: number;
  chow_options?: string[][]; kong_options?: string[]; last_discard?: { user_id: string; tile: string };
  deal_count?: number;
  mahjong_variant?: string; missing_suit_options?: MissingSuit[]; win_events?: MahjongWinEvent[];
  [key: string]: unknown;
};
type RoomState = {
  room_id: string; game_type: TableRoomGameType; host_user_id: string;
  state: 'waiting' | 'playing' | 'finished'; revision: number; mode: 'solo' | 'multi';
  include_yueyue: boolean; min_players: number; max_players: number;
  players: Member[]; game: GameState | null; turn_deadline?: number | null;
  stake?: number; buy_in?: number; settlement_status?: string; actual_settlement?: Record<string, number>;
  room_tier: RoomTier; base_stake: number; entry_min: number; loss_limit: number;
  round_number?: number;
};
type PublicTier = 'beginner' | 'intermediate' | 'advanced';
type RoomTier = PublicTier | 'custom';
type RoomEnvelope = { success: boolean; room: RoomState | null; viewer_balance?: number };
type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;

const props = defineProps<{ gameType: TableGameType; profile: Profile; apiCall: ApiCall; initialRoomId?: string }>();
const emit = defineEmits<{ back: []; balance: [number]; invite: [{ room_id: string; game_type: string }]; joinFailed: [string] }>();
const room = ref<RoomState | null>(null);
const roomInput = ref('');
const busy = ref(false);
const recovering = ref(true);
const showRoomDirectory = ref(false);
const error = ref('');
const includeYueyue = ref(true);
const selectedTier = ref<PublicTier>('beginner');
const selectedMahjongType = ref<'mahjong' | 'sichuan_mahjong'>('mahjong');
const suitNames: Record<MissingSuit, string> = { m: '万', p: '筒', s: '条' };
const customBase = ref<number>(1);
const customLimit = ref<number>(100);
const settingsBase = ref<number>(1);
const settingsLimit = ref<number>(100);
const botCount = ref<number>(1);
const tiers: { id: PublicTier; title: string; subtitle: string; base: number; entry: number; limit: number }[] = [
  { id: 'beginner', title: '初级场', subtitle: '轻松入席', base: 1, entry: 100, limit: 100 },
  { id: 'intermediate', title: '中级场', subtitle: '以牌会友', base: 5, entry: 1000, limit: 500 },
  { id: 'advanced', title: '高级场', subtitle: '高手过招', base: 20, entry: 5000, limit: 2000 },
];
const tierNames: Record<RoomTier, string> = { beginner: '初级场', intermediate: '中级场', advanced: '高级场', custom: '自定义房间' };
const maxLossLimit = Math.floor((2 ** 53 - 1) / 8);
const amount = ref<number | null>(null);
const targetId = ref('');
const selectedIndices = ref<number[]>([]);
const rulesDialog = ref<HTMLDialogElement | null>(null);
const customDialog = ref<HTMLDialogElement | null>(null);
const joinDialog = ref<HTMLDialogElement | null>(null);
const settingsDialog = ref<HTMLDialogElement | null>(null);
const botsDialog = ref<HTMLDialogElement | null>(null);
const now = ref(Date.now());
type HandCard = { id: number; card: string };
type DealtBack = { id: number; userId: string; delay: number; x: number; y: number };
const renderedHand = ref<HandCard[]>([]);
const dealingCards = ref<Record<number, number>>({});
const dealingBoard = ref<Record<number, number>>({});
const dealtBacks = ref<DealtBack[]>([]);
const reducedMotion = ref(false);
const resultReady = ref(true);
const resultAnimated = ref(false);
const winNotice = ref<MahjongWinEvent | null>(null);
let winNoticeTimer: ReturnType<typeof setTimeout> | undefined;
let queuedWins: MahjongWinEvent[] = [];
let seenWinEvents = new Set<string>();
let cardSerial = 0;
let dealCleanupTimer: ReturnType<typeof setTimeout> | undefined;
let resultTimer: ReturnType<typeof setTimeout> | undefined;
let dealEndsAt = 0;
let motionPreference: MediaQueryList | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;
let clockTimer: ReturnType<typeof setInterval> | undefined;
let epoch = 0;
let pollInFlight = false;
let disposed = false;

const lobbyGameType = computed<TableRoomGameType>(() => props.gameType === 'mahjong' ? selectedMahjongType.value : props.gameType);
const currentGameType = computed(() => room.value?.game_type ?? lobbyGameType.value);
const isSichuan = computed(() => currentGameType.value === 'sichuan_mahjong');
const isMahjong = computed(() => currentGameType.value === 'mahjong' || isSichuan.value);
const maxBaseStake = computed(() => Math.floor((2 ** 53 - 1) / (isSichuan.value ? 512 : 80)));
const rules = computed(() => tableGameRules[currentGameType.value]);
const viewerId = computed(() => String(props.profile.user_id));
const storageKey = computed(() => `yueyue.table.${isMahjong.value ? 'mahjong' : currentGameType.value}.${viewerId.value}`);
const variantStorageKey = computed(() => `yueyue.mahjong.variant.${viewerId.value}`);
const game = computed(() => room.value?.game ?? null);
const viewer = computed(() => room.value?.players.find(player => String(player.user_id) === viewerId.value));
const myGame = computed(() => game.value?.players.find(player => String(player.user_id) === viewerId.value));
const myHand = computed(() => myGame.value?.hand ?? []);
const selectedCards = computed(() => selectedIndices.value.map(index => myHand.value[index]).filter((card): card is string => Boolean(card)));
const host = computed(() => String(room.value?.host_user_id) === viewerId.value);
const legalActions = computed(() => game.value?.legal_actions ?? []);
const genericActions = computed(() => legalActions.value.filter(value => !['bid', 'dingque'].includes(value) && !(value === 'chow' && game.value?.chow_options?.length) && !(value === 'kong' && game.value?.kong_options?.length)));
const missingSuitOptions = computed(() => game.value?.missing_suit_options ?? []);
const mustDiscardMissingSuit = computed(() => isSichuan.value && Boolean(myGame.value?.missing_suit && myHand.value.some(card => card[0] === myGame.value?.missing_suit)));
const phaseLabel = computed(() => isSichuan.value && ['reaction', 'response', 'responding'].includes(game.value?.phase ?? '') ? '等待碰杠胡' : phaseLabels[game.value?.phase ?? ''] || '进行中');
const currentName = computed(() => nameFor(game.value?.current_player_id));
const startReady = computed(() => Boolean(room.value && room.value.players.length >= room.value.min_players && room.value.players.every(player => player.is_ready)));
const tier = computed(() => tiers.find(item => item.id === selectedTier.value)!);
const canEnterTier = computed(() => props.profile.balance >= tier.value.entry);
const customValid = computed(() => validTerms(customBase.value, customLimit.value));
const settingsValid = computed(() => validTerms(settingsBase.value, settingsLimit.value));
const settingsChanged = computed(() => room.value?.base_stake !== settingsBase.value || room.value?.loss_limit !== settingsLimit.value);
const canManage = computed(() => host.value && room.value?.state !== 'playing');
const bots = computed(() => room.value?.players.filter(player => player.is_bot) ?? []);
const emptySeats = computed(() => Math.max(0, (room.value?.max_players ?? 0) - (room.value?.players.length ?? 0)));
const validBotCount = computed(() => Number.isInteger(botCount.value) && botCount.value >= 1 && botCount.value <= emptySeats.value);
const otherPlayers = computed(() => room.value?.players.filter(player => String(player.user_id) !== viewerId.value && !gamePlayer(player.user_id)?.folded) ?? []);
const secondsLeft = computed(() => {
  const deadline = Number(room.value?.turn_deadline ?? 0);
  if (!deadline) return null;
  return Math.max(0, Math.ceil(((deadline < 1e12 ? deadline * 1000 : deadline) - now.value) / 1000));
});
const lastPlayedCards = computed(() => {
  const last = game.value?.last_play;
  return Array.isArray(last) ? last : last?.cards ?? [];
});
const winnerText = computed(() => game.value?.winners.map(nameFor).join('、') || '本局流局 / 平局');
const isDealing = computed(() => Boolean(dealtBacks.value.length || Object.keys(dealingCards.value).length || Object.keys(dealingBoard.value).length));
const roundResult = computed(() => {
  if (!game.value?.finished || !room.value) return null;
  const winners = game.value.winners.map(String);
  const mine = winners.includes(viewerId.value);
  const mahjong = isMahjong.value;
  const title = isSichuan.value ? '血战结算' : mahjong ? !winners.length ? '流局' : mine ? '胡牌' : `${winnerText.value} 胡牌`
    : !winners.length ? '平局' : mine ? '胜利' : '失败';
  const tone = mahjong && winners.length ? 'mahjong' : !winners.length ? 'push' : mine ? 'win' : 'loss';
  const delta = room.value.actual_settlement?.[viewerId.value];
  const detail = room.value.settlement_status === 'settled' && typeof delta === 'number'
    ? `本局实际${delta > 0 ? '净赢' : delta < 0 ? '净输' : '净变动'} ${Math.abs(delta)} 灵石`
    : '结算处理中，请以实际到账为准';
  return { title, tone: tone as 'win' | 'loss' | 'push' | 'mahjong', subtitle: winners.length ? `赢家：${winnerText.value}` : '本局无人获胜', detail };
});

function clearWinNotices() {
  clearTimeout(winNoticeTimer);
  winNoticeTimer = undefined;
  queuedWins = [];
  winNotice.value = null;
}

function showNextWinNotice() {
  clearTimeout(winNoticeTimer);
  winNoticeTimer = undefined;
  winNotice.value = queuedWins.shift() ?? null;
  if (winNotice.value) winNoticeTimer = setTimeout(showNextWinNotice, 3200);
}

function updateWinNotices(current: GameState | null, reset: boolean, live: boolean) {
  const events = current?.win_events ?? [];
  if (reset || !isSichuan.value || current?.finished) {
    clearWinNotices();
    seenWinEvents = new Set(events.map(event => String(event.id)));
    return;
  }
  for (const event of events) {
    const key = String(event.id);
    if (!seenWinEvents.has(key) && live) queuedWins.push(event);
    seenWinEvents.add(key);
  }
  if (!winNotice.value && queuedWins.length && winNoticeTimer === undefined) {
    const delay = reducedMotion.value ? 0 : Math.max(0, dealEndsAt - Date.now());
    if (delay) winNoticeTimer = setTimeout(showNextWinNotice, delay);
    else showNextWinNotice();
  }
}

function signedScore(value: number | undefined) {
  const score = Number(value ?? 0);
  return `${score > 0 ? '+' : ''}${score}`;
}

function chooseMahjong(type: 'mahjong' | 'sichuan_mahjong') {
  if (busy.value || recovering.value || room.value) return;
  selectedMahjongType.value = type;
  error.value = '';
  try { localStorage.setItem(variantStorageKey.value, type); } catch { /* 存储不可用时仍可选择本次玩法。 */ }
}

function clearDealing() {
  clearTimeout(dealCleanupTimer);
  dealingCards.value = {};
  dealingBoard.value = {};
  dealtBacks.value = [];
  dealEndsAt = 0;
}

function updateMotionPreference(event?: MediaQueryListEvent) {
  reducedMotion.value = event?.matches ?? motionPreference?.matches ?? false;
  if (reducedMotion.value) {
    clearDealing();
    clearTimeout(resultTimer);
    resultAnimated.value = false;
    resultReady.value = true;
  }
}

function finishHandAnimation(id: number) {
  delete dealingCards.value[id];
}

function finishBackAnimation(id: number) {
  dealtBacks.value = dealtBacks.value.filter(card => card.id !== id);
}

function dealBackStyle(card: DealtBack) {
  return {
    left: `${card.x}%`, top: `${card.y}%`,
    '--deal-from-x': `${50 - card.x}cqw`, '--deal-from-y': `${43 - card.y}cqh`,
    animationDelay: `${card.delay}ms`,
  };
}

// 按牌值及重复次数保留实例。排序、弃牌和轮询不能让旧牌变成新发的牌。
function reconcileHand(nextCards: string[], fresh: boolean): Set<number> {
  const retained = new Map<string, HandCard[]>();
  if (!fresh) for (const item of renderedHand.value) {
    const matching = retained.get(item.card) ?? [];
    matching.push(item);
    retained.set(item.card, matching);
  }
  const added = new Set<number>();
  renderedHand.value = nextCards.map(card => {
    const existing = retained.get(card)?.shift();
    if (existing) return existing;
    const item = { id: ++cardSerial, card };
    added.add(item.id);
    return item;
  });
  const activeIds = new Set(renderedHand.value.map(card => card.id));
  for (const id of Object.keys(dealingCards.value)) if (!activeIds.has(Number(id))) delete dealingCards.value[Number(id)];
  return added;
}

function animateSnapshot(previous: RoomState | null, next: RoomState, live: boolean) {
  const sameRoom = previous?.room_id === next.room_id;
  const before = sameRoom ? previous?.game : null;
  const current = next.game;
  const newDeal = Boolean(current && sameRoom && (!before
    || (next.round_number !== undefined && previous?.round_number !== undefined && next.round_number > previous.round_number)
    || (current.deal_count !== undefined && before.deal_count !== undefined && current.deal_count > before.deal_count)
    || (before.finished && !current.finished)));
  if (!sameRoom || newDeal || !live) {
    clearDealing();
    clearTimeout(resultTimer);
    resultAnimated.value = false;
    resultReady.value = true;
  }
  const ownHand = current?.players.find(player => String(player.user_id) === viewerId.value)?.hand ?? [];
  const added = reconcileHand(ownHand, !sameRoom || newDeal);
  const canAnimate = live && sameRoom && !reducedMotion.value;
  if (current && canAnimate) {
    const members = next.players;
    const viewerIndex = members.findIndex(member => String(member.user_id) === viewerId.value);
    const ordered = viewerIndex < 0 ? members : [...members.slice(viewerIndex), ...members.slice(0, viewerIndex)];
    let latestDelay = -1;
    ordered.forEach((member, seat) => {
      const player = current.players.find(item => String(item.user_id) === String(member.user_id));
      const oldPlayer = before?.players.find(item => String(item.user_id) === String(member.user_id));
      const count = player?.hand_count ?? player?.hand.length ?? 0;
      const oldCount = oldPlayer?.hand_count ?? oldPlayer?.hand.length ?? 0;
      // 摊牌/看牌不算新增手牌，只有新局和实际数量增加才发牌。
      const ownAdditions = String(member.user_id) === viewerId.value && oldPlayer?.hand.length ? added.size : 0;
      const incoming = newDeal ? count : !current.finished ? Math.max(0, count - oldCount, ownAdditions) : 0;
      if (!incoming) return;
      const position = seatStyle(seat);
      const x = seat === 0 ? 54 : Number.parseFloat(position['--seat-x']);
      const y = seat === 0 ? 85 : Math.min(74, Number.parseFloat(position['--seat-y']) + 12);
      for (let index = 0; index < incoming; index++) {
        const delay = newDeal ? (index * ordered.length + seat) * 22 : index * 60;
        latestDelay = Math.max(latestDelay, delay);
        dealtBacks.value.push({ id: ++cardSerial, userId: String(member.user_id), delay, x, y });
      }
      if (String(member.user_id) === viewerId.value) {
        let incomingIndex = 0;
        for (const item of renderedHand.value) if (added.has(item.id)) {
          const delay = newDeal ? incomingIndex * ordered.length * 22 : incomingIndex * 60;
          dealingCards.value[item.id] = delay;
          latestDelay = Math.max(latestDelay, delay);
          incomingIndex++;
        }
      }
    });
    const previousBoard = newDeal ? 0 : before?.community_cards?.length ?? 0;
    for (let index = previousBoard; index < (current.community_cards?.length ?? 0); index++) {
      const delay = (index - previousBoard) * 90;
      dealingBoard.value[index] = delay;
      latestDelay = Math.max(latestDelay, delay);
    }
    if (latestDelay >= 0) {
      dealEndsAt = Math.max(dealEndsAt, Date.now() + latestDelay + 440);
      clearTimeout(dealCleanupTimer);
      dealCleanupTimer = setTimeout(clearDealing, dealEndsAt - Date.now() + 60);
    }
  }
  if (current?.finished && canAnimate && (!before?.finished || newDeal)) {
    clearTimeout(resultTimer);
    resultReady.value = false;
    const showResult = () => { resultReady.value = true; resultAnimated.value = true; };
    const delay = Math.max(0, dealEndsAt - Date.now());
    if (delay) resultTimer = setTimeout(showResult, delay);
    else showResult();
  }
  if (newDeal) {
    clearWinNotices();
    seenWinEvents.clear();
  }
  updateWinNotices(current, !sameRoom || !live, live);
}

// 按玩家视角旋转座次，自己的位置始终在桌子下方，不改变服务端行动顺序。
const seatedPlayers = computed(() => {
  const members = room.value?.players ?? [];
  const selfIndex = members.findIndex(member => String(member.user_id) === viewerId.value);
  return selfIndex < 0 ? members : [...members.slice(selfIndex), ...members.slice(0, selfIndex)];
});
const seatCount = computed(() => Math.max(seatedPlayers.value.length, room.value?.min_players ?? 2));
const seatPositions: Record<number, number[][]> = {
  2: [[8, 83], [50, 17]],
  3: [[8, 83], [17, 27], [83, 27]],
  4: [[8, 83], [17, 31], [50, 15], [83, 31]],
  5: [[8, 83], [12, 35], [37, 16], [63, 16], [88, 35]],
  6: [[8, 83], [10, 43], [29, 21], [50, 14], [71, 21], [90, 43]],
  7: [[8, 83], [10, 49], [23, 28], [41, 15], [59, 15], [77, 28], [90, 49]],
  8: [[8, 83], [8, 49], [20, 31], [35, 17], [50, 12], [65, 17], [80, 31], [92, 49]],
};
function seatStyle(index: number) {
  // 斗地主和麻将的下家在右侧，服务端正向轮转对应屏幕逆时针。
  const positions = isMahjong.value
    ? [[6, 76], [94, 37], [74, 13], [6, 37]]
    : currentGameType.value === 'landlord' ? [[7, 82], [91, 24], [9, 24]] : seatPositions[seatCount.value];
  const position = positions?.[index] ?? [50, 50];
  return { '--seat-x': `${position[0]}%`, '--seat-y': `${position[1]}%` };
}
function seatDirection(index: number) {
  if (index === 0) return 'south';
  if (isMahjong.value) return ['south', 'east', 'north', 'west'][index] ?? 'north';
  if (currentGameType.value === 'landlord') return ['south', 'east', 'west'][index] ?? 'north';
  const position = seatPositions[seatCount.value]?.[index] ?? [50, 50];
  if (position[1]! < 25) return 'north';
  return position[0]! < 50 ? 'west' : 'east';
}

const labels: Record<string, string> = {
  fold: '弃牌', check: '过牌', call: '跟注', raise: '加注', all_in: '全下',
  look: '看牌', compare: '比牌', play: '出牌', pass: '不要 / 过',
  discard: '弃选中的牌', chow: '吃', pung: '碰', kong: '杠', win: '胡牌',
};
const phaseLabels: Record<string, string> = {
  preflop: '翻牌前', flop: '翻牌', turn: '转牌', river: '河牌', showdown: '摊牌',
  betting: '下注中', bidding: '叫分', playing: '出牌中', discard: '出牌',
  response: '等待吃碰杠胡', responding: '等待吃碰杠胡', finished: '本局结束',
  reaction: '等待吃碰杠胡', dingque: '选择定缺',
};

function nameFor(id: string | null | undefined): string {
  if (!id) return '';
  return room.value?.players.find(player => String(player.user_id) === String(id))?.username ?? String(id);
}

function gamePlayer(id: string): GamePlayer | undefined {
  return game.value?.players.find(player => String(player.user_id) === String(id));
}

function seatAction(id: string): SeatAction | undefined {
  return game.value?.seat_actions?.[id];
}

function settledDelta(id: string): number {
  return Number(room.value?.actual_settlement?.[id] ?? gamePlayer(id)?.score_delta ?? gamePlayer(id)?.score ?? 0);
}

function cardLabel(card: string): string {
  if (card === 'JokerSmall') return '小王';
  if (card === 'JokerBig') return '大王';
  const suit = { Club: '♣', Diamond: '♦', Heart: '♥', Spade: '♠' };
  for (const [key, symbol] of Object.entries(suit)) if (card.startsWith(key)) return symbol + card.slice(key.length);
  if (/^[mps][1-9]$/.test(card)) return `${card[1]}${{ m: '万', p: '筒', s: '条' }[card[0] as 'm' | 'p' | 's']}`;
  if (/^z[1-7]$/.test(card)) return ['东', '南', '西', '北', '中', '发', '白'][Number(card[1]) - 1] ?? card;
  return card;
}

function cardImage(card: string): string | null {
  if (card === 'JokerSmall') return '/cards/JOKER-B.webp';
  if (card === 'JokerBig') return '/cards/JOKER-A.webp';
  if (/^(Club|Diamond|Heart|Spade)([2-9]|10|J|Q|K|A)$/.test(card)) return `/cards/${card}.webp`;
  if (/^([mps][1-9]|z[1-7])$/.test(card)) return `/mahjong/faces/${card}.svg`;
  return null;
}

function publicTiles(value: unknown): string[] {
  if (typeof value === 'string') return [value];
  if (Array.isArray(value)) return value.flatMap(publicTiles);
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>;
    return publicTiles(item.tiles ?? item.cards ?? item.tile ?? []);
  }
  return [];
}

function discardsFor(id: string): string[] {
  const playerDiscards = gamePlayer(id)?.discards;
  if (playerDiscards) return playerDiscards;
  const discards = game.value?.discards;
  if (!discards || Array.isArray(discards)) return [];
  return publicTiles(discards[id]);
}

function meldsFor(id: string): string[] {
  return publicTiles(gamePlayer(id)?.melds ?? game.value?.melds?.[id] ?? []);
}

function rememberRoom(id: string | null) {
  try {
    if (id) localStorage.setItem(storageKey.value, id);
    else localStorage.removeItem(storageKey.value);
  } catch { /* 嵌入页面禁用存储时仍可继续当前牌局。 */ }
}

function validTerms(base: number, limit: number) {
  return Number.isSafeInteger(base) && base >= 1 && base <= maxBaseStake.value
    && Number.isSafeInteger(limit) && limit >= Math.max(100, base * 10)
    && limit <= maxLossLimit && limit <= props.profile.balance;
}

function closeRoomDialogs() {
  settingsDialog.value?.close();
  botsDialog.value?.close();
}

function openSettings() {
  if (!room.value) return;
  settingsBase.value = room.value.base_stake;
  settingsLimit.value = room.value.loss_limit;
  error.value = '';
  settingsDialog.value?.showModal();
}

function openBots() {
  botCount.value = 1;
  error.value = '';
  botsDialog.value?.showModal();
}

function applyRoom(data: RoomEnvelope, live = true) {
  if (typeof data.viewer_balance === 'number') emit('balance', data.viewer_balance);
  if (!data.room) {
    clearDealing();
    clearTimeout(resultTimer);
    renderedHand.value = [];
    resultReady.value = true;
    resultAnimated.value = false;
    clearWinNotices();
    seenWinEvents.clear();
    closeRoomDialogs();
    rememberRoom(null);
    room.value = null;
    return;
  }
  if (room.value?.room_id === data.room.room_id && data.room.revision < room.value.revision) return;
  const oldHand = myHand.value.join(',');
  const previous = room.value;
  room.value = data.room;
  if (data.room.game_type === 'mahjong' || data.room.game_type === 'sichuan_mahjong') {
    selectedMahjongType.value = data.room.game_type;
    try { localStorage.setItem(variantStorageKey.value, data.room.game_type); } catch { /* 房间恢复不依赖本地存储可写。 */ }
  }
  animateSnapshot(previous, data.room, live);
  if (data.room.state === 'playing') closeRoomDialogs();
  rememberRoom(data.room.room_id);
  if (oldHand !== myHand.value.join(',')) selectedIndices.value = [];
  if (!otherPlayers.value.some(player => player.user_id === targetId.value)) targetId.value = otherPlayers.value[0]?.user_id ?? '';
  if (game.value?.min_raise_to !== undefined && (!amount.value || amount.value < game.value.min_raise_to)) amount.value = game.value.min_raise_to;
}

async function refresh() {
  if (!room.value || busy.value || pollInFlight || disposed) return;
  pollInFlight = true;
  const requestEpoch = epoch;
  const roomId = room.value.room_id;
  try {
    const data = await props.apiCall<RoomEnvelope>(`/api/tables/${encodeURIComponent(roomId)}`, 'GET', undefined, 0);
    if (!disposed && requestEpoch === epoch && room.value?.room_id === roomId) applyRoom(data);
  } catch (reason) {
    if (!disposed && requestEpoch === epoch) error.value = reason instanceof Error ? reason.message : '同步失败，将自动重试';
  } finally { pollInFlight = false; }
}

async function request(path: string, body: Record<string, unknown>): Promise<boolean> {
  if (busy.value) return false;
  busy.value = true;
  error.value = '';
  const requestEpoch = ++epoch;
  try {
    const data = await props.apiCall<RoomEnvelope>(`/api/tables/${path}`, 'POST', body, 0);
    if (disposed || requestEpoch !== epoch) return false;
    applyRoom(data, path !== 'join' && path !== 'create');
    return true;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '操作失败，请重试';
    return false;
  } finally {
    busy.value = false;
    // 失败后同样重新同步，避免重复提交已经执行的动作。
    if (!disposed) void refresh();
  }
}

async function create(mode: 'solo' | 'multi', roomTier: RoomTier = selectedTier.value) {
  if (roomTier === 'custom' ? !customValid.value : !canEnterTier.value) {
    error.value = roomTier === 'custom' ? '请检查底分、单局上限和账户余额' : `进入${tier.value.title}需要至少 ${tier.value.entry} 灵石`;
    return;
  }
  const payload: Record<string, unknown> = { game_type: lobbyGameType.value, mode, include_yueyue: mode === 'solo' || includeYueyue.value, room_tier: roomTier };
  if (roomTier === 'custom') Object.assign(payload, { base_stake: customBase.value, loss_limit: customLimit.value });
  if (await request('create', payload)) customDialog.value?.close();
}

async function join() {
  const id = roomInput.value.trim().toUpperCase();
  if (!id) { error.value = '请输入房间号'; return; }
  if (await request('join', { room_id: id })) joinDialog.value?.close();
}

async function saveSettings() {
  if (!room.value || !canManage.value || !settingsValid.value || !settingsChanged.value) return;
  if (await request('settings', { room_id: room.value.room_id, base_stake: settingsBase.value, loss_limit: settingsLimit.value })) settingsDialog.value?.close();
}

async function addBots() {
  if (!room.value || !canManage.value || !validBotCount.value) return;
  await request('bots', { room_id: room.value.room_id, operation: 'add', count: botCount.value });
  botCount.value = Math.min(botCount.value, Math.max(1, emptySeats.value));
}

async function removeBot(id: string) {
  if (!room.value || !canManage.value) return;
  await request('bots', { room_id: room.value.room_id, operation: 'remove', bot_id: id });
}

async function leave() {
  if (!room.value) return;
  await request('leave', { room_id: room.value.room_id });
}

function toggleCard(index: number) {
  if (busy.value || game.value?.finished || myGame.value?.has_won || game.value?.phase === 'dingque') return;
  if (isMahjong.value && !legalActions.value.includes('chow')) {
    selectedIndices.value = selectedIndices.value.includes(index) ? [] : [index];
    return;
  }
  selectedIndices.value = selectedIndices.value.includes(index)
    ? selectedIndices.value.filter(value => value !== index)
    : [...selectedIndices.value, index];
}

function actionDisabled(action: string): boolean {
  if (busy.value) return true;
  if (action === 'play') return selectedCards.value.length === 0;
  if (action === 'discard') return selectedCards.value.length !== 1 || (mustDiscardMissingSuit.value && selectedCards.value[0]?.[0] !== myGame.value?.missing_suit);
  if (action === 'chow') return selectedCards.value.length !== 2;
  if (action === 'raise') {
    const target = Number(amount.value);
    const maximum = game.value?.max_raise_to ?? Number.MAX_SAFE_INTEGER;
    const minimum = game.value?.min_raise_to ?? 1;
    return !Number.isSafeInteger(target) || target <= Number(game.value?.current_bet ?? 0)
      || target > maximum || (target < minimum && !(currentGameType.value === 'texas' && target === maximum));
  }
  if (action === 'compare') return !targetId.value;
  return false;
}

function quickRaise(multiplier: 2 | 4) {
  if (busy.value || !game.value || !legalActions.value.includes('raise')) return;
  // 无人下注时以大盲为基数；短筹码允许填入全下金额，仅填值不提交。
  const basis = Math.max(Number(game.value.current_bet ?? 0), (room.value?.base_stake ?? 1) * 2);
  const minimum = game.value.min_raise_to ?? 1;
  const maximum = game.value.max_raise_to ?? Number.MAX_SAFE_INTEGER;
  amount.value = Math.min(maximum, Math.max(minimum, basis * multiplier));
}

async function joinListedRoom(listed: { room_id: string; game_type: string }): Promise<boolean> {
  if (listed.game_type === 'blackjack') throw new Error('请从大厅的房间列表加入21点房间');
  const data = await props.apiCall<RoomEnvelope>('/api/tables/join', 'POST', { room_id: listed.room_id }, 0);
  applyRoom(data, false);
  return true;
}

async function act(action: string, extra: Record<string, unknown> = {}) {
  if (!room.value) return;
  const payload: Record<string, unknown> = { room_id: room.value.room_id, expected_revision: room.value.revision, action, ...extra };
  if (action === 'raise') payload.amount = Number(amount.value);
  if (action === 'compare') payload.target_id = targetId.value;
  if (action === 'play') payload.cards = selectedCards.value;
  if (action === 'discard' || (action === 'kong' && selectedCards.value.length && !extra.tile)) payload.tile = selectedCards.value[0];
  if (action === 'chow' && !extra.tiles) payload.tiles = selectedCards.value;
  await request('action', payload);
}

onMounted(async () => {
  motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  updateMotionPreference();
  motionPreference.addEventListener('change', updateMotionPreference);
  if (props.gameType === 'mahjong') {
    try { if (localStorage.getItem(variantStorageKey.value) === 'sichuan_mahjong') selectedMahjongType.value = 'sichuan_mahjong'; } catch { /* 无存储时默认基础麻将。 */ }
  }
  let savedRoom = '';
  try { savedRoom = localStorage.getItem(storageKey.value) ?? ''; } catch { /* 存储不可用时从大厅进入。 */ }
  savedRoom = props.initialRoomId || savedRoom;
  if (savedRoom) {
    try {
      const data = await props.apiCall<RoomEnvelope>('/api/tables/join', 'POST', { room_id: savedRoom }, 0);
      if (!disposed) applyRoom(data, false);
    } catch (reason) {
      if (props.initialRoomId) {
        emit('joinFailed', reason instanceof Error ? `入座失败：${reason.message}` : '房间暂时无法加入');
        return;
      }
      roomInput.value = savedRoom;
      error.value = reason instanceof Error ? `房间恢复失败：${reason.message}，可用房间号重新加入` : '房间恢复失败，可用房间号重新加入';
    }
  }
  recovering.value = false;
  if (disposed) return;
  pollTimer = setInterval(() => void refresh(), 1500);
  clockTimer = setInterval(() => { now.value = Date.now(); }, 1000);
});

onBeforeUnmount(() => {
  disposed = true;
  epoch++;
  clearInterval(pollTimer);
  clearInterval(clockTimer);
  clearDealing();
  clearTimeout(resultTimer);
  clearWinNotices();
  motionPreference?.removeEventListener('change', updateMotionPreference);
});
</script>

<template>
  <section class="table-games" :data-dealing="isDealing" :class="[{ 'has-room': room, 'tg-waiting': room?.state !== 'playing', 'tg-finished': game?.finished, 'tg-playing-cards': game?.phase === 'playing' && !game.finished, 'game-mahjong': isMahjong, 'tg-dingque': game?.phase === 'dingque' }, `game-${currentGameType}`]">
    <header class="tg-toolbar">
      <div class="tg-title"><h2>{{ rules.title }}<span v-if="room" class="tg-tier-tag">{{ tierNames[room.room_tier] }}</span></h2><p v-if="room" :title="game?.message">房间 {{ room.room_id }} · {{ room.state === 'waiting' ? '等待准备' : room.state === 'finished' ? '本局结束' : phaseLabel }}<span v-if="game && !game.finished && ['texas', 'golden_flower'].includes(currentGameType)" class="tg-compact-notice"> · {{ game.message }}</span></p><p v-else>{{ rules.summary }}</p></div>
      <div class="tg-actions">
        <button class="game-button quiet" @click="rulesDialog?.showModal()">玩法规则</button>
        <CopyRoomCode v-if="room" :room-id="room.room_id" />
        <button v-if="room" class="game-button quiet" :disabled="busy" @click="refresh">同步</button>
        <button v-if="room?.mode === 'multi'" class="game-button quiet" :disabled="busy" @click="emit('invite', { room_id: room.room_id, game_type: room.game_type })">招募队友</button>
        <button v-if="room" class="game-button quiet" :disabled="busy" @click="leave">离开房间</button>
        <button v-else class="game-button quiet" :disabled="busy || recovering" @click="emit('back')">返回大厅</button>
      </div>
    </header>

    <RoomDirectory v-if="showRoomDirectory" :api-call="apiCall" :balance="profile.balance" :game-type="lobbyGameType" restrict-game :join-room="joinListedRoom" @close="showRoomDirectory = false" />
    <div v-if="recovering" class="tg-lobby" role="status">正在恢复房间…</div>
    <div v-else-if="!room" class="tg-lobby">
      <div class="tg-lobby-inner">
        <div class="tg-lobby-heading"><div><span class="tg-eyebrow">月月牌室 · 选择场次</span><div v-if="props.gameType === 'mahjong'" class="tg-variant-tabs" role="group" aria-label="选择麻将模式"><button class="game-button" :class="{ gold: selectedMahjongType === 'mahjong' }" :aria-pressed="selectedMahjongType === 'mahjong'" :disabled="busy" @click="chooseMahjong('mahjong')">基础麻将</button><button class="game-button" :class="{ gold: selectedMahjongType === 'sichuan_mahjong' }" :aria-pressed="selectedMahjongType === 'sichuan_mahjong'" :disabled="busy" @click="chooseMahjong('sichuan_mahjong')">四川血战</button></div><h3 v-else>入席，好牌等你</h3><p>{{ rules.players }}</p></div><div class="tg-lobby-balance"><span>我的灵石</span><strong>{{ profile.balance.toLocaleString() }}</strong></div></div>
        <div class="tg-tier-grid" role="group" aria-label="选择游戏场次">
          <button v-for="item in tiers" :key="item.id" class="tg-tier-card" :class="[`tg-tier-${item.id}`, { selected: selectedTier === item.id }]" :aria-label="`选择${item.title}`" :aria-pressed="selectedTier === item.id" :disabled="busy" @click="selectedTier = item.id">
            <svg class="tg-scroll-frame" viewBox="0 0 300 280" preserveAspectRatio="none" fill="none" aria-hidden="true"><path d="M22 9h256l10 14v234l-10 14H22l-10-14V23Z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M18 24h28V12m-27 26h14V25m249-1h-28V12m27 26h-14V25M18 256h28v12m-27-26h14v13m249 1h-28v12m27-26h-14v13" stroke="currentColor" stroke-width="3"/><path d="M109 14h25l16-8 16 8h25M109 266h25l16 8 16-8h25" stroke="currentColor" stroke-width="2"/></svg>
            <GameIcon class="tg-tier-art" :name="item.id" />
            <div class="tg-tier-heading"><span>{{ item.subtitle }}</span><strong>{{ item.title }}</strong></div>
            <div class="tg-tier-terms"><span>底分 <b>{{ item.base }}</b></span><span>准入 <b>{{ item.entry }}</b></span><span>单局最多输 <b>{{ item.limit }}</b></span></div>
            <span class="tg-tier-selected">{{ selectedTier === item.id ? '已选场次' : '选择场次' }}</span>
          </button>
        </div>
        <div class="tg-entry-row">
          <button class="tg-mode-card tg-solo-entry" aria-label="月月陪玩" :disabled="busy || !canEnterTier" @click="create('solo')"><GameIcon name="solo" /><span><strong>月月陪玩</strong><small>自动配齐，随时开局</small></span><span class="tg-entry-arrow" aria-hidden="true">›</span></button>
          <div class="tg-friends-entry"><button class="tg-mode-card" aria-label="好友同桌" :disabled="busy || !canEnterTier" @click="create('multi')"><GameIcon name="friends" /><span><strong>好友同桌</strong><small>创建{{ tier.title }}，邀请朋友</small></span><span class="tg-entry-arrow" aria-hidden="true">›</span></button><label class="tg-check"><input v-model="includeYueyue" type="checkbox" :disabled="busy">邀请月月一起玩</label></div>
        </div>
        <div class="tg-lobby-footer"><p :class="{ 'tg-entry-insufficient': !canEnterTier }">{{ canEnterTier ? `${tier.title} · 底分 ${tier.base} · 准入 ${tier.entry} 灵石` : `进入${tier.title}还需 ${tier.entry - profile.balance} 灵石` }}</p><div class="tg-actions"><button class="game-button" :disabled="busy" @click="showRoomDirectory = true">房间列表</button><button class="game-button quiet" :disabled="busy" @click="error = ''; customDialog?.showModal()"><GameIcon name="room" />自定义房间</button><button class="game-button gold" :disabled="busy" @click="error = ''; joinDialog?.showModal()">加入房间</button></div></div>
      </div>
    </div>

    <template v-else>
      <main class="tg-scroll" tabindex="0" aria-label="桌游牌桌">
        <div v-if="!['texas', 'golden_flower'].includes(currentGameType)" v-show="!winNotice" class="tg-table-status">
          <p v-if="room.state === 'waiting'" class="tg-notice">{{ room.players.length }}/{{ room.max_players }} 人已入座 · 准备后开始</p>
          <p v-else-if="game && !game.finished" class="tg-notice" role="status">{{ game.message }}</p>
          <span v-if="currentGameType !== 'landlord' && game && !game.finished && game.current_player_id" class="tg-turn">{{ currentName }} 的回合 <b v-if="secondsLeft !== null">{{ secondsLeft }}s</b></span>
        </div>
        <div class="tg-arena" :class="[{ 'tg-mahjong-table': isMahjong, 'tg-crowded': seatCount > 5 }, `tg-count-${seatCount}`]">
          <div class="tg-felt" aria-hidden="true"></div>
          <div v-if="dealtBacks.length" class="tg-deal-layer" aria-hidden="true">
            <i v-for="card in dealtBacks" :key="card.id" class="tg-deal-card" :class="{ 'tg-deal-tile': isMahjong }" :data-seat-id="card.userId" :data-card-dealing="true" :style="dealBackStyle(card)" @animationend="finishBackAnimation(card.id)"><img v-if="!isMahjong" src="/table-assets/card-back.png" alt=""></i>
          </div>
          <div v-if="roundResult && resultReady" class="tg-round-result">
            <RoundFeedback :title="roundResult.title" :subtitle="roundResult.subtitle" :detail="roundResult.detail" :tone="roundResult.tone" :animated="resultAnimated" :aria-label="isMahjong && game?.winners.length ? '胡牌结果' : '本局结果'" />
          </div>
          <div v-if="winNotice && !game?.finished" class="tg-win-notice" :data-win-event-id="winNotice.id">
            <RoundFeedback :key="winNotice.id" title="胡牌 · 血战继续" :subtitle="`${nameFor(winNotice.user_id)} · ${winNotice.fan}番 ${winNotice.label}${winNotice.tile ? ` · 胡${cardLabel(winNotice.tile)}` : ''}`" detail="本局尚未结束 · 灵石待结算" tone="mahjong" :animated="!reducedMotion" aria-label="血战途中胡牌" />
          </div>
          <div v-if="currentGameType === 'landlord' && game" class="tg-bottom-cards" aria-label="地主底牌"><small>地主底牌</small><div class="tg-public-cards"><div v-for="index in 3" :key="index" class="tg-board-card"><img v-if="game.bottom_cards?.[index - 1] && cardImage(game.bottom_cards[index - 1]!)" :src="cardImage(game.bottom_cards[index - 1]!)!" :alt="cardLabel(game.bottom_cards[index - 1]!)"><img v-else src="/table-assets/card-back.png" alt="未公开底牌"></div></div></div>
          <div v-if="isMahjong && game" class="tg-walls" aria-hidden="true"><div v-for="side in ['north', 'west', 'east']" :key="side" class="tg-wall" :class="`tg-wall-${side}`"><i v-for="tile in 13" :key="tile"></i></div></div>
          <div v-if="currentGameType === 'landlord' && game" class="tg-seat-plays">
            <section v-for="(member, index) in seatedPlayers" :key="member.user_id" v-show="seatAction(member.user_id)" class="tg-seat-play" :class="`tg-play-${index === 0 ? 'self' : seatDirection(index)}`" :data-user-id="member.user_id" :aria-label="`${member.username}的出牌`">
              <span class="tg-play-label" :class="{ 'tg-pass-label': seatAction(member.user_id)?.action === 'pass' }">{{ seatAction(member.user_id)?.label }}</span>
              <div v-if="seatAction(member.user_id)?.cards.length" class="tg-played-cards" :style="{ '--public-count': seatAction(member.user_id)?.cards.length ?? 1 }"><div v-for="(card, cardIndex) in seatAction(member.user_id)?.cards" :key="cardIndex" class="tg-board-card"><img v-if="cardImage(card)" :src="cardImage(card)!" :alt="cardLabel(card)"><span v-else>{{ cardLabel(card) }}</span></div></div>
            </section>
          </div>
          <div class="tg-center" :class="{ 'tg-center-mahjong': isMahjong }">
            <div v-if="['texas', 'golden_flower'].includes(currentGameType) && !game?.finished" class="tg-table-status tg-poker-status">
              <p v-if="room.state === 'waiting'" class="tg-notice">{{ room.players.length }}/{{ room.max_players }} 人已入座 · 准备后开始</p>
              <template v-else-if="game">
                <p class="tg-notice" role="status" :title="game.message">{{ game.message }}</p>
                <span v-if="game.current_player_id" class="tg-turn"><span>{{ currentName }} 的回合</span><b v-if="secondsLeft !== null">{{ secondsLeft }}s</b></span>
              </template>
            </div>
            <template v-if="game">
              <div v-if="game.pot !== undefined" class="tg-pot"><i class="tg-chip-stack" aria-hidden="true"></i><div><small>底池</small><strong>{{ game.pot }} <span>灵石</span></strong></div></div>
              <div v-if="currentGameType === 'texas'" class="tg-public-cards" aria-label="公共牌">
                <div v-for="index in 5" :key="index" class="tg-board-card" :data-card-dealing="dealingBoard[index - 1] !== undefined" :style="{ '--deal-delay': `${dealingBoard[index - 1] ?? 0}ms` }" :class="{ 'tg-card-slot': !game.community_cards?.[index - 1] }" @animationend="delete dealingBoard[index - 1]">
                  <img v-if="game.community_cards?.[index - 1] && cardImage(game.community_cards[index - 1]!)" :src="cardImage(game.community_cards[index - 1]!)!" :alt="cardLabel(game.community_cards[index - 1]!)"><span v-else aria-hidden="true">♠</span>
                </div>
              </div>
              <p v-if="game.current_bet !== undefined" class="tg-table-caption">{{ phaseLabels[game.phase] || '下注中' }} · 当前桌注 {{ game.current_bet }}</p>
              <div v-if="currentGameType !== 'landlord' && lastPlayedCards.length" class="tg-last-play"><small>上手出牌</small><div class="tg-played-cards" :style="{ '--public-count': lastPlayedCards.length }"><div v-for="(card, index) in lastPlayedCards" :key="index" class="tg-board-card"><img v-if="cardImage(card)" :src="cardImage(card)!" :alt="cardLabel(card)"><span v-else>{{ cardLabel(card) }}</span></div></div></div>
              <template v-if="isMahjong">
                <div class="tg-mahjong-compass"><span>东</span><strong>{{ game.wall_count ?? '—' }}</strong><small>余牌</small></div>
                <div class="tg-rivers">
                  <div v-for="(member, index) in seatedPlayers" :key="member.user_id" class="tg-river" :class="`tg-river-${seatDirection(index)}`">
                    <small>{{ index === 0 ? '你' : member.username }}</small>
                    <div class="tg-tile-row"><span v-for="(tile, tileIndex) in discardsFor(member.user_id)" :key="tileIndex" class="tg-river-tile"><img :src="cardImage(tile)!" :alt="cardLabel(tile)"><span class="tg-sr-only">{{ cardLabel(tile) }}</span></span></div>
                  </div>
                </div>
                <p v-if="game.last_discard" class="tg-table-caption">{{ nameFor(game.last_discard.user_id) }} 打出 {{ cardLabel(game.last_discard.tile) }}</p>
              </template>
            </template>
            <div v-else class="tg-table-welcome"><span>♠ ♥ ♣ ♦</span><strong>{{ rules.title }}</strong><p>{{ tierNames[room.room_tier] }} · 底分 {{ room.base_stake }} 灵石</p><small>等待玩家准备</small></div>
          </div>
          <div class="tg-seats">
            <section v-for="(member, index) in seatedPlayers" :key="member.user_id" class="tg-seat" :style="seatStyle(index)" :data-position="seatDirection(index)" :data-has-won="Boolean(gamePlayer(member.user_id)?.has_won)" :class="{ 'tg-self': String(member.user_id) === viewerId, 'tg-current': String(member.user_id) === String(game?.current_player_id) && !game?.finished, 'tg-folded': gamePlayer(member.user_id)?.folded, 'tg-has-won': gamePlayer(member.user_id)?.has_won }">
              <div class="tg-player">
                <div class="tg-avatar"><img :src="member.avatar_url || '/character/normal.webp'" :alt="`${member.username}头像`"><span v-if="String(member.user_id) === String(room.host_user_id)" class="tg-host-badge" title="房主">主</span></div>
                <div class="tg-nameplate"><h3 :title="member.username">{{ member.username }}</h3><span v-if="String(member.user_id) === viewerId" class="tg-seat-tag">你</span><span v-else-if="member.is_bot" class="tg-seat-tag">AI</span><span v-if="gamePlayer(member.user_id)?.role && gamePlayer(member.user_id)?.role !== 'unknown'" class="tg-role">{{ gamePlayer(member.user_id)?.role === 'landlord' ? '地主' : '农民' }}</span></div>
                <p v-if="room.state === 'waiting'" class="tg-seat-state" :class="{ 'tg-ready': member.is_ready }">{{ member.is_ready ? '已准备' : '等待准备' }}</p>
                <p v-else-if="isSichuan && game && !game.finished" class="tg-sichuan-pending" :aria-label="`${member.username}的待结算理论分`">待结算理论 {{ signedScore(gamePlayer(member.user_id)?.score_delta) }}</p>
                <p v-else-if="game" class="tg-seat-chips"><i aria-hidden="true"></i>{{ gamePlayer(member.user_id)?.stack ?? gamePlayer(member.user_id)?.chips ?? room.buy_in ?? 100 }}<small>灵石</small></p>
              </div>
              <p v-if="isSichuan && game" class="tg-sichuan-seat-status"><span v-if="gamePlayer(member.user_id)?.missing_suit" class="tg-missing-suit">缺{{ suitNames[gamePlayer(member.user_id)!.missing_suit!] }}</span><strong v-if="gamePlayer(member.user_id)?.has_won" :title="`${gamePlayer(member.user_id)?.win_label ?? '胡牌'} · ${gamePlayer(member.user_id)?.win_fan ?? 1}番${gamePlayer(member.user_id)?.winning_tile ? ` · 胡${cardLabel(gamePlayer(member.user_id)!.winning_tile!)}` : ''}`">第{{ gamePlayer(member.user_id)?.win_order }}胡 · {{ gamePlayer(member.user_id)?.win_fan }}番</strong><span v-else-if="game.phase === 'dingque'">定缺中</span></p>
              <p v-if="gamePlayer(member.user_id)?.folded" class="tg-seat-state">已弃牌</p><p v-else-if="member.connected === false && !member.is_bot" class="tg-seat-state">托管中</p>
              <p v-if="gamePlayer(member.user_id)?.hand_name" class="tg-hand-name">{{ gamePlayer(member.user_id)?.hand_name }}</p>
              <p v-if="game?.finished" class="tg-settled" :class="{ 'tg-positive': settledDelta(member.user_id) > 0 }">{{ room.actual_settlement ? '实际净变动' : '待结算净变动' }} {{ settledDelta(member.user_id) > 0 ? '+' : '' }}{{ settledDelta(member.user_id) }} 灵石</p>
              <div v-if="game?.finished && String(member.user_id) !== viewerId && gamePlayer(member.user_id)?.hand.length" class="tg-opponent-hand" :style="{ '--public-count': gamePlayer(member.user_id)?.hand.length ?? 1 }"><img v-for="(card, cardIndex) in gamePlayer(member.user_id)?.hand" :key="cardIndex" :src="cardImage(card) || '/table-assets/card-back.png'" :alt="cardLabel(card)"></div>
              <div v-else-if="String(member.user_id) !== viewerId && gamePlayer(member.user_id)?.hand_count" class="tg-hidden-hand" :aria-label="`${gamePlayer(member.user_id)?.hand_count} 张暗牌`"><i v-for="back in Math.min(gamePlayer(member.user_id)?.hand_count ?? 0, 3)" :key="back" :class="{ 'tg-tile-back': isMahjong }"></i><span>{{ gamePlayer(member.user_id)?.hand_count }} 张</span></div>
              <div v-if="meldsFor(member.user_id).length" class="tg-melds" aria-label="副露"><span v-for="(tile, tileIndex) in meldsFor(member.user_id)" :key="tileIndex" class="tg-river-tile"><img :src="cardImage(tile)!" :alt="cardLabel(tile)"></span></div>
            </section>
            <div v-for="emptyIndex in Math.max(0, seatCount - seatedPlayers.length)" :key="`empty-${emptyIndex}`" class="tg-empty-seat" :style="seatStyle(seatedPlayers.length + emptyIndex - 1)"><span>＋</span><small>空位</small></div>
          </div>
        </div>
        <p v-if="room.state === 'playing'" class="tg-table-footnote">离桌后 AI 托管 · 刷新可恢复座位</p>
      </main>

      <footer class="tg-dock" aria-label="桌游操作区">
        <div v-if="currentGameType === 'landlord' && game && !game.finished && game.current_player_id" class="tg-countdown" aria-label="出牌倒计时"><span>{{ String(game.current_player_id) === viewerId ? '轮到你出牌' : `等待 ${currentName}` }}</span><strong v-if="secondsLeft !== null">{{ secondsLeft }}</strong></div>
        <div v-if="game" class="tg-hand-shelf">
          <div v-if="myHand.length" class="tg-my-hand" :style="{ '--hand-count': myHand.length }" aria-label="我的手牌">
            <button v-for="(item, index) in renderedHand" :key="item.id" class="tg-card tg-hand-card" :data-card-id="item.id" :data-card-dealing="dealingCards[item.id] !== undefined" :style="{ '--deal-delay': `${dealingCards[item.id] ?? 0}ms` }" :class="{ selected: selectedIndices.includes(index), 'tg-tile': isMahjong, 'tg-missing-tile': isSichuan && item.card[0] === myGame?.missing_suit }" :aria-label="`${cardLabel(item.card)}，第${index + 1}张`" :aria-pressed="selectedIndices.includes(index)" :disabled="busy || game.finished || !(currentGameType === 'landlord' || isMahjong) || myGame?.has_won || game.phase === 'dingque'" @click="toggleCard(index)"><img v-if="cardImage(item.card)" :src="cardImage(item.card)!" :alt="cardLabel(item.card)" @animationend="finishHandAnimation(item.id)"><span v-else>{{ cardLabel(item.card) }}</span></button>
          </div>
          <p v-else-if="currentGameType === 'golden_flower' && !game.finished" class="tg-muted">🂠 🂠 🂠 · 点击「看牌」查看你的三张牌</p>
        </div>

        <p class="tg-wallet-strip">余额 {{ profile.balance }} 灵石 · 底分 {{ room.base_stake }} · 单局最多输 {{ room.loss_limit }}<span v-if="room.settlement_status === 'settled'"> · 本局已结算</span><span v-else-if="room.settlement_status === 'reserved'"> · {{ isSichuan ? '整局结束统一结算' : '本局已冻结' }}</span></p>
        <div class="tg-control-panel">
        <template v-if="room.state === 'waiting' || room.state === 'finished'">
          <div class="tg-actions"><button class="game-button quiet" :disabled="busy" @click="openSettings">房间设置</button><button v-if="host" class="game-button quiet" :disabled="busy" @click="openBots">陪玩管理</button></div>
          <div class="tg-actions"><button class="game-button" :disabled="busy" @click="request('ready', { room_id: room.room_id, ready: !viewer?.is_ready })">{{ viewer?.is_ready ? '取消准备' : room.state === 'finished' ? '准备下一局' : '准备' }}</button><button v-if="host" class="game-button gold" :disabled="busy || !startReady" @click="request('start', { room_id: room.room_id })">开始本局</button></div>
        </template>
        <template v-if="game">
          <div v-if="isSichuan && legalActions.includes('dingque')" class="tg-actions tg-dingque-actions" aria-label="选择定缺"><span>选择定缺：</span><button v-for="suit in missingSuitOptions" :key="suit" class="game-button gold" :disabled="busy" @click="act('dingque', { suit })">缺{{ suitNames[suit] }}</button></div>
          <div v-if="legalActions.includes('bid')" class="tg-actions"><span>叫分：</span><button v-for="bid in game.bid_options ?? [0, 1, 2, 3]" :key="bid" class="game-button" :disabled="busy" @click="act('bid', { bid })">{{ bid ? `${bid} 分` : '不叫' }}</button></div>
          <div v-if="legalActions.includes('raise') || legalActions.includes('compare')" class="tg-actions tg-bet-fields">
            <div v-if="legalActions.includes('raise')" class="tg-field tg-raise-control"><label for="table-raise-amount">{{ currentGameType === 'texas' ? '本轮加到' : '基础注加到' }}</label><div class="tg-raise-entry"><input id="table-raise-amount" v-model.number="amount" type="number" :min="Math.min(game.min_raise_to ?? 1, game.max_raise_to ?? Number.MAX_SAFE_INTEGER)" :max="game.max_raise_to" step="1" inputmode="numeric" :disabled="busy"><template v-if="currentGameType === 'texas'"><button v-for="multiple in ([2, 4] as const)" :key="multiple" class="game-button quiet tg-quick-raise" :aria-label="`填入${multiple}倍当前桌注`" :title="`按当前桌注填入${multiple}倍，超出时按本轮可加金额限制`" :disabled="busy" @click="quickRaise(multiple)">{{ multiple }}×</button></template></div></div>
            <label v-if="legalActions.includes('compare')" class="tg-field">比牌对手<select v-model="targetId" :disabled="busy"><option v-for="player in otherPlayers" :key="player.user_id" :value="player.user_id">{{ player.username }}</option></select></label>
          </div>
          <p v-if="legalActions.includes('call') || legalActions.includes('compare')" class="tg-muted tg-bet-cost"><span v-if="legalActions.includes('call')">跟注 {{ game.call_amount ?? 0 }}</span><span v-if="legalActions.includes('compare')"> · 比牌 {{ game.compare_cost ?? 0 }}</span></p>
          <div v-if="legalActions.includes('chow') && game.chow_options?.length" class="tg-actions"><button v-for="(tiles, index) in game.chow_options" :key="index" class="game-button" :disabled="busy" @click="act('chow', { tiles })">吃 {{ tiles.map(cardLabel).join(' ') }}</button></div>
          <div v-if="legalActions.includes('kong') && game.kong_options?.length" class="tg-actions"><button v-for="tile in game.kong_options" :key="tile" class="game-button" :disabled="busy" @click="act('kong', { tile })">杠 {{ cardLabel(tile) }}</button></div>
          <div v-if="genericActions.length || selectedIndices.length" class="tg-actions"><button v-for="action in genericActions" :key="action" class="game-button" :class="{ gold: ['play', 'win', 'call'].includes(action) }" :disabled="actionDisabled(action)" @click="act(action)">{{ action === 'pass' ? currentGameType === 'landlord' ? '不出' : isMahjong ? '过' : labels[action] : labels[action] || action }}</button><button v-if="selectedIndices.length" class="game-button quiet" :disabled="busy" @click="selectedIndices = []">清空选择</button></div>
          <p v-if="isSichuan && myGame?.has_won && !game.finished" class="tg-muted tg-blood-progress">你已胡牌，等待血战结束 · 当前 {{ game.winners.length }}/3 人胡牌</p>
          <p v-else-if="!legalActions.length && !game.finished" class="tg-muted">等待 {{ currentName || '其他玩家' }} {{ game.phase === 'dingque' ? '定缺' : '操作' }}…</p>
          <p v-if="isSichuan && mustDiscardMissingSuit && legalActions.includes('discard')" class="tg-muted">请先打出缺{{ suitNames[myGame!.missing_suit!] }}的牌</p>
          <p v-if="isMahjong && legalActions.includes('chow')" class="tg-muted">吃牌请先选择手中的两张牌；弃牌或暗杠 / 补杠请先选对应牌。</p>
        </template>
        </div>
      </footer>
    </template>

    <p v-if="error" class="tg-error" role="alert">{{ error }}</p>

    <dialog ref="customDialog" class="tg-modal" aria-labelledby="custom-room-title">
      <div class="tg-modal-head"><GameIcon name="room" /><div><small>好友专属牌桌</small><h2 id="custom-room-title">自定义房间</h2></div><button class="game-button quiet" aria-label="关闭自定义房间" @click="customDialog?.close()">关闭</button></div>
      <form @submit.prevent="create('multi', 'custom')">
        <div class="tg-setting-fields"><label>底分<input v-model.number="customBase" type="number" min="1" :max="maxBaseStake" step="1" inputmode="numeric" :disabled="busy"></label><label>单局最多输<input v-model.number="customLimit" type="number" :min="Math.max(100, customBase * 10)" :max="Math.min(maxLossLimit, profile.balance)" step="1" inputmode="numeric" :disabled="busy"></label></div>
        <p class="tg-modal-hint">准入灵石 {{ customLimit }} · 单局上限至少 100 灵石，且不低于底分的 10 倍。</p>
        <p class="tg-modal-hint">开局冻结单局上限，结算按实际输赢返还；不会自动追加。</p>
        <label class="tg-check"><input v-model="includeYueyue" type="checkbox" :disabled="busy">邀请月月一起玩</label>
        <p v-if="error" class="tg-modal-error" role="alert">{{ error }}</p>
        <div class="tg-modal-footer"><span>可用 {{ profile.balance }} 灵石</span><button class="game-button gold" :disabled="busy || !customValid">创建自定义房间</button></div>
      </form>
    </dialog>

    <dialog ref="joinDialog" class="tg-modal tg-join-modal" aria-labelledby="join-room-title">
      <div class="tg-modal-head"><GameIcon name="friends" /><div><small>朋友已经就位</small><h2 id="join-room-title">加入房间</h2></div><button class="game-button quiet" aria-label="关闭加入房间" @click="joinDialog?.close()">关闭</button></div>
      <form @submit.prevent="join"><label class="tg-room-code-label" for="table-room-id">桌游房间号<input id="table-room-id" v-model="roomInput" placeholder="输入房间号" maxlength="16" autocomplete="off" autocapitalize="characters" :disabled="busy"></label><p class="tg-modal-hint">输入好友分享的房间号，加入后准备即可开局。</p><p v-if="error" class="tg-modal-error" role="alert">{{ error }}</p><div class="tg-modal-footer"><span>可用 {{ profile.balance }} 灵石</span><button class="game-button gold" :disabled="busy || !roomInput.trim()">确认加入</button></div></form>
    </dialog>

    <dialog ref="settingsDialog" class="tg-modal" aria-labelledby="room-settings-title">
      <div class="tg-modal-head"><GameIcon name="room" /><div><small>{{ room?.room_id }} · {{ room ? tierNames[room.room_tier] : '' }}</small><h2 id="room-settings-title">房间设置</h2></div><button class="game-button quiet" aria-label="关闭房间设置" @click="settingsDialog?.close()">关闭</button></div>
      <template v-if="room">
        <form v-if="canManage && room.room_tier === 'custom'" @submit.prevent="saveSettings"><div class="tg-setting-fields"><label>底分<input v-model.number="settingsBase" type="number" min="1" :max="maxBaseStake" step="1" inputmode="numeric" :disabled="busy"></label><label>单局最多输<input v-model.number="settingsLimit" type="number" :min="Math.max(100, settingsBase * 10)" :max="Math.min(maxLossLimit, profile.balance)" step="1" inputmode="numeric" :disabled="busy"></label></div><p class="tg-modal-hint">准入灵石 {{ settingsLimit }} · 保存后所有真人需重新准备。</p><p class="tg-modal-hint">每位真人余额须满足新准入，最低单局上限为 100 灵石和 10 倍底分中的较大值。</p><p v-if="error" class="tg-modal-error" role="alert">{{ error }}</p><div class="tg-modal-footer"><span>可用 {{ profile.balance }} 灵石</span><button class="game-button gold" :disabled="busy || !settingsValid || !settingsChanged">保存设置</button></div></form>
        <template v-else><dl class="tg-room-terms"><div><dt>底分</dt><dd>{{ room.base_stake }} 灵石</dd></div><div><dt>准入灵石</dt><dd>{{ room.entry_min }}</dd></div><div><dt>单局最多输</dt><dd>{{ room.loss_limit }} 灵石</dd></div></dl><p class="tg-modal-hint">{{ room.room_tier !== 'custom' ? '此场次参数固定；如需更大底分，可返回大厅创建自定义房间。' : '底分和单局上限由房主在本局结束后调整。' }}</p></template>
      </template>
    </dialog>

    <dialog ref="botsDialog" class="tg-modal" aria-labelledby="bots-settings-title">
      <div class="tg-modal-head"><GameIcon name="robot" /><div><small>{{ room?.players.length ?? 0 }} / {{ room?.max_players ?? 0 }} 人已入座</small><h2 id="bots-settings-title">陪玩管理</h2></div><button class="game-button quiet" aria-label="关闭陪玩管理" @click="botsDialog?.close()">关闭</button></div>
      <form class="tg-bot-add" @submit.prevent="addBots"><label>添加人数<select v-model.number="botCount" aria-label="添加人数" :disabled="busy || !canManage || !emptySeats"><option v-for="count in emptySeats" :key="count" :value="count">{{ count }} 位</option><option v-if="!emptySeats" :value="1">没有空位</option></select></label><button class="game-button gold" :disabled="busy || !canManage || !validBotCount">添加陪玩</button></form>
      <p class="tg-modal-hint">仅添加选择的人数；可逐个移除，单人房间也不会自动补回。</p>
      <ul v-if="bots.length" class="tg-bot-list"><li v-for="bot in bots" :key="bot.user_id"><img :src="bot.avatar_url || '/character/normal.webp'" alt=""><div><strong>{{ bot.username }}</strong><small>AI 陪玩 · 已准备</small></div><button class="game-button quiet" :aria-label="`移除 ${bot.username}`" :disabled="busy || !canManage" @click="removeBot(bot.user_id)">移除</button></li></ul><p v-else class="tg-bots-empty">目前没有陪玩，选择人数即可邀请入座。</p>
      <p v-if="error" class="tg-modal-error" role="alert">{{ error }}</p>
    </dialog>

    <dialog ref="rulesDialog" class="tg-rules" aria-labelledby="table-rules-title">
      <div class="tg-rules-head"><h2 id="table-rules-title">{{ rules.title }}规则</h2><button class="game-button quiet" aria-label="关闭规则" @click="rulesDialog?.close()">关闭</button></div>
      <p>{{ rules.players }}</p>
      <section v-for="section in rules.sections" :key="section.title"><h3>{{ section.title }}</h3><p>{{ section.text }}</p></section>
      <p v-if="isSichuan">玩法参考：<a href="https://majiang.qq.com/webplat/info/news_version3/7207/25932/25933/25936/m16340/201611/523252.shtml" target="_blank" rel="noopener noreferrer">腾讯欢乐麻将川麻赛事规则</a>；本房间实际范围和计分以上述规则为准。</p>
      <p v-if="isMahjong">麻将牌图：Cangjie6 等作者，来自 <a href="https://github.com/perthmahjongsoc/mahjong-tiles-svg" target="_blank" rel="noopener noreferrer">Perth Mahjong Society</a>，采用 <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener noreferrer">CC BY-SA 4.0</a> 许可。</p>
    </dialog>
  </section>
</template>

<style scoped>
/* 使用独立的横屏安全区：背景延展，头像、手牌与操作始终留在视窗内。 */
.table-games { position: relative; flex: 1; min-width: 0; min-height: 0; width: 100%; color: #fff2cc; background: #634665; container-type: size; text-align: center; --gold: #f4d28b; --card-height: clamp(68px, 24cqh, 172px); }
.table-games, .table-games * { box-sizing: border-box; }
.tg-toolbar { position: absolute; inset: 0 0 auto; height: 48px; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 5px max(14px, env(safe-area-inset-right)) 5px max(14px, env(safe-area-inset-left)); background: linear-gradient(90deg, #743e3de8, #5f4561c9 52%, #73433be8); border-bottom: 1px solid #eac991; box-shadow: inset 0 -3px #542d3433, 0 2px 8px #4f273d55; }
.tg-title { min-width: 0; text-align: left; }
.tg-title h2 { color: #ffe5a3; margin: 0; font-size: 16px; font-weight: 800; letter-spacing: 1px; text-shadow: 0 2px #59374d, 1px 0 #59374d; }
.tg-title p { color: #f3d5b7; margin: 2px 0 0; font-size: 10px; white-space: nowrap; text-overflow: ellipsis; overflow: hidden; }
.tg-actions { display: flex; justify-content: center; align-items: center; gap: 7px; min-width: 0; flex-wrap: wrap; }
.tg-toolbar .tg-actions { flex-shrink: 0; flex-wrap: nowrap; }
.table-games button:not(.game-button, .tg-tier-card, .tg-mode-card, .tg-hand-card), .table-games input, .table-games select { min-width: 0; max-width: 100%; min-height: 38px; margin: 0; padding: 8px 16px; border: 1px solid #d6ab66; border-radius: 8px; color: #34355b; background: linear-gradient(#fff9e8, #efdab1); font: inherit; font-size: 13px; line-height: 1.2; letter-spacing: 0; text-transform: none; box-shadow: inset 0 2px 4px #9b64352b; touch-action: manipulation; }
.table-games .game-button { margin: 0; max-width: 100%; letter-spacing: 0; text-transform: none; }
.table-games button { white-space: nowrap; }
.table-games button:hover { filter: brightness(1.15); }
.table-games button:disabled { opacity: .45; cursor: not-allowed; }
.table-games :focus-visible { outline: 2px solid #fbe2a7; outline-offset: 2px; }
.tg-toolbar button { min-height: 36px; font-size: 11px; padding: 6px 12px; }
.tg-tier-tag { display: inline-block; margin-left: 8px; color: #ffdfa2; font-size: 10px; letter-spacing: 0; }
.tg-lobby { position: absolute; inset: 48px 0 0; overflow: auto; padding: clamp(12px, 3cqh, 28px) clamp(16px, 4cqw, 54px); background: linear-gradient(#78374918, #2c2b5bad), url('/ui/guochao/teahouse-room.webp') center/cover, #a76b63; }
.tg-lobby-inner { display: flex; flex-direction: column; gap: clamp(9px, 2cqh, 18px); width: min(100%, 1040px); min-height: 100%; margin-inline: auto; justify-content: center; }
.tg-lobby-heading { display: flex; justify-content: space-between; align-items: center; gap: 14px; text-align: left; }
.tg-eyebrow { color: #ffe3aa; font-size: 11px; letter-spacing: 3px; text-shadow: 0 1px 3px #493252; }
.tg-lobby-heading h3 { margin: 6px 0 4px; color: #ffe5a1; font-size: clamp(19px, 4cqh, 32px); letter-spacing: 3px; font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-weight: 900; text-shadow: 0 2px #453961, 1px 0 #453961, -1px 0 #453961, 0 4px 9px #5d294866; }
.tg-lobby-heading p { margin: 0; color: #fff2dc; font-size: 11px; text-shadow: 0 1px 3px #4b3657; }
.tg-lobby-balance { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; color: #ffedd1; font-size: 11px; text-shadow: 0 2px 3px #463253; }
.tg-lobby-balance strong { color: #ffdc81; font-size: 24px; font-variant-numeric: tabular-nums; }
.tg-variant-tabs { display: flex; gap: 8px; margin: 5px 0; }
.tg-variant-tabs .game-button { min-height: 36px; padding: 6px 14px; font-size: 13px; }
.tg-tier-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(10px, 2cqw, 22px); }
.table-games .tg-tier-card { --tier-accent: #ffda88; position: relative; height: clamp(150px, 34cqh, 278px); padding: 20px 16px 14px; border: 1px solid #ffdb94; border-radius: 20px 20px 12px 12px; overflow: hidden; text-align: left; white-space: normal; background: radial-gradient(ellipse at 82% 24%, #ffd08c99, transparent 58%), url('/ui/guochao/cloud-pattern.svg') center/150px, linear-gradient(155deg, #d57c68, #914855); box-shadow: inset 0 0 0 4px #733b4366, inset 0 0 0 6px #ffd49c77, 0 6px 0 #623a53, 0 12px 20px #30204355; }
.table-games .tg-tier-intermediate { --tier-accent: #ffdfa1; background: radial-gradient(ellipse at 82% 24%, #d4e5e699, transparent 60%), url('/ui/guochao/cloud-pattern.svg') center/150px, linear-gradient(155deg, #6a9ec0, #4c568a); }
.table-games .tg-tier-advanced { --tier-accent: #ffe096; background: radial-gradient(ellipse at 82% 24%, #f7c68999, transparent 60%), url('/ui/guochao/cloud-pattern.svg') center/150px, linear-gradient(155deg, #af7390, #71476e); }
.table-games .tg-tier-card.selected { outline: 2px solid #fff1b3; outline-offset: 2px; box-shadow: inset 0 0 0 4px #8d544266, inset 0 0 0 6px #ffe1a6aa, 0 6px 0 #623a53, 0 0 22px #ffca7160; }
.tg-scroll-frame { position: absolute; inset: 0; width: 100%; height: 100%; color: #ffe2a3; opacity: .82; pointer-events: none; }
.tg-tier-art { position: absolute; right: -3%; top: 2%; width: 76%; height: 73%; filter: drop-shadow(0 8px 7px #001b2f44); pointer-events: none; }
.tg-tier-heading { position: absolute; left: 16px; top: 18px; display: flex; flex-direction: column; gap: 6px; z-index: 1; text-shadow: 0 2px #453655, 1px 0 #453655, -1px 0 #453655; }
.tg-tier-heading > span { color: var(--tier-accent); font-size: 11px; letter-spacing: 2px; }
.tg-tier-heading strong { color: #fff0b4; font-size: clamp(22px, 4.6cqh, 36px); font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-weight: 900; letter-spacing: 2px; }
.tg-tier-terms { position: absolute; left: 14px; right: 14px; bottom: 37px; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px; font-size: 10px; color: #fff1d5; text-shadow: 0 1px 2px #60394f; }
.tg-tier-terms b { color: #ffe699; font-size: 13px; font-weight: 700; }
.tg-tier-selected { position: absolute; bottom: 0; left: 0; right: 0; display: grid; place-items: center; height: 27px; border-top: 1px solid #f1c17b; background: linear-gradient(#a26349, #764347); font-size: 10px; color: #ffe4ab; letter-spacing: 2px; }
.tg-entry-row { display: grid; grid-template-columns: 1fr 1fr; gap: clamp(10px, 2cqw, 22px); }
.table-games .tg-mode-card { display: flex; align-items: center; gap: 12px; min-height: 96px; width: 100%; padding: 6px 16px 6px 6px; border: 1px solid #f3cf8a; border-radius: 48px 14px 14px 48px; background: linear-gradient(115deg, #dfa967, #b86956 65%, #915160); box-shadow: inset 0 2px #ffe4ac, inset 0 -3px #7c454a80, 0 3px 0 #58364d; text-align: left; }
.tg-mode-card > .game-icon { width: 105px; height: 80px; flex-shrink: 0; }
.tg-mode-card > span:not(.tg-entry-arrow) { display: flex; flex-direction: column; gap: 7px; }
.tg-mode-card strong { font-size: 19px; color: #fff0b8; text-shadow: 0 2px #62415b; }
.tg-mode-card small { font-size: 11px; color: #fff0d4; text-shadow: 0 1px #734559; }
.tg-entry-arrow { margin-left: auto; color: #ffe8a7; font-size: 35px; font-weight: 300; }
.tg-friends-entry { position: relative; }
.table-games .tg-friends-entry .tg-mode-card { background: linear-gradient(115deg, #78acc5, #507ba2 65%, #535782); border-color: #f3cf8a; }
.tg-friends-entry .tg-check { position: absolute; right: 14px; bottom: 9px; font-size: 9px; gap: 5px; color: #fff0d2; }
.tg-lobby-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.tg-lobby-footer > p { margin: 0; font-size: 11px; color: #fff0d0; text-shadow: 0 1px 3px #453151; }
.tg-lobby-footer > p.tg-entry-insufficient { color: #ffe5a8; }
.tg-lobby-footer .game-button > .game-icon { width: 25px; height: 23px; }
.tg-muted { font-size: 11px; line-height: 1.4; color: #f8e5c7; margin: 0; text-shadow: 0 1px 3px #353254; }
.tg-check { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.table-games .tg-check input { min-height: 16px; height: 16px; width: 16px; padding: 0; margin: 0; accent-color: #d6b771; }
.tg-scroll { position: absolute; inset: 48px 0 26px; min-height: 0; overflow: hidden; background: linear-gradient(#c9856326, #49396070), url('/ui/guochao/teahouse-room.webp') center/cover, #a76b63; }
.tg-table-status { position: absolute; top: 41%; left: 23%; right: 23%; z-index: 5; display: flex; justify-content: center; align-items: center; gap: 10px; pointer-events: none; }
.tg-notice { margin: 0; max-width: 75%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; color: #fff1d4; text-shadow: 0 1px 3px #393057; }
.tg-turn { color: #ffe2a0; font-size: 11px; white-space: nowrap; }
.tg-turn b { display: inline-grid; place-items: center; min-width: 25px; height: 25px; border-radius: 50%; border: 1px solid #f4d58d; background: linear-gradient(#7475b1, #434b86); box-shadow: inset 0 1px #ded2f199, 0 2px 2px #42335680; margin-left: 5px; }
.tg-arena { position: absolute; inset: 0; isolation: isolate; }
.tg-deal-layer { position: absolute; inset: 0; z-index: 8; pointer-events: none; overflow: hidden; }
.tg-deal-card { position: absolute; display: block; width: clamp(18px, 5cqh, 36px); aspect-ratio: 5 / 7; margin-left: -12px; border: 1px solid #f6d798; border-radius: 3px; background: #586096; box-shadow: 0 3px 6px #3b285760; opacity: 0; animation: tg-deal-flight 420ms cubic-bezier(.15,.6,.3,1) both; }
.tg-deal-card img { width: 100%; height: 100%; border-radius: 2px; object-fit: fill; }
.tg-deal-card.tg-deal-tile { aspect-ratio: .65; border: 2px solid #fff0cf; border-bottom: 5px solid #d4c39f; background: linear-gradient(100deg, #455982, #8297c7 45%, #4d5a89); }
.tg-hand-card[data-card-dealing="true"] img, .tg-board-card[data-card-dealing="true"] img { animation: tg-hand-deal 420ms var(--deal-delay, 0ms) cubic-bezier(.15,.7,.3,1) both; }
.tg-round-result { position: absolute; top: 43%; left: 50%; width: min(360px, 43%); transform: translate(-50%, -50%); z-index: 7; pointer-events: none; }
.tg-round-result :deep(.round-feedback) { --result-title-size: clamp(20px, 3.3cqh, 30px); }
.tg-round-result :deep(.round-feedback-subtitle) { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tg-win-notice { position: absolute; z-index: 7; top: 0; left: 50%; width: min(340px, 33%); transform: translateX(-50%); pointer-events: none; }
.tg-win-notice :deep(.round-feedback) { --result-title-size: clamp(13px, 2.1cqh, 20px); --result-padding: 8px 17px; }
.tg-win-notice :deep(.result-emblem) { display: none; }
.tg-win-notice :deep(.result-copy) { gap: 2px; }
.tg-win-notice :deep(.round-feedback-subtitle), .tg-win-notice :deep(.round-feedback-detail) { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; }
.tg-finished .tg-pot, .tg-finished .tg-table-caption { display: none; }
@keyframes tg-deal-flight {
  0% { opacity: 0; transform: translate(var(--deal-from-x), var(--deal-from-y)) scale(.65) rotate(-16deg); }
  18% { opacity: 1; }
  82% { opacity: 1; }
  100% { opacity: 0; transform: translate(0, 0) scale(1) rotate(0); }
}
@keyframes tg-hand-deal {
  0% { opacity: 0; transform: translateY(-25px) scale(.87) rotate(-5deg); }
  65% { opacity: 1; }
  100% { opacity: 1; transform: translateY(0) scale(1) rotate(0); }
}
.tg-felt { position: absolute; inset: 7% 0 0; background: url('/ui/guochao/table-felt.svg') center/100% 100% no-repeat; filter: drop-shadow(0 8px 5px #49324955); }
.tg-center { position: absolute; top: 51%; left: 50%; width: 48%; transform: translate(-50%, -50%); display: flex; flex-direction: column; align-items: center; gap: clamp(3px, 1.2cqh, 10px); text-align: center; z-index: 1; }
/* 提示、底池与公共牌使用同一纵向流，避免独立百分比定位叠字。 */
.tg-center > .tg-poker-status { position: static; width: 100%; min-width: 0; gap: 8px; }
.tg-poker-status .tg-notice { min-width: 0; max-width: none; flex: 1 1 auto; text-align: right; }
.tg-poker-status .tg-turn { display: inline-flex; align-items: center; min-width: 0; max-width: 65%; }
.tg-poker-status .tg-turn > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tg-poker-status .tg-turn b { flex-shrink: 0; }
.tg-compact-notice { display: none; }
/* 扑克桌心采用两行信息区，短窗口也给公共牌和底部操作各留独立空间。 */
.game-texas:not(.tg-finished):not(.tg-waiting) .tg-center { top: 44%; display: grid; grid-template-columns: auto auto; justify-content: center; column-gap: 12px; row-gap: 5px; }
.game-texas:not(.tg-finished):not(.tg-waiting) .tg-poker-status { grid-column: 1 / -1; }
.game-texas:not(.tg-finished):not(.tg-waiting) .tg-pot { grid-column: 1; grid-row: 2; flex-direction: column; gap: 2px; }
.game-texas:not(.tg-finished):not(.tg-waiting) .tg-center > .tg-public-cards { grid-column: 2; grid-row: 2 / 4; }
.game-texas:not(.tg-finished):not(.tg-waiting) .tg-table-caption { grid-column: 1; grid-row: 3; font-size: 9px; white-space: nowrap; }
.game-golden_flower:not(.tg-finished):not(.tg-waiting) .tg-center { top: 44%; }
.tg-table-welcome { display: flex; flex-direction: column; gap: 8px; color: #ffe5a5; text-shadow: 0 2px #3f355c, 1px 0 #3f355c, -1px 0 #3f355c, 0 4px 5px #4a3a7266; }
.tg-table-welcome > span { letter-spacing: 12px; font-size: 20px; opacity: .6; }
.tg-table-welcome strong { font-size: clamp(19px, 4cqh, 32px); font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-weight: 900; letter-spacing: 6px; }
.tg-table-welcome p, .tg-table-welcome small { margin: 0; font-size: 11px; color: #fff0ce; }
.tg-pot { display: flex; align-items: center; gap: 8px; color: #ffe1a2; }
.tg-pot small { display: inline; font-size: 10px; color: #f9e6cf; margin-right: 6px; }
.tg-pot strong { font-size: clamp(16px, 3.7cqh, 25px); font-variant-numeric: tabular-nums; }
.tg-pot strong span { font-size: 10px; font-weight: 400; }
.tg-chip-stack { width: 28px; height: 29px; background: url('/table-assets/chip-red.png') center/contain no-repeat; }
.tg-public-cards { display: flex; align-items: center; justify-content: center; gap: 5px; max-width: 100%; }
.tg-board-card { width: clamp(31px, 10cqh, 65px); aspect-ratio: 5 / 7; flex-shrink: 0; display: flex; align-items: center; justify-content: center; background: #fff8e9; border-radius: 5px; color: #222; box-shadow: 0 3px 7px #352b5966; overflow: hidden; }
.tg-board-card img { width: 100%; height: 100%; object-fit: contain; }
.tg-board-card.tg-card-slot { border: 1px solid #e4cda36b; background: #45427470; box-shadow: inset 0 1px 5px #35265666; color: #e7d4b164; font-size: 27px; }
.tg-table-caption { font-size: 10px; color: #f3e2d4; margin: 0; text-shadow: 0 1px 2px #493457; }
.tg-bottom-cards, .tg-last-play { display: flex; flex-direction: column; gap: 5px; align-items: center; max-width: 100%; }
.tg-bottom-cards > small, .tg-last-play > small { color: #ffe8b5; font-size: 10px; text-shadow: 0 1px 2px #3d345c; }
.tg-bottom-cards .tg-board-card { width: clamp(22px, 7cqh, 45px); }
.tg-last-play { width: 100%; container-type: inline-size; }
.tg-played-cards { --public-width: min(56px, calc((100cqw - 12px) / (1 + (var(--public-count) - 1) * .38))); display: flex; justify-content: center; width: 100%; }
.tg-played-cards .tg-board-card { width: var(--public-width); margin-left: 0; }
.tg-played-cards .tg-board-card + .tg-board-card { margin-left: calc(var(--public-width) * -.62); }
.tg-seats { position: absolute; inset: 0; z-index: 3; pointer-events: none; }
.tg-seat, .tg-empty-seat { position: absolute; left: var(--seat-x); top: var(--seat-y); transform: translate(-50%, -50%); width: clamp(80px, 12cqw, 168px); text-align: center; pointer-events: auto; }
.tg-player { position: relative; display: grid; grid-template-columns: auto minmax(0, 1fr); grid-template-rows: auto auto; align-items: center; gap: 2px 7px; padding: 6px 8px 6px 6px; border: 1px solid #ebc986; border-radius: 9px; background: linear-gradient(#6588b9f5, #435484f5); box-shadow: inset 0 1px #d9ddf780, inset 0 -3px #343d6559, 0 3px 0 #735d68, 0 5px 10px #49335466; text-align: left; }
.tg-avatar { position: relative; grid-row: 1 / 3; width: clamp(28px, 7.5cqh, 56px); height: clamp(28px, 7.5cqh, 56px); border: 2px solid #f2cc83; border-radius: 7px; background: #d8bca2; box-shadow: 0 1px 4px #42335988; }
.tg-avatar img { display: block; width: 100%; height: 100%; border-radius: 3px; object-fit: cover; }
.tg-host-badge { position: absolute; right: -5px; bottom: -3px; width: 14px; height: 14px; border-radius: 50%; background: #ffe1a0; color: #995044; font-size: 9px; text-align: center; box-shadow: 0 1px 2px #5e3d5c88; }
.tg-nameplate { display: flex; align-items: center; gap: 3px; min-width: 0; }
.tg-nameplate h3 { margin: 0; min-width: 0; font-size: clamp(10px, 1.8cqh, 13px); font-weight: 500; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }
.tg-seat-tag, .tg-role { color: #f9dbb1; font-size: 9px; flex-shrink: 0; }
.tg-seat-state { margin: 0; font-size: 10px; color: #f4e3d0; }
.tg-seat-state.tg-ready { color: #ffe394; }
.tg-seat > .tg-seat-state { display: inline-block; background: #535586e8; border: 1px solid #e7c59480; border-radius: 5px; padding: 2px 6px; margin-top: 3px; }
.tg-seat-chips { display: flex; gap: 3px; align-items: center; margin: 0; color: #f5da7a; font-size: clamp(11px, 2.2cqh, 17px); font-variant-numeric: tabular-nums; }
.tg-seat-chips small { font-size: 8px; color: #f0dfca; white-space: nowrap; }
.tg-seat-chips i { width: 13px; height: 13px; background: url('/table-assets/chip-red.png') center/contain no-repeat; }
.tg-current .tg-player { border-color: #fff1aa; box-shadow: inset 0 1px #fff5c488, 0 0 0 2px #f6d68180, 0 0 18px #ffe0976b; }
.tg-self .tg-player { border-color: #f4d492; }
.tg-has-won .tg-player { border-color: #ffe1a0; background: linear-gradient(#ad784df5, #865472f5); }
.game-sichuan_mahjong .tg-seat { width: clamp(68px, 12cqw, 168px); }
.game-sichuan_mahjong .tg-avatar { grid-row: 1; }
.tg-sichuan-pending { grid-column: 1 / -1; margin: 0; color: #ffe5a9; font-size: clamp(8px, 1.5cqh, 11px); white-space: nowrap; text-align: center; }
.tg-sichuan-seat-status { display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 2px 4px; margin: 3px 0 0; color: #ffeac2; font-size: 9px; }
.tg-sichuan-seat-status > * { padding: 1px 4px; border: 1px solid #edcb8799; border-radius: 4px; background: #5c558adc; white-space: nowrap; }
.tg-sichuan-seat-status strong { color: #ffe392; background: #9a534bdc; }
.tg-folded .tg-player, .tg-folded .tg-hidden-hand { opacity: .48; }
.tg-hand-name { margin: 3px 0 0; font-size: 10px; color: #f4d87f; }
.tg-settled { margin: 3px 0 0; padding: 3px; border: 1px solid #dcc19580; border-radius: 5px; color: #ffe1d4; background: #6a4d74ed; font-size: 10px; }
.tg-settled.tg-positive { color: #ffe69b; background: #485581ed; }
.tg-hidden-hand, .tg-opponent-hand { display: flex; align-items: center; justify-content: center; margin-top: 7px; padding-left: 12px; }
.tg-hidden-hand { width: fit-content; max-width: 100%; margin-inline: auto; }
.tg-hidden-hand i { display: block; flex: 0 0 auto; width: clamp(22px, 6.8cqh, 44px); height: clamp(30px, 9.4cqh, 62px); border: 1px solid #e1e3d5; border-radius: 3px; margin-left: -9px; background: url('/table-assets/card-back.png') center/100% 100% no-repeat; box-shadow: 0 2px 4px #0006; }
.tg-hidden-hand span { font-size: 9px; color: #fff0c8; margin-left: 5px; text-shadow: 0 1px #443358; }
.tg-hidden-hand i.tg-tile-back { width: 13px; height: 23px; margin-left: -1px; background: linear-gradient(90deg, #4b5d8d, #829ac4 75%, #435880); border: 1px solid #eddbb9; border-bottom-width: 4px; }
.tg-opponent-hand { container-type: inline-size; width: 100%; padding-left: 0; }
.tg-opponent-hand img { --public-width: min(45px, calc((100cqw - 8px) / (1 + (var(--public-count) - 1) * .38))); width: var(--public-width); height: calc(var(--public-width) * 1.4); margin-left: 0; border-radius: 3px; object-fit: contain; }
.tg-opponent-hand img + img { margin-left: calc(var(--public-width) * -.62); }
.tg-empty-seat { color: #ffe4b8c9; font-size: 10px; display: grid; gap: 4px; justify-items: center; }
.tg-empty-seat span { display: grid; place-items: center; width: 42px; height: 42px; border: 1px dashed #edcd96a6; border-radius: 50%; font-size: 24px; background: #62558299; }
.tg-table-footnote { display: none; }
.tg-dock { position: absolute; inset: 48px 0 0; z-index: 6; pointer-events: none; }
.tg-hand-shelf { position: absolute; left: 20%; right: 18%; bottom: 32px; pointer-events: auto; container-type: inline-size; }
.tg-my-hand { --face-width: min(calc(var(--card-height) * .714), calc((100cqw - 14px) / (1 + (var(--hand-count) - 1) * .38))); display: flex; align-items: flex-end; justify-content: center; padding: 12px 4px 5px; overflow: visible; min-width: 0; }
.tg-card, .table-games button.tg-card { position: relative; display: flex; justify-content: center; align-items: center; flex: 0 0 calc(var(--face-width) * .38); width: calc(var(--face-width) * .38); height: calc(var(--face-width) * 1.4); min-height: 0; padding: 0; border: 0; border-radius: 0; background: transparent; color: #222; box-shadow: none; overflow: visible; }
.tg-card img { position: absolute; left: 0; bottom: 0; width: var(--face-width); max-width: none; height: 100%; object-fit: contain; border-radius: 4px; box-shadow: 0 3px 7px #001c2966; pointer-events: none; }
.table-games .tg-hand-card:last-child { flex-basis: var(--face-width); }
.table-games .tg-hand-card + .tg-hand-card { margin-left: 0; }
.table-games .tg-hand-card:disabled { opacity: 1; }
.table-games .tg-hand-card.selected { transform: translateY(-9px); outline: none; }
.table-games .tg-hand-card.selected img { box-shadow: 0 0 0 2px #ffe281, 0 3px 7px #001c2966; }
.table-games .tg-hand-card.tg-tile { flex: 0 0 var(--face-width); width: var(--face-width); margin-left: 2px; border-top: 3px solid #657bab; border-bottom: 4px solid #cbbc98; border-radius: 4px; background: #fff5dc; }
.table-games .tg-hand-card.tg-tile img { position: static; width: 100%; max-width: 100%; box-shadow: none; }
.table-games .tg-hand-card.tg-missing-tile { border-top-color: #d95e45; border-bottom-color: #edb44e; }
.tg-control-panel { position: absolute; left: 18%; right: 5%; bottom: calc(26cqh + 28px); display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 7px 10px; pointer-events: auto; }
.tg-waiting .tg-control-panel { left: 18%; right: 18%; bottom: 26%; justify-content: center; }
.tg-waiting .tg-control-panel > .tg-actions:first-child { flex-basis: 100%; }
.tg-control-panel > .tg-muted { font-size: 10px; }
.tg-wallet-strip { position: absolute; bottom: 0; left: 0; right: 0; height: 26px; margin: 0; padding: 5px 18px; background: linear-gradient(90deg, #874f49ed, #5e466be8, #875748ed); border-top: 1px solid #eac68b99; text-align: right; color: #ffebc8; font-size: 11px; }
.tg-field { display: flex; align-items: center; gap: 6px; font-size: 11px; max-width: 100%; }
.tg-field input { width: 84px; }
.tg-field select { max-width: 122px; }
.tg-raise-entry { display: flex; align-items: center; gap: 4px; }
.table-games .tg-quick-raise { min-width: 36px; padding-inline: 7px; }
.tg-bet-fields { pointer-events: none; }
.tg-bet-fields .tg-field { pointer-events: auto; }
.tg-error { position: absolute; bottom: 26px; left: 20%; right: 20%; z-index: 12; margin: 0; padding: 8px 12px; background: #6e2828ee; border: 1px solid #e7b0a3; border-radius: 6px; max-height: 20cqh; overflow: auto; color: #ffe8de; font-size: 12px; }
.tg-modal { width: min(560px, calc(100vw - 28px)); max-height: calc(100dvh - 24px); margin: auto; padding: 20px; border: 2px solid #d1a469; border-radius: 18px; background: radial-gradient(ellipse at top left, #fff9e7, transparent 72%), #f1dcba; box-shadow: 0 20px 80px #36263f99, inset 0 0 0 3px #fff0cd, inset 0 0 0 5px #dcb57c99; color: #49405e; text-align: left; overflow: auto; }
.tg-modal::backdrop { background: #3a284d9e; backdrop-filter: blur(4px); }
.tg-modal-head { display: flex; align-items: center; gap: 10px; margin: -5px 0 16px; position: sticky; top: -20px; z-index: 2; background: #f7e8cb; border-bottom: 1px solid #d4ad77; padding-block: 5px 10px; }
.tg-modal-head > .game-icon { width: 72px; height: 55px; flex-shrink: 0; }
.tg-modal-head h2 { margin: 3px 0 0; color: #595181; font-size: 22px; letter-spacing: 2px; font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-weight: 900; text-shadow: 0 1px #fff7e5; }
.tg-modal-head small { color: #97664b; font-size: 10px; }
.table-games .tg-modal-head > button { margin-left: auto; }
.tg-setting-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.tg-setting-fields label, .tg-room-code-label { display: flex; flex-direction: column; gap: 7px; color: #514563; font-size: 12px; }
.tg-setting-fields input, .tg-room-code-label input { width: 100%; }
.tg-modal-hint { color: #786349; font-size: 11px; line-height: 1.7; margin: 12px 0; }
.tg-modal-footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 18px; border-top: 1px solid #d8b784; padding-top: 14px; }
.tg-modal-footer > span { color: #806144; font-size: 11px; }
.tg-modal-error { margin: 12px 0; padding: 8px; border: 1px solid #c97d6399; border-radius: 6px; background: #ffe3cc; color: #9b443f; font-size: 12px; }
.tg-room-terms { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 18px 0; }
.tg-room-terms > div { border: 1px solid #d4ae77; border-radius: 8px; padding: 14px 8px; text-align: center; background: linear-gradient(#fff4dc, #ecd1ab); box-shadow: inset 0 1px #fff9e9; }
.tg-room-terms dt { color: #806447; font-size: 11px; }
.tg-room-terms dd { color: #655183; font-size: 17px; font-weight: 700; margin: 10px 0 0; }
.tg-bot-add { display: flex; align-items: flex-end; justify-content: space-between; gap: 15px; }
.tg-bot-add label { display: flex; align-items: center; gap: 10px; color: #514563; font-size: 12px; }
.tg-bot-add select { min-width: 110px; }
.tg-bot-list { display: flex; flex-direction: column; gap: 8px; margin: 12px 0 0; padding: 0; list-style: none; }
.tg-bot-list li { display: flex; align-items: center; gap: 10px; border: 1px solid #d3b284; border-radius: 8px; padding: 8px 10px; background: #ffefd0; box-shadow: inset 0 1px #fff9eb; }
.tg-bot-list img { width: 38px; height: 38px; border: 2px solid #e7bd76; border-radius: 8px; object-fit: cover; }
.tg-bot-list li > div { display: flex; flex-direction: column; gap: 4px; }
.tg-bot-list strong { color: #514e7b; font-size: 13px; }
.tg-bot-list small { color: #8b6a4c; font-size: 10px; }
.table-games .tg-bot-list button { margin-left: auto; }
.tg-bots-empty { font-size: 12px; color: #816847; padding: 20px 0; text-align: center; }
.tg-rules { width: min(680px, calc(100vw - 24px)); max-height: calc(100dvh - 24px); margin: auto; border: 2px solid #d1a469; border-radius: 16px; padding: 18px; background: #f6e5c5; box-shadow: 0 15px 65px #392a4c88, inset 0 0 0 3px #fff3d7, inset 0 0 0 5px #dcb57c88; color: #4d4261; text-align: left; overflow: auto; }
.tg-rules::backdrop { background: #3a284da3; }
.tg-rules-head { display: flex; justify-content: space-between; align-items: center; position: sticky; top: -18px; background: #f6e5c5; border-bottom: 1px solid #d4ae77; padding: 6px 0; }
.tg-rules h2 { margin: 0; font-size: 20px; color: #595181; font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-weight: 900; }
.tg-rules h3 { font-size: 15px; color: #9d5b49; margin: 16px 0 6px; }
.tg-rules p { font-size: 13px; line-height: 1.65; margin: 6px 0; }
.tg-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }

/* 斗地主：左右对手、桌内出牌、底部展开手牌。 */
.game-landlord .tg-felt { top: 22%; background-size: 104% 113%; background-position: center top; }
.game-landlord .tg-center { top: 44%; }
.game-landlord .tg-table-status { top: 22%; left: 34%; right: 34%; }
.game-landlord .tg-seat:not(.tg-self) .tg-player { margin-top: 8cqh; }
.game-landlord .tg-hidden-hand { position: absolute; top: 100%; left: 0; right: 0; margin-top: 4px; padding: 0; white-space: nowrap; }
.game-landlord .tg-hidden-hand i { display: none; }
.game-landlord .tg-hidden-hand span { margin-left: 0; font-size: clamp(10px, 2cqh, 14px); padding: 2px 6px; border: 1px solid #efd094b3; background: #626591e3; border-radius: 5px; }
.game-landlord .tg-hand-shelf { left: 16%; right: 3%; bottom: 30px; }
.game-landlord .tg-my-hand { --card-height: clamp(65px, 27cqh, 205px); }
.game-landlord .tg-control-panel { left: 40%; right: 12%; bottom: calc(28cqh + 33px); justify-content: center; }
.game-landlord.tg-playing-cards .tg-control-panel { left: 67%; right: 3%; }
.game-landlord.tg-waiting .tg-control-panel { bottom: 26%; }
.game-landlord .tg-bottom-cards { position: absolute; z-index: 4; top: 2%; left: 50%; transform: translateX(-50%); gap: 3px; }
.game-landlord .tg-bottom-cards .tg-board-card img { object-fit: fill; }
.tg-seat-plays { position: absolute; inset: 0; z-index: 4; pointer-events: none; }
.tg-seat-play { position: absolute; width: 27%; container-type: inline-size; display: flex; flex-direction: column; gap: 4px; }
.tg-play-west { left: 19%; top: 29%; align-items: flex-start; }
.tg-play-east { right: 19%; top: 29%; align-items: flex-end; }
.tg-play-self { left: 44%; bottom: calc(28cqh + 26px); width: 20%; align-items: center; }
.tg-play-west .tg-played-cards { justify-content: flex-start; }
.tg-play-east .tg-played-cards { justify-content: flex-end; }
.tg-play-label { color: #fff0be; font-size: clamp(10px, 2cqh, 15px); text-shadow: 0 2px 3px #443963; }
.tg-pass-label { color: #ffe2a0; font-size: clamp(18px, 4cqh, 32px); font-weight: 700; }
.tg-seat-play .tg-played-cards { --public-width: min(44px, 7cqh, calc((100cqw - 10px) / (1 + (var(--public-count) - 1) * .38))); }
.tg-countdown { position: absolute; left: 18%; bottom: calc(28cqh + 41px); display: flex; align-items: center; flex-direction: row-reverse; gap: 7px; color: #ffe9ac; text-shadow: 0 1px 2px #413359; }
.tg-countdown span { font-size: 11px; max-width: min(130px, 17cqw); white-space: nowrap; text-overflow: ellipsis; overflow: hidden; }
.tg-countdown strong { display: grid; place-items: center; width: clamp(35px, 7cqh, 52px); height: clamp(35px, 7cqh, 52px); border: 2px solid #f7d58b; border-radius: 50%; background: linear-gradient(#7f9bc5, #4c5c91); box-shadow: inset 0 2px #e2e3ed80, 0 3px 0 #655168, 0 5px 7px #3c2c5666; color: #fff5cf; font-size: clamp(19px, 4cqh, 29px); font-variant-numeric: tabular-nums; }

/* 麻将：四向牌墙与弃牌，底部大手牌；方位随观察者旋转。 */
.game-mahjong .tg-felt { inset: 4% 3% 0; background-image: url('/ui/guochao/mahjong-felt.svg'); }
.game-mahjong .tg-table-status { top: 3%; left: 25%; right: 25%; }
.tg-center-mahjong { top: 45%; height: 46%; width: 44%; justify-content: center; }
.tg-mahjong-compass { width: clamp(48px, 12cqh, 85px); height: clamp(45px, 11cqh, 79px); border: 2px solid #e4c084; border-radius: 10px; background: linear-gradient(135deg, #8989b4, #535482); box-shadow: inset 0 1px #eae0f080, 0 3px 0 #806269, 0 5px 8px #4a355c80; display: grid; grid-template-columns: 1fr 1fr; align-content: center; gap: 3px; padding: 7px; z-index: 2; }
.tg-mahjong-compass > span { grid-row: span 2; align-self: center; font-size: clamp(16px, 4cqh, 26px); color: #ffe3a6; }
.tg-mahjong-compass strong { font-size: clamp(16px, 3.4cqh, 22px); color: #fff1ca; }
.tg-mahjong-compass small { font-size: 9px; color: #f5dfcc; }
.tg-rivers { position: absolute; inset: 0; pointer-events: none; }
.tg-river { position: absolute; width: 43%; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.tg-river > small { font-size: 8px; color: #f3decd; }
.tg-river-north { top: 0; left: 50%; transform: translateX(-50%) rotate(180deg); }
.tg-river-south { bottom: 0; left: 50%; transform: translateX(-50%); }
.tg-river-west { top: 50%; left: -8%; transform: translateY(-50%) rotate(90deg); }
.tg-river-east { top: 50%; right: -8%; transform: translateY(-50%) rotate(-90deg); }
.tg-tile-row { display: grid; grid-template-columns: repeat(6, auto); justify-content: center; gap: 2px; max-height: 13cqh; overflow: auto; pointer-events: auto; }
.tg-river-tile { display: inline-flex; width: clamp(13px, 3.4cqh, 24px); height: clamp(18px, 4.6cqh, 33px); border-radius: 2px; background: #fff5de; box-shadow: 0 2px #bfad8d; flex-shrink: 0; }
.tg-river-tile img { width: 100%; height: 100%; object-fit: contain; }
.tg-melds { display: flex; gap: 2px; justify-content: center; margin-top: 3px; }
.tg-center-mahjong > .tg-table-caption { position: absolute; bottom: -14%; font-size: 9px; }
.tg-walls { position: absolute; inset: 0; pointer-events: none; z-index: 1; }
.tg-wall { position: absolute; display: flex; justify-content: center; }
.tg-wall i { width: clamp(12px, 2.2cqw, 34px); height: clamp(21px, 6cqh, 47px); border-radius: 2px 2px 3px 3px; border: 1px solid #46557f; border-bottom: 5px solid #f0e4c9; background: linear-gradient(90deg, #55679b, #94a5cf 35%, #6779a7); box-shadow: 0 3px 2px #44345e77; }
.tg-wall-north { top: 9%; left: 27%; width: 42%; transform: scaleY(.8); }
.tg-wall-west { top: 42%; left: 3%; width: 32%; transform: rotate(100deg) scale(.85); }
.tg-wall-east { top: 42%; right: 3%; width: 32%; transform: rotate(-100deg) scale(.85); }
.game-mahjong .tg-hidden-hand { display: flex; margin-top: 3px; }
.game-mahjong .tg-hidden-hand i { display: none; }
.game-mahjong .tg-opponent-hand img { background: #fff5dc; border-bottom: 3px solid #cbbc98; }
.game-mahjong .tg-hand-shelf { left: 13%; right: 9%; bottom: 29px; }
.game-mahjong .tg-my-hand { --card-height: clamp(48px, 22cqh, 146px); --face-width: min(calc(var(--card-height) * .66), calc((100cqw - 12px - var(--hand-count) * 2px) / var(--hand-count))); }
.game-mahjong .tg-control-panel { bottom: calc(23cqh + 33px); right: 11%; left: 30%; }
.game-mahjong.tg-waiting .tg-control-panel { bottom: 25%; left: 18%; right: 18%; }

@container game-viewport (max-height: 480px) {
  .tg-round-result { width: min(270px, 42%); }
  .tg-round-result :deep(.round-feedback) { --result-title-size: 20px; --result-padding: 9px 14px; }
  .tg-round-result :deep(.round-feedback-subtitle), .tg-round-result :deep(.round-feedback-detail) { font-size: 9px; }
  .tg-toolbar { height: 42px; padding-block: 3px; }
  .tg-title h2 { font-size: 13px; }
  .tg-title p { font-size: 9px; }
  .tg-scroll { top: 42px; bottom: 23px; }
  .tg-dock { top: 42px; }
  .tg-lobby { top: 42px; padding: 10px 22px; }
  .tg-lobby-inner { gap: 8px; }
  .tg-lobby-heading h3 { font-size: 19px; margin-block: 3px; }
  .tg-lobby-heading p { display: none; }
  .tg-eyebrow { font-size: 9px; }
  .tg-lobby-balance { gap: 2px; font-size: 9px; }
  .tg-lobby-balance strong { font-size: 20px; }
  .tg-variant-tabs { margin-block: 0; gap: 5px; }
  .tg-variant-tabs .game-button { font-size: 11px; padding-inline: 10px; }
  .tg-win-notice :deep(.round-feedback) { --result-padding: 6px 13px; }
  .tg-win-notice :deep(.round-feedback-subtitle), .tg-win-notice :deep(.round-feedback-detail) { font-size: 8px; }
  .tg-sichuan-seat-status { gap: 2px; font-size: 8px; }
  .table-games .tg-tier-card { height: clamp(103px, 32cqh, 155px); padding: 10px; border-radius: 13px 13px 9px 9px; }
  .tg-tier-heading { top: 11px; left: 12px; gap: 4px; }
  .tg-tier-heading > span { font-size: 9px; letter-spacing: 1px; }
  .tg-tier-heading strong { font-size: 24px; }
  .tg-tier-art { top: -4%; width: 64%; height: 83%; }
  .tg-tier-terms { left: 10px; right: 10px; bottom: 29px; font-size: 9px; gap: 3px; }
  .tg-tier-terms b { font-size: 11px; }
  .tg-tier-selected { height: 22px; font-size: 9px; }
  .table-games .tg-mode-card { min-height: 67px; padding: 3px 11px 3px 5px; gap: 7px; }
  .tg-mode-card > .game-icon { width: 75px; height: 57px; }
  .tg-mode-card strong { font-size: 15px; }
  .tg-mode-card small { font-size: 9px; }
  .tg-mode-card > span:not(.tg-entry-arrow) { gap: 4px; }
  .tg-friends-entry .tg-check { right: 11px; bottom: 6px; font-size: 8px; }
  .tg-friends-entry .tg-mode-card > span:not(.tg-entry-arrow) { transform: translateY(-5px); }
  .tg-lobby-footer > p { font-size: 9px; }
  .tg-lobby-footer .game-button > .game-icon { width: 19px; height: 18px; }
  .tg-modal { padding: 14px; }
  .tg-modal-head { top: -14px; margin-bottom: 10px; padding-bottom: 7px; }
  .tg-modal-head > .game-icon { width: 54px; height: 42px; }
  .tg-modal-head h2 { font-size: 18px; }
  .tg-modal-hint { margin-block: 8px; font-size: 10px; }
  .tg-modal-footer { margin-top: 10px; padding-top: 10px; }
  .tg-wallet-strip { height: 23px; font-size: 9px; padding-block: 4px; }
  .tg-table-status { top: 39%; }
  .tg-notice, .tg-turn { font-size: 9px; }
  .game-texas:not(.tg-finished):not(.tg-waiting) .tg-center { width: 55%; column-gap: 8px; row-gap: 3px; }
  .game-texas:not(.tg-finished):not(.tg-waiting) .tg-pot { flex-direction: row; gap: 4px; }
  .game-texas:not(.tg-finished):not(.tg-waiting) .tg-chip-stack { width: 18px; height: 18px; }
  .game-texas:not(.tg-finished):not(.tg-waiting) .tg-table-caption { font-size: 8px; }
  .tg-turn b { height: 20px; min-width: 20px; }
  .table-games button:not(.tg-tier-card):not(.tg-mode-card):not(.tg-hand-card), .table-games input, .table-games select { min-height: 36px; font-size: 11px; padding: 6px 10px; }
  .tg-toolbar button { padding: 5px 9px; font-size: 10px; }
  .tg-player { padding: 4px; gap: 2px 4px; }
  .tg-seat-chips small, .tg-role, .tg-seat-tag { font-size: 7px; }
  .tg-seat-state, .tg-hand-name, .tg-settled { font-size: 8px; }
  .tg-hidden-hand { margin-top: 4px; }
  .tg-hidden-hand span { font-size: 8px; }
  .tg-control-panel { gap: 4px 7px; bottom: calc(25cqh + 25px); }
  .tg-control-panel .tg-actions { gap: 5px; }
  .tg-control-panel .tg-muted { font-size: 9px; }
  .tg-waiting .tg-control-panel { left: 14%; right: 12%; bottom: 20%; }
  .tg-field { font-size: 10px; gap: 4px; }
  .tg-field input { width: 67px; }
  .tg-field select { max-width: 94px; }
  .tg-hand-shelf { bottom: 27px; }
  .tg-table-welcome { gap: 4px; }
  .tg-table-welcome > span { font-size: 13px; }
  .tg-table-welcome p, .tg-table-welcome small { font-size: 9px; }
  .tg-crowded .tg-seat { width: 82px; }
  .tg-crowded .tg-player { gap: 3px; padding: 3px; }
  .tg-crowded .tg-avatar { width: 24px; height: 28px; }
  .tg-crowded .tg-nameplate h3 { font-size: 9px; }
  .tg-crowded .tg-seat-chips { font-size: 10px; }
  .tg-crowded .tg-seat-chips small { display: none; }
  .tg-crowded .tg-hidden-hand i { width: 19px; height: 28px; }
  .game-landlord .tg-control-panel { bottom: calc(28cqh + 28px); }
  .tg-countdown { bottom: calc(28cqh + 36px); }
  .game-mahjong .tg-control-panel { bottom: calc(22cqh + 27px); }
  /* 扑克设置留在手牌两侧，主要动作单行放在手牌上方，避免换行向桌心生长。 */
  .game-texas:not(.tg-waiting) .tg-my-hand,
  .game-golden_flower:not(.tg-waiting) .tg-my-hand { --card-height: 20cqh; }
  .game-texas:not(.tg-waiting) .tg-control-panel,
  .game-golden_flower:not(.tg-waiting) .tg-control-panel { left: 18%; right: 4%; bottom: calc(20cqh + 32px); flex-wrap: nowrap; }
  .game-texas:not(.tg-waiting) .tg-bet-fields,
  .game-golden_flower:not(.tg-waiting) .tg-bet-fields { position: absolute; left: 0; right: 0; top: calc(100% + 23px); flex-wrap: nowrap; justify-content: space-between; }
  .game-texas:not(.tg-waiting) .tg-bet-cost,
  .game-golden_flower:not(.tg-waiting) .tg-bet-cost { position: absolute; left: 0; top: calc(100% + 6px); max-width: 20%; text-align: left; }
  .game-texas:not(.tg-waiting) .tg-bet-fields .tg-field,
  .game-golden_flower:not(.tg-waiting) .tg-bet-fields .tg-field { font-size: 9px; }
  .game-texas:not(.tg-waiting) .tg-bet-fields input,
  .game-golden_flower:not(.tg-waiting) .tg-bet-fields input { width: 55px; }
  .game-golden_flower:not(.tg-waiting) .tg-bet-fields select { width: 68px; }
  .game-texas:not(.tg-waiting) .tg-bet-fields { top: calc(100% + 3px); }
  .game-texas:not(.tg-waiting) .tg-raise-control { flex-direction: column; align-items: flex-start; gap: 2px; }
  .game-texas:not(.tg-waiting) .tg-bet-cost { left: auto; right: 0; text-align: right; }
  .game-texas:not(.tg-finished):not(.tg-waiting) .tg-center { top: 44%; }
  .game-texas:not(.tg-finished):not(.tg-waiting) .tg-center .tg-board-card { width: clamp(25px, 8cqh, 38px); }
  .game-golden_flower:not(.tg-finished):not(.tg-waiting) .tg-center { display: grid; grid-template-columns: auto auto; justify-content: center; gap: 5px 10px; }
  .game-golden_flower:not(.tg-finished):not(.tg-waiting) .tg-poker-status { grid-column: 1 / -1; }
  .tg-compact-notice { display: inline; }
  .tg-poker-status .tg-notice { display: none; }
  .tg-poker-status .tg-turn { max-width: 100%; }
  .game-texas .tg-hidden-hand i,
  .game-golden_flower .tg-hidden-hand i { width: 15px; height: 21px; }
  .game-texas .tg-count-8 .tg-seat:nth-child(2), .game-texas .tg-count-8 .tg-seat:nth-child(8),
  .game-texas .tg-count-7 .tg-seat:nth-child(2), .game-texas .tg-count-7 .tg-seat:nth-child(7) { top: 43%; }
}
@media (prefers-reduced-motion: reduce) {
  .tg-deal-layer { display: none; }
  .tg-hand-card[data-card-dealing="true"] img, .tg-board-card[data-card-dealing="true"] img { animation: none; }
}

/* 结算占用桌心独立区域，重开操作单行置于完整手牌上方。 */
.table-games.tg-finished .tg-round-result { top: 43%; }
.game-mahjong .tg-seat[data-position="north"] { top: 3%; transform: translateX(-50%); }
.table-games.tg-finished .tg-my-hand { --card-height: clamp(48px, 18cqh, 162px); }
.table-games.tg-finished .tg-control-panel { left: 13%; right: 9%; bottom: calc(18cqh + 35px); flex-wrap: nowrap; justify-content: center; gap: 8px; }
.table-games.tg-finished .tg-control-panel > .tg-actions { flex-basis: auto; flex-wrap: nowrap; }
.table-games.tg-finished .tg-opponent-hand img { --public-width: min(30px, 7cqh, calc((100cqw - 8px) / (1 + (var(--public-count) - 1) * .38))); }
.game-texas.tg-finished .tg-round-result { left: 31%; width: min(360px, 30%); top: 45%; }
.game-golden_flower.tg-finished .tg-round-result { left: 31%; width: min(360px, 30%); top: 45%; }
.game-texas.tg-finished .tg-center { left: 75%; top: 45%; width: 31%; }
.game-texas.tg-finished .tg-center .tg-board-card { width: clamp(19px, 6cqh, 47px); }
@container game-viewport (max-height: 350px) {
  .table-games.tg-finished .tg-round-result :deep(.round-feedback) { --result-padding: 6px 10px; }
  .table-games.tg-finished .tg-round-result :deep(.result-copy) { gap: 1px; }
  .tg-lobby { padding-block: 6px; }
  .tg-lobby-inner { gap: 6px; }
  .tg-lobby-heading h3 { font-size: 16px; margin: 0; }
  .tg-eyebrow { display: none; }
  .tg-lobby-balance { flex-direction: row; align-items: baseline; gap: 8px; }
  .tg-lobby-balance strong { font-size: 17px; }
  .table-games .tg-tier-card { height: 104px; }
  .tg-tier-heading { top: 9px; }
  .tg-tier-heading strong { font-size: 22px; }
  .tg-tier-heading > span { display: none; }
  .tg-tier-terms { justify-content: flex-start; gap: 4px 7px; bottom: 26px; }
  .tg-tier-terms span:last-child { flex-basis: 100%; }
  .table-games .tg-mode-card { min-height: 59px; }
  .tg-mode-card > .game-icon { width: 58px; height: 49px; }
  .tg-mode-card strong { font-size: 14px; }
  .tg-friends-entry .tg-check { bottom: 4px; }
  .tg-friends-entry .tg-mode-card small { display: none; }
  .tg-entry-arrow { font-size: 25px; }
  .tg-lobby-footer .game-button { font-size: 10px; padding-inline: 8px; }
}
@container game-viewport (max-width: 620px) {
  .tg-toolbar { padding-inline: 8px; }
  .tg-title p { max-width: 210px; }
  .tg-toolbar .tg-actions { gap: 4px; }
  .tg-toolbar button { padding-inline: 7px; }
  .tg-seat { width: 76px; }
  .tg-waiting .tg-control-panel { left: 10%; right: 10%; }
}
/* 偏窄高窗口以桌宽约束横向牌列，避免按高度放大的牌背侵占中心信息区。 */
@container game-viewport (max-aspect-ratio: 4 / 3) {
  .game-texas .tg-hidden-hand i,
  .game-golden_flower .tg-hidden-hand i { width: clamp(15px, 4cqw, 36px); height: clamp(21px, 5.5cqw, 50px); }
  .game-texas:not(.tg-finished):not(.tg-waiting) .tg-center .tg-board-card { width: clamp(25px, 6cqw, 65px); }
}
</style>
