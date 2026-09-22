<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import dialogueConfig from "./dialogue.json";
import TableGames from "./TableGames.vue";
import GameTools from "./GameTools.vue";
import GameStatsPanel from "./GameStatsPanel.vue";
import { mountGameAudio, unmountGameAudio, playGameSound } from "./gameAudio";
import NonameGame from "./NonameGame.vue";
import RoomDirectory from "./RoomDirectory.vue";
import RoomInvite from "./RoomInvite.vue";
import CopyRoomCode from "./CopyRoomCode.vue";
import { parseRoomInvite, roomInviteCode, type RoomInvite as RoomInviteTarget } from "./roomInvites";
import type { DiscordSDK } from "@discord/embedded-app-sdk";
import GameViewport from "./GameViewport.vue";
import LandscapeNotice from "./LandscapeNotice.vue";
import GameIcon from "./GameIcon.vue";
import RoundFeedback from "./RoundFeedback.vue";
import BustBurst from "./BustBurst.vue";
import "./game-ui.css";
import { tableGameRules, type TableGameType } from "./tableGameRules";

type ViewMode =
  | "loading"
  | "game_hub"
  | "blackjack_mode_select"
  | "single"
  | "lobby"
  | "table_games"
  | "noname"
  | "table";
type RoomStage = "waiting" | "playing" | "dealer_turn" | "finished";

type PublicConfigResponse = {
  discord_client_id?: string;
  noname_available?: boolean;
};

type ProfileResponse = {
  success: boolean;
  user_id: string;
  username: string;
  avatar_url: string;
  balance: number;
};

type PlayerState = {
  user_id: string;
  username: string;
  avatar_url: string;
  seat_index: number;
  bet_amount: number;
  hand: string[];
  score: number;
  status: string;
  result: string | null;
  payout_amount: number;
  is_ready: boolean;
  is_current_turn: boolean;
  is_bot?: boolean;
};

type DealerState = {
  name: string;
  avatar_path: string;
  expression: string;
  hand: string[];
  score: number;
};

type RoomState = {
  room_id: string;
  host_user_id: string;
  max_players: number;
  state: RoomStage;
  current_turn_user_id: string | null;
  ready_player_count: number;
  all_players_ready: boolean;
  dealer: DealerState;
  players: PlayerState[];
  turn_deadline?: number | null;
};

type RoomEnvelope = {
  success: boolean;
  room?: RoomState;
  viewer_balance?: number;
};

type AutoJoinRoomResponse = RoomEnvelope & {
  session_key?: string;
};

type SingleGameState =
  | "player_turn"
  | "dealer_turn"
  | "finished_win"
  | "finished_loss"
  | "finished_push"
  | "finished_blackjack";

type SingleGameStatePayload = {
  user_id: string;
  bet_amount: number;
  game_state: SingleGameState;
  player_hand: string[];
  dealer_hand: string[];
  player_score: number;
  dealer_score: number;
};

type SingleGameEnvelope = {
  success: boolean;
  game: SingleGameStatePayload;
  new_balance?: number;
};

const viewMode = ref<ViewMode>("loading");
const lobbyStatsPanel = ref<'stats' | 'leaderboard' | null>(null);
const nonameAvailable = ref(false);
const selectedTableGame = ref<TableGameType>('texas');
const availableTableGames: TableGameType[] = ['texas', 'landlord', 'mahjong', 'golden_flower'];
const loadingText = ref("初始化中...");
const statusMessage = ref("");
const errorMessage = ref("");
const roomInput = ref("");
const showRoomDirectory = ref(false);
const directoryGameType = ref('');
const pendingTableRoom = ref('');
const inviteTarget = ref<RoomInviteTarget | null>(null);
let discordSdkInstance: DiscordSDK | null = null;
let launchRoomTarget: RoomInviteTarget | null = null;
const betInput = ref<number | null>(100);
const singleBetInput = ref<number | null>(100);
const roomState = ref<RoomState | null>(null);
const singleGame = ref<SingleGameStatePayload | null>(null);
const profile = ref<ProfileResponse | null>(null);
const requestInFlight = ref(false);
const blackjackRulesDialog = ref<HTMLDialogElement | null>(null);
type CardMotion = { kind: "deal" | "flip"; delay: number; token: number };
type HandSnapshot = Record<string, string[]>;
const cardMotions = ref<Record<string, CardMotion>>({});
const bustBursts = ref<Record<string, number>>({});
const cardRoundKey = ref(0);
const resultVisible = ref(false);
const resultAnimated = ref(false);
const resultKey = ref(0);
const presentationTimers = new Set<number>();
let motionToken = 0;
let lastCardMotionEnd = 0;
const dealerSpeech = ref("月月正在观察牌局...");
let dealerSpeechTimer: number | null = null;

const queryParams = new URLSearchParams(window.location.search);
const isEmbedded = queryParams.get("frame_id") != null;
const shouldUseDiscordAuth = ref(isEmbedded);
const runtimeDiscordClientId = ref("");
const discordSessionKey = ref("");
const discordChannelId = ref("");
const discordGuildId = ref("");

const devUserId = queryParams.get("dev_user_id")?.trim() ?? "";
const devUsername = queryParams.get("dev_username")?.trim() ?? "";
const devAvatarUrl = queryParams.get("dev_avatar")?.trim() ?? "";

const ASSET_VERSION =
  String(import.meta.env.VITE_ASSET_VERSION ?? "dev").trim() || "dev";

let accessToken: string | null = null;
let roomPollTimer: number | null = null;
let roomEpoch = 0;
let roomPollInFlight = false;

const viewerUserId = computed(() => String(profile.value?.user_id ?? ""));
const isDiscordMode = computed(() => shouldUseDiscordAuth.value);

const hostDisplayName = computed(() => {
  if (!roomState.value) return "";
  const hostId = String(roomState.value.host_user_id);
  return (
    roomState.value.players.find((p) => String(p.user_id) === hostId)?.username ??
    String(roomState.value.host_user_id)
  );
});

const players = computed(() => roomState.value?.players ?? []);

const dealer = computed(() => roomState.value?.dealer ?? null);

const seatPlayerMap = computed<Record<number, PlayerState | null>>(() => {
  const mapping: Record<number, PlayerState | null> = {
    0: null,
    1: null,
    2: null,
  };
  for (const player of players.value) {
    if (Object.prototype.hasOwnProperty.call(mapping, player.seat_index)) {
      mapping[player.seat_index] = player;
    }
  }
  return mapping;
});

const viewerPlayer = computed(() => {
  const uid = viewerUserId.value;
  if (!uid) return null;
  return players.value.find((p) => String(p.user_id) === uid) ?? null;
});

// 显示座位相对自己旋转；服务端座位顺序和行动顺序保持不变。
const displaySeats = computed(() => {
  const ownSeat = viewerPlayer.value?.seat_index ?? 1;
  return [
    { seatIndex: (ownSeat + 1) % 3, position: 'left' },
    { seatIndex: ownSeat, position: 'bottom' },
    { seatIndex: (ownSeat + 2) % 3, position: 'right' },
  ];
});

const isHost = computed(() => {
  if (!roomState.value) return false;
  return String(roomState.value.host_user_id) === viewerUserId.value;
});

const isMyTurn = computed(() => Boolean(viewerPlayer.value?.is_current_turn));

const isRoomBettingStage = computed(() => {
  const stage = roomState.value?.state;
  return stage === "waiting" || stage === "finished";
});

const canSetBet = computed(() => {
  return isRoomBettingStage.value && Boolean(viewerPlayer.value);
});

const canToggleReady = computed(() => {
  if (!roomState.value || roomState.value.state !== "waiting") {
    return false;
  }
  return Boolean(viewerPlayer.value) && Number(viewerPlayer.value?.bet_amount ?? 0) > 0;
});

const readyButtonText = computed(() => {
  if (!viewerPlayer.value) return "准备";
  return viewerPlayer.value.is_ready ? "取消准备" : "准备";
});

const canStartRound = computed(() => {
  if (!roomState.value || !isHost.value || roomState.value.state !== "waiting") {
    return false;
  }
  return Boolean(roomState.value.all_players_ready);
});

const canSingleStart = computed(() => {
  const amount = Number(singleBetInput.value ?? 0);
  if (!Number.isFinite(amount) || amount <= 0) {
    return false;
  }
  if (!singleGame.value) return true;
  return !["player_turn", "dealer_turn"].includes(singleGame.value.game_state);
});

const canSingleOperate = computed(
  () => singleGame.value?.game_state === "player_turn",
);

const canSingleDouble = computed(() => {
  if (!singleGame.value || singleGame.value.game_state !== "player_turn") {
    return false;
  }
  if (singleGame.value.player_hand.length !== 2) {
    return false;
  }
  return Number(profile.value?.balance ?? 0) >= Number(singleGame.value.bet_amount);
});

const singleBetOptions = computed(() => {
  const balance = Number(profile.value?.balance ?? 0);
  if (!Number.isFinite(balance) || balance <= 0) {
    return [] as { key: string; label: string; value: number }[];
  }

  const options = [
    { key: "small", label: "小", value: 100 },
    { key: "medium", label: "中", value: 200 },
    { key: "large", label: "大", value: 500 },
    { key: "all_in", label: "梭哈", value: Math.floor(balance) },
  ];

  const uniqueValues = new Set<number>();
  return options.filter((option) => {
    if (option.value <= 0 || option.value > balance || uniqueValues.has(option.value)) {
      return false;
    }
    uniqueValues.add(option.value);
    return true;
  });
});

const singleStateText = computed(() => {
  const game = singleGame.value;
  if (!game) return "请输入下注金额后点击开始对战。";
  if (game.game_state === "player_turn") return "当前轮到你操作。";
  if (game.game_state === "dealer_turn") return "月月正在结算本局。";
  if (game.game_state === "finished_blackjack") return "本局结束：BlackJack";
  if (game.game_state === "finished_win") return "本局结束：你赢了";
  if (game.game_state === "finished_push") return "本局结束：平局";
  return "本局结束：你输了";
});

const singleResultText = computed(() => {
  const game = singleGame.value;
  if (!game) return "";
  if (game.game_state === "finished_blackjack") return "BlackJack";
  if (game.game_state === "finished_win") return "胜利";
  if (game.game_state === "finished_push") return "平局";
  if (game.game_state === "finished_loss") return "失败";
  return "";
});

const ownScore = computed(() => viewMode.value === "single"
  ? singleGame.value?.player_score ?? 0
  : viewerPlayer.value?.score ?? 0);
const ownHandCount = computed(() => viewMode.value === "single"
  ? singleGame.value?.player_hand.length ?? 0
  : viewerPlayer.value?.hand.length ?? 0);
const ownScoreLabel = computed(() => !ownHandCount.value ? "待发牌"
  : ownScore.value > 21 ? "爆牌" : ownScore.value === 21 ? "21点" : "当前点数");
const blackjackResult = computed(() => {
  const single = viewMode.value === "single";
  const game = singleGame.value;
  const player = viewerPlayer.value;
  if (single ? !game?.game_state.startsWith("finished_") : roomState.value?.state !== "finished" || !player?.result) return null;
  const outcome = single ? game!.game_state.replace("finished_", "") : player!.result;
  const wager = single ? game!.bet_amount : player!.bet_amount;
  const payout = single
    ? outcome === "blackjack" ? Math.floor(wager * 2.5) : outcome === "win" ? wager * 2 : outcome === "push" ? wager : 0
    : player!.payout_amount;
  const net = payout - wager;
  const title = outcome === "blackjack" ? "天然21点" : outcome === "win" ? "胜利" : outcome === "push" ? "平局" : "失败";
  return {
    title,
    tone: (outcome === "loss" ? "loss" : outcome === "push" ? "push" : "win") as "win" | "loss" | "push",
    subtitle: `你${ownScore.value} · 荷官${single ? game!.dealer_score : dealer.value?.score ?? 0}`,
    detail: `${ownScore.value > 21 ? "爆牌 · " : ""}${net > 0 ? `净赢 +${net}` : net < 0 ? `净输 ${Math.abs(net)}` : "净输赢 0"} 灵石`,
  };
});

const roomStateText = computed(() => {
  const stage = roomState.value?.state;
  if (!stage) return "";
  if (stage === "waiting") return "等待下注";
  if (stage === "playing") return "玩家操作中";
  if (stage === "dealer_turn") return "月月结算中";
  return "本局结束";
});

const dealerAvatarSrc = computed(() => {
  const path = dealer.value?.avatar_path || "/character/normal.webp";
  return withAssetVersion(path);
});

function withAssetVersion(path: string): string {
  if (!path.startsWith("/")) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}v=${encodeURIComponent(ASSET_VERSION)}`;
}

function playerAvatarSrc(player: PlayerState | null): string {
  if (!player) return withAssetVersion("/character/normal.webp");
  if (player.avatar_url.startsWith("/")) {
    return withAssetVersion(player.avatar_url);
  }
  return player.avatar_url;
}

function cardImageSrc(card: string): string {
  if (card === "Hidden") {
    return withAssetVersion("/table-assets/card-back.png");
  }
  return withAssetVersion(`/cards/${card}.webp`);
}

function cardHandStyle(hand: string[] | undefined) {
  // 每张后续牌保留 36% 的左侧点数区；牌宽随实际张数和容器宽度一起缩放。
  return { "--hand-slots": 1 + Math.max(0, (hand?.length ?? 1) - 1) * 0.36 };
}

function cardMotionId(hand: string, index: number) {
  return `${hand}:${index}`;
}

function cardMotion(hand: string, index: number) {
  return cardMotions.value[cardMotionId(hand, index)];
}

function hasDealingCards(hand?: string) {
  return Object.keys(cardMotions.value).some(key => !hand || key.startsWith(`${hand}:`));
}

function schedulePresentation(callback: () => void, delay: number) {
  const timer = window.setTimeout(() => {
    presentationTimers.delete(timer);
    callback();
  }, delay);
  presentationTimers.add(timer);
}

function resetPresentation() {
  for (const timer of presentationTimers) window.clearTimeout(timer);
  presentationTimers.clear();
  cardMotions.value = {};
  bustBursts.value = {};
  lastCardMotionEnd = 0;
  resultVisible.value = false;
  resultAnimated.value = false;
  resultKey.value += 1;
}

function animateHandChanges(previous: HandSnapshot, next: HandSnapshot, newRound: boolean) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return 0;
  const hands = Object.entries(next);
  let duration = Math.max(0, lastCardMotionEnd - performance.now());
  for (const [handIndex, [hand, cards]] of hands.entries()) {
    cards.forEach((card, index) => {
      const oldCard = previous[hand]?.[index];
      const kind = newRound || oldCard === undefined ? "deal"
        : oldCard === "Hidden" && card !== "Hidden" ? "flip" : null;
      if (!kind) return;
      const delay = newRound ? Math.min((index * hands.length + handIndex) * 75, 675)
        : kind === "flip" ? 0 : Math.min(Math.max(0, index - (previous[hand]?.length ?? 0)) * 80, 320);
      const token = ++motionToken;
      const id = cardMotionId(hand, index);
      cardMotions.value[id] = { kind, delay, token };
      const end = delay + (kind === "flip" ? 520 : 440);
      duration = Math.max(duration, end);
      schedulePresentation(() => {
        if (cardMotions.value[id]?.token === token) delete cardMotions.value[id];
      }, end + 30);
    });
  }
  lastCardMotionEnd = performance.now() + duration;
  return duration;
}

function presentSnapshot(previous: HandSnapshot, next: HandSnapshot, options: { restore?: boolean; newRound?: boolean; finished: boolean; wasFinished: boolean }) {
  if (options.restore || options.newRound) {
    resetPresentation();
    cardRoundKey.value += 1;
  }
  if (options.restore) {
    resultVisible.value = options.finished;
    return 0;
  }
  const duration = animateHandChanges(previous, next, Boolean(options.newRound));
  if (!options.finished) {
    resultVisible.value = false;
    resultAnimated.value = false;
  } else if (!options.wasFinished || options.newRound) {
    resultVisible.value = false;
    resultAnimated.value = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    resultKey.value += 1;
    // 天然21点也先完成初始发牌，再展示结算；操作按钮始终可以使用。
    const key = resultKey.value;
    if (duration > 0) schedulePresentation(() => {
      if (key === resultKey.value && blackjackResult.value) resultVisible.value = true;
    }, duration + 40);
    else resultVisible.value = true;
  }
  return duration;
}

function animateBusts(previous: Record<string, number>, next: Record<string, number>, duration: number) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  for (const [hand, score] of Object.entries(next)) {
    if (score <= 21 || (previous[hand] ?? 0) > 21) continue;
    const token = ++motionToken;
    schedulePresentation(() => {
      bustBursts.value[hand] = token;
      schedulePresentation(() => {
        if (bustBursts.value[hand] === token) delete bustBursts.value[hand];
      }, 1200);
    }, duration + 20);
  }
}

function multiplayerScores(room: RoomState | null): Record<string, number> {
  const scores: Record<string, number> = { "multi:dealer": room?.dealer.score ?? 0 };
  for (const player of room?.players ?? []) scores[`multi:${player.user_id}`] = player.score;
  return scores;
}

function singleHands(game: SingleGameStatePayload | null): HandSnapshot {
  return { "single:player": game?.player_hand ?? [], "single:dealer": game?.dealer_hand ?? [] };
}

function multiplayerHands(room: RoomState | null): HandSnapshot {
  const hands: HandSnapshot = { "multi:dealer": room?.dealer.hand ?? [] };
  for (const player of room?.players ?? []) hands[`multi:${player.user_id}`] = player.hand;
  return hands;
}

function getPlayerStatusText(player: PlayerState): string {
  if (player.status === "waiting") {
    if (player.is_ready) return "已准备";
    if (player.bet_amount > 0) return "待准备";
    return "等待";
  }
  if (player.status === "playing") return "操作中";
  if (player.status === "stood") return "已停牌";
  if (player.status === "bust") return "爆牌";
  if (player.status === "blackjack") return "BlackJack";
  return "已完成";
}

function getPlayerResultText(player: PlayerState): string {
  if (!player.result) return "";
  if (player.result === "win") return "胜利";
  if (player.result === "loss") return "失败";
  if (player.result === "push") return "平局";
  return "BlackJack";
}


function extractDialogueList(category: string): string[] {
  const source = (dialogueConfig as Record<string, unknown>)[category];
  if (Array.isArray(source)) {
    return source.filter((item): item is string => typeof item === "string");
  }

  if (source && typeof source === "object") {
    const nested = source as Record<string, unknown>;
    const orderedKeys = ["any_bet", "low_bet", "medium_bet", "high_bet", "all_in"];
    const merged: string[] = [];
    const visited = new Set<string>();

    for (const key of orderedKeys) {
      const value = nested[key];
      if (!Array.isArray(value)) continue;
      for (const item of value) {
        if (typeof item === "string" && !visited.has(item)) {
          visited.add(item);
          merged.push(item);
        }
      }
    }

    for (const value of Object.values(nested)) {
      if (!Array.isArray(value)) continue;
      for (const item of value) {
        if (typeof item === "string" && !visited.has(item)) {
          visited.add(item);
          merged.push(item);
        }
      }
    }
    return merged;
  }

  return [];
}

function pickRandomLine(lines: string[], fallback: string): string {
  if (!lines.length) return fallback;
  const index = Math.floor(Math.random() * lines.length);
  return lines[index] ?? fallback;
}

function resolveDealerDialogueCategory(): string {
  if (viewMode.value === "table") {
    const stage = roomState.value?.state;
    if (stage === "waiting") return "new_round";
    if (stage === "playing") return "welcome";
    if (stage === "dealer_turn") return "loading";

    const result = viewerPlayer.value?.result;
    if (result === "win") return "end_game_win";
    if (result === "loss") return "end_game_loss";
    if (result === "push") return "end_game_push";
    return "new_round";
  }

  if (viewMode.value === "single") {
    const state = singleGame.value?.game_state;
    if (!state) return "welcome";
    if (state === "player_turn") return "welcome";
    if (state === "dealer_turn") return "loading";
    if (state === "finished_blackjack") return "blackjack";
    if (state === "finished_win") return "end_game_win";
    if (state === "finished_loss") return "end_game_loss";
    return "end_game_push";
  }

  if (viewMode.value === "lobby") return "bet_required";
  if (viewMode.value === "blackjack_mode_select") return "welcome";
  return "welcome";
}

function refreshDealerSpeech() {
  const category = resolveDealerDialogueCategory();
  const lines = extractDialogueList(category);
  const fallback = "月月正在观察牌局...";
  const betAmount = Number(singleGame.value?.bet_amount ?? viewerPlayer.value?.bet_amount ?? 0);
  const line = pickRandomLine(lines, fallback).replace(/\$\{amount\}/g, String(betAmount || 0));
  dealerSpeech.value = line;
}

function stopDealerSpeechLoop() {
  if (dealerSpeechTimer !== null) {
    window.clearInterval(dealerSpeechTimer);
    dealerSpeechTimer = null;
  }
}

function startDealerSpeechLoop() {
  stopDealerSpeechLoop();
  refreshDealerSpeech();
  dealerSpeechTimer = window.setInterval(() => {
    refreshDealerSpeech();
  }, 3800);
}

function buildRequestHeaders(includeJson: boolean): HeadersInit {
  const headers: HeadersInit = {};
  if (includeJson) {
    headers["Content-Type"] = "application/json";
  }

  if (shouldUseDiscordAuth.value && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  if (!shouldUseDiscordAuth.value) {
    if (devUserId) headers["X-Dev-User-Id"] = devUserId;
    if (devUsername) headers["X-Dev-Username"] = devUsername;
    if (devAvatarUrl) headers["X-Dev-Avatar-Url"] = devAvatarUrl;
  }

  return headers;
}

async function apiCall<T>(
  endpoint: string,
  method: "GET" | "POST",
  body?: unknown,
  retries = method === "GET" ? 1 : 0,
): Promise<T> {
  // 写请求不自动重发，网络超时可能发生在服务端已完成要牌/扣款之后。
  if (method === "POST") roomEpoch += 1;
  let lastError: unknown = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const includeJson = method !== "GET";
      const headers = buildRequestHeaders(includeJson);

      const response = await fetch(endpoint, {
        method,
        headers,
        body: includeJson && body !== undefined ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(15000),
      });

      if (!response.ok) {
        const errorData = await response
          .json()
          .catch(() => ({ detail: "请求失败，服务端返回异常响应" }));
        throw Object.assign(new Error(typeof errorData.detail === "string" ? errorData.detail : "请求参数不正确"), { status: response.status });
      }

      const result = (await response.json()) as T;
      if (method === 'POST' && (endpoint.startsWith('/api/game/') || endpoint.startsWith('/api/multi/room/'))) {
        if (/\/(bet|double)$/.test(endpoint)) playGameSound('raise');
        else if (/\/(start|hit|stand)$/.test(endpoint)) playGameSound('deal');
      }
      return result;
    } catch (error) {
      lastError = error;
      if (attempt < retries && !Number((error as { status?: number })?.status)) {
        await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
      } else break;
    }
  }

  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error("未知网络错误");
}

async function fetchPublicConfig(): Promise<void> {
  const response = await fetch("/api/config");
  if (!response.ok) {
    const errorData = await response
      .json()
      .catch(() => ({ detail: "获取配置失败" }));
    throw new Error(errorData.detail || "获取配置失败");
  }

  const configData = (await response.json()) as PublicConfigResponse;
  nonameAvailable.value = configData.noname_available === true;
  runtimeDiscordClientId.value = String(configData.discord_client_id ?? "").trim();
}

async function setupDiscordSdk(resolvedClientId: string): Promise<string> {
  const sdkModule = await import("@discord/embedded-app-sdk");
  const DiscordSDKCtor = sdkModule.DiscordSDK;
  const discordSdk = new DiscordSDKCtor(resolvedClientId);

  await discordSdk.ready();
  discordSdkInstance = discordSdk;
  launchRoomTarget = parseRoomInvite(discordSdk.customId);
  const { code } = await discordSdk.commands.authorize({
    client_id: discordSdk.clientId,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify", "guilds"],
  });

  const tokenResponse = await fetch("/api/token", {
    method: "POST",
    headers: buildRequestHeaders(true),
    body: JSON.stringify({ code }),
  });

  if (!tokenResponse.ok) {
    const errorData = await tokenResponse
      .json()
      .catch(() => ({ detail: "Token 交换失败" }));
    throw new Error(errorData.detail || "Token 交换失败");
  }

  const tokenPayload = await tokenResponse.json();
  accessToken = String(tokenPayload.access_token ?? "").trim();

  if (!accessToken) {
    throw new Error("Discord access token 为空");
  }

  const auth = await discordSdk.commands.authenticate({ access_token: accessToken });
  if (!auth) {
    throw new Error("Discord authenticate 失败");
  }

  const instanceId = String((discordSdk as { instanceId?: string }).instanceId ?? "").trim();
  const channelId = String(
    (discordSdk as { channelId?: string | null }).channelId ?? "",
  ).trim();
  const guildId = String((discordSdk as { guildId?: string | null }).guildId ?? "").trim();

  discordChannelId.value = channelId;
  discordGuildId.value = guildId;

  const sessionKey = instanceId
    ? `instance:${instanceId}`
    : channelId
      ? `channel:${guildId || "dm"}:${channelId}`
      : "";

  if (!sessionKey) {
    throw new Error("无法识别 Discord 活动会话，无法自动加入房间");
  }

  return sessionKey;
}

async function loadProfile(): Promise<void> {
  const data = await apiCall<ProfileResponse>("/api/profile", "GET");
  profile.value = data;
}

function applyRoomEnvelope(data: RoomEnvelope | { room_closed?: boolean; room_id?: string }, restore = false, startedRound = false) {
  if ("room_closed" in data && data.room_closed) {
    resetPresentation();
    roomState.value = null;
    viewMode.value = "lobby";
    statusMessage.value = `房间 ${data.room_id ?? ""} 已关闭`;
    stopRoomPolling();
    return;
  }

  const envelope = data as RoomEnvelope;
  if (envelope.room) {
    const previous = roomState.value;
    const next = envelope.room;
    const isRestore = restore || !previous || previous.room_id !== next.room_id;
    const newRound = startedRound || (!isRestore && (previous.state === "waiting" || previous.state === "finished")
      && next.state !== "waiting" && (previous.state !== next.state
        || JSON.stringify(multiplayerHands(previous)) !== JSON.stringify(multiplayerHands(next))));
    const duration = presentSnapshot(multiplayerHands(previous), multiplayerHands(next), {
      restore: isRestore, newRound, finished: next.state === "finished", wasFinished: previous?.state === "finished",
    });
    if (!isRestore) animateBusts(newRound ? {} : multiplayerScores(previous), multiplayerScores(next), duration);
    roomState.value = envelope.room;
    viewMode.value = "table";
    roomInput.value = envelope.room.room_id;
    sessionStorage.setItem(`blackjack.room.${viewerUserId.value}`, envelope.room.room_id);
    startRoomPolling();
  } else {
    resetPresentation();
    roomState.value = null;
    viewMode.value = "lobby";
    stopRoomPolling();
  }

  if (profile.value && envelope.viewer_balance !== undefined) {
    profile.value.balance = Number(envelope.viewer_balance);
  }
}

function applySingleEnvelope(data: SingleGameEnvelope, newRound = false) {
  const previous = singleGame.value;
  const duration = presentSnapshot(singleHands(previous), singleHands(data.game), {
    newRound, finished: data.game.game_state.startsWith("finished_"), wasFinished: Boolean(previous?.game_state.startsWith("finished_")),
  });
  animateBusts(newRound ? {} : { "single:player": previous?.player_score ?? 0, "single:dealer": previous?.dealer_score ?? 0 },
    { "single:player": data.game.player_score, "single:dealer": data.game.dealer_score }, duration);
  singleGame.value = data.game;
  viewMode.value = "single";
  if (profile.value && data.new_balance !== undefined) {
    profile.value.balance = Number(data.new_balance);
  }
}

async function autoJoinCurrentSession(showNotice = false) {
  if (requestInFlight.value) return;

  const sessionKey = discordSessionKey.value.trim();
  if (!sessionKey) {
    errorMessage.value = "当前未获取到 Discord 会话标识";
    return;
  }

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<AutoJoinRoomResponse>("/api/multi/room/auto-join", "POST", {
      session_key: sessionKey,
    });
    applyRoomEnvelope(data, true);

    if (data.session_key) {
      const normalized = String(data.session_key).trim();
      if (normalized) {
        discordSessionKey.value = normalized;
      }
    }

    if (showNotice) {
      statusMessage.value = "已连接当前 Discord 会话房间";
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "自动加入房间失败";
  } finally {
    requestInFlight.value = false;
  }
}

function clearNotices() {
  statusMessage.value = "";
  errorMessage.value = "";
}

function enterGameHub() {
  pendingTableRoom.value = '';
  resetPresentation();
  stopRoomPolling();
  viewMode.value = "game_hub";
}

function enterBlackjackModeSelect() {
  resetPresentation();
  stopRoomPolling();
  viewMode.value = "blackjack_mode_select";
}

function openTableGame(gameType: TableGameType) {
  pendingTableRoom.value = '';
  resetPresentation();
  clearNotices();
  stopRoomPolling();
  selectedTableGame.value = gameType;
  viewMode.value = 'table_games';
}

function openRoomDirectory(gameType = '') {
  directoryGameType.value = gameType;
  showRoomDirectory.value = true;
}

async function joinListedRoom(room: { room_id: string; game_type: string }): Promise<boolean> {
  clearNotices();
  if (room.game_type === 'blackjack') {
    const data = await apiCall<RoomEnvelope>('/api/multi/room/join', 'POST', { room_id: room.room_id }, 0);
    applyRoomEnvelope(data, true);
  } else {
    pendingTableRoom.value = room.room_id;
    selectedTableGame.value = (room.game_type === 'sichuan_mahjong' ? 'mahjong' : room.game_type) as TableGameType;
    viewMode.value = 'table_games';
  }
  return true;
}

function applySingleBetOption(amount: number) {
  if (!Number.isFinite(amount) || amount <= 0) return;
  singleBetInput.value = amount;
}

async function enterSingleMode() {
  resetPresentation();
  clearNotices();
  stopRoomPolling();
  viewMode.value = "single";
  requestInFlight.value = true;
  try {
    const data = await apiCall<{ game: SingleGameStatePayload | null; new_balance?: number }>("/api/game/current", "GET");
    singleGame.value = data.game;
    presentSnapshot({}, singleHands(data.game), { restore: true, finished: Boolean(data.game?.game_state.startsWith("finished_")), wasFinished: false });
    if (profile.value && data.new_balance !== undefined) profile.value.balance = data.new_balance;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "恢复单人牌局失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function forfeitSingleGame() {
  if (requestInFlight.value) return;

  requestInFlight.value = true;
  clearNotices();
  try {
    await apiCall<{ success: boolean; message: string }>("/api/game/forfeit", "POST", {});
    resetPresentation();
    singleGame.value = null;
    statusMessage.value = "已放弃当前单人对局";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "放弃单人对局失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function startSingleGame() {
  if (requestInFlight.value) return;

  const amount = Number(singleBetInput.value ?? 0);
  if (!Number.isFinite(amount) || amount <= 0) {
    errorMessage.value = "请输入有效下注金额";
    return;
  }

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<SingleGameEnvelope>("/api/game/start", "POST", { amount });
    applySingleEnvelope(data, true);
    statusMessage.value = "单人对战已开始";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "开始单人对战失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function singleHit() {
  if (requestInFlight.value || !singleGame.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<SingleGameEnvelope>("/api/game/hit", "POST", {});
    applySingleEnvelope(data);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "要牌失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function singleStand() {
  if (requestInFlight.value || !singleGame.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<SingleGameEnvelope>("/api/game/stand", "POST", {});
    applySingleEnvelope(data);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "停牌失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function singleDouble() {
  if (requestInFlight.value || !singleGame.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<SingleGameEnvelope>("/api/game/double", "POST", {});
    applySingleEnvelope(data);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "加倍失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function enterMultiMode() {
  clearNotices();
  if (roomState.value) {
    resetPresentation();
    resultVisible.value = roomState.value.state === "finished";
    viewMode.value = "table";
    startRoomPolling();
    void refreshRoom(false);
    return;
  }

  const savedRoom = sessionStorage.getItem(`blackjack.room.${viewerUserId.value}`);
  if (savedRoom) {
    try {
      const data = await apiCall<RoomEnvelope>("/api/multi/room/join", "POST", { room_id: savedRoom });
      applyRoomEnvelope(data, true);
      return;
    } catch {
      sessionStorage.removeItem(`blackjack.room.${viewerUserId.value}`);
    }
  }

  if (isDiscordMode.value) {
    await autoJoinCurrentSession(true);
    return;
  }

  viewMode.value = "lobby";
}

async function createRoom() {
  if (requestInFlight.value) return;
  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/create", "POST");
    applyRoomEnvelope(data, true);
    statusMessage.value = "房间已创建";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "创建房间失败";
  } finally {
    requestInFlight.value = false;
  }
}

function recruitTeammates() {
  if (roomState.value) void openRoomInvite({ game_type: 'blackjack', room_id: roomState.value.room_id });
}

async function shareRoom(room: RoomInviteTarget): Promise<string> {
  if (!discordSdkInstance) throw new Error('请在 Discord 小活动内分享');
  const result = await discordSdkInstance.commands.shareLink({
    message: `来月月茶楼一起玩！房间号 ${room.room_id}`,
    custom_id: roomInviteCode(room),
  });
  if (result.didSendMessage) return '已通过 Discord 分享邀请';
  if (result.didCopyLink) return '已复制 Discord 邀请链接';
  return result.success ? '邀请分享完成' : '已取消分享';
}

async function openRoomInvite(room: RoomInviteTarget) {
  inviteTarget.value = room;
  if (!runtimeDiscordClientId.value) {
    try { await fetchPublicConfig(); } catch { /* 仍可复制房间号。 */ }
  }
}

async function joinRoom() {
  if (requestInFlight.value) return;

  const roomId = roomInput.value.trim().toUpperCase();
  if (!roomId) {
    errorMessage.value = "请输入房间号";
    return;
  }

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/join", "POST", {
      room_id: roomId,
    });
    applyRoomEnvelope(data, true);
    statusMessage.value = "已加入房间";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "加入房间失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function leaveRoom() {
  if (requestInFlight.value || !roomState.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope | { room_closed?: boolean; room_id?: string }>(
      "/api/multi/room/leave",
      "POST",
      { room_id: roomState.value.room_id },
    );
    if (profile.value && "viewer_balance" in data && data.viewer_balance !== undefined) {
      profile.value.balance = Number(data.viewer_balance);
    }
    roomState.value = null;
    resetPresentation();
    sessionStorage.removeItem(`blackjack.room.${viewerUserId.value}`);
    viewMode.value = "lobby";
    stopRoomPolling();
    statusMessage.value = "已离开房间";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "离开房间失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function refreshRoom(showNotice = false) {
  if (!roomState.value || roomPollInFlight || requestInFlight.value) return;
  const requestedRoom = roomState.value.room_id;
  const epoch = roomEpoch;
  roomPollInFlight = true;

  try {
    const roomId = encodeURIComponent(roomState.value.room_id);
    const data = await apiCall<RoomEnvelope>(`/api/multi/room/${roomId}`, "GET", undefined, 0);
    if (epoch !== roomEpoch || requestInFlight.value || roomState.value?.room_id !== requestedRoom) return;
    applyRoomEnvelope(data);
    if (showNotice) {
      statusMessage.value = "房间状态已同步";
    }
  } catch (error) {
    if (epoch !== roomEpoch) return;
    errorMessage.value = error instanceof Error ? error.message : "同步暂时中断，正在重连";
    const status = Number((error as { status?: number })?.status);
    if (status === 403 || (status === 400 && errorMessage.value.includes("房间不存在"))) {
      roomState.value = null;
      sessionStorage.removeItem(`blackjack.room.${viewerUserId.value}`);
      viewMode.value = "lobby";
      stopRoomPolling();
    }
  } finally {
    roomPollInFlight = false;
  }
}

async function setBet() {
  if (requestInFlight.value || !roomState.value) return;

  const amount = Number(betInput.value ?? 0);
  if (!Number.isFinite(amount) || amount <= 0) {
    errorMessage.value = "请输入有效下注金额";
    return;
  }

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/bet", "POST", {
      room_id: roomState.value.room_id,
      amount,
    });
    applyRoomEnvelope(data);
    statusMessage.value = "下注已更新，请点击准备";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "设置下注失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function toggleReady() {
  if (!viewerPlayer.value) return;
  await setReady(!viewerPlayer.value.is_ready);
}

async function toggleRoomBot() {
  if (requestInFlight.value || !roomState.value || !isHost.value || !isRoomBettingStage.value) return;
  const includeYueyue = !players.value.some(player => player.is_bot);
  requestInFlight.value = true;
  clearNotices();
  try {
    const data = await apiCall<RoomEnvelope>('/api/multi/room/bot', 'POST', {
      room_id: roomState.value.room_id,
      include_yueyue: includeYueyue,
    });
    applyRoomEnvelope(data);
    statusMessage.value = includeYueyue ? '月月已作为玩家加入，下注跟随房主并自动准备' : '已移除月月陪玩';
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '设置陪玩失败';
  } finally {
    requestInFlight.value = false;
  }
}

async function setReady(ready: boolean) {
  if (requestInFlight.value || !roomState.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/ready", "POST", {
      room_id: roomState.value.room_id,
      ready,
    });
    applyRoomEnvelope(data);
    statusMessage.value = ready ? "已准备，等待其他玩家" : "已取消准备";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "设置准备状态失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function continueReady() {
  if (requestInFlight.value || !roomState.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/continue-ready", "POST", {
      room_id: roomState.value.room_id,
    });
    applyRoomEnvelope(data);
    statusMessage.value = "已沿用上一局下注并准备";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "继续准备失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function startRound() {
  if (requestInFlight.value || !roomState.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/start", "POST", {
      room_id: roomState.value.room_id,
    });
    applyRoomEnvelope(data, false, true);
    statusMessage.value = "本局开始";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "开始失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function hit() {
  if (requestInFlight.value || !roomState.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/hit", "POST", {
      room_id: roomState.value.room_id,
    });
    applyRoomEnvelope(data);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "要牌失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function stand() {
  if (requestInFlight.value || !roomState.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/stand", "POST", {
      room_id: roomState.value.room_id,
    });
    applyRoomEnvelope(data);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "停牌失败";
  } finally {
    requestInFlight.value = false;
  }
}

function stopRoomPolling() {
  roomEpoch += 1;
  if (roomPollTimer !== null) {
    window.clearInterval(roomPollTimer);
    roomPollTimer = null;
  }
}

function startRoomPolling() {
  if (roomPollTimer !== null) return;
  roomPollTimer = window.setInterval(() => {
    void refreshRoom(false);
  }, 1500);
}

async function bootstrap() {
  loadingText.value = "正在连接服务...";

  try {
    if (isEmbedded) {
      try {
        await fetchPublicConfig();
        if (!runtimeDiscordClientId.value) {
          throw new Error("DISCORD_CLIENT_ID 未配置");
        }
        discordSessionKey.value = await setupDiscordSdk(runtimeDiscordClientId.value);
      } catch (embeddedError) {
        throw embeddedError instanceof Error ? embeddedError : new Error("Discord 登录失败，请重新打开活动");
      }
    } else {
      shouldUseDiscordAuth.value = false;
      discordSessionKey.value = "";
      discordChannelId.value = "";
      discordGuildId.value = "";
    }

    await loadProfile();
    viewMode.value = "game_hub";
    loadingText.value = "初始化完成";
    // 邀请房间优先于本地恢复和频道默认房，不存在时留在大厅。
    if (launchRoomTarget) {
      try { await joinListedRoom(launchRoomTarget); }
      catch (reason) { errorMessage.value = reason instanceof Error ? `邀请入座失败：${reason.message}` : '邀请房间暂时无法加入'; }
    }
  } catch (error) {
    loadingText.value = "初始化失败";
    errorMessage.value = error instanceof Error ? error.message : "初始化异常";
  }
}

watch(
  () => [viewMode.value, roomState.value?.state ?? "", singleGame.value?.game_state ?? ""],
  () => {
    startDealerSpeechLoop();
  },
  { immediate: true },
);

onMounted(() => {
  mountGameAudio();
  void bootstrap();
});

onBeforeUnmount(() => {
  unmountGameAudio();
  resetPresentation();
  stopRoomPolling();
  stopDealerSpeechLoop();
});
</script>

<template>
  <div :class="['multi-root', { 'table-fullscreen': viewMode === 'single' || viewMode === 'table' || viewMode === 'table_games' || viewMode === 'noname' }]">
    <div v-if="viewMode === 'loading'" class="panel loading-panel">
      <h2>月月游戏中心</h2>
      <p>{{ loadingText }}</p>
    </div>

    <template v-else>
      <header v-if="viewMode !== 'single' && viewMode !== 'table' && viewMode !== 'table_games' && viewMode !== 'noname'" class="top-bar">
        <div class="title-group">
          <span class="lobby-eyebrow">茶香一盏 · 好牌一局</span>
          <h1>月月游戏中心</h1>
          <p>找张喜欢的牌桌，和月月一起开局</p>
        </div>

        <div v-if="profile" class="lobby-profile-actions">
        <GameTools :profile="profile" :api-call="apiCall" audio-only />
        <button type="button" class="profile-chip" aria-label="查看个人信息与统计" @click="lobbyStatsPanel = 'stats'">
          <img class="profile-avatar" :src="playerAvatarSrc({
            user_id: String(profile.user_id),
            username: profile.username,
            avatar_url: profile.avatar_url,
            seat_index: -1,
            bet_amount: 0,
            hand: [],
            score: 0,
            status: '',
            result: null,
            payout_amount: 0,
            is_ready: false,
            is_current_turn: false
          })" alt="玩家头像" />
          <div class="profile-meta">
            <div class="profile-name">{{ profile.username }}</div>
            <div class="profile-balance">余额：{{ profile.balance }}</div>
          </div>
        </button>
        </div>
      </header>

      <section v-if="viewMode === 'game_hub'" class="lobby-panel game-hub-panel">
        <div class="lobby-section-heading"><h3>今晚，玩点什么？</h3><button class="game-button" @click="openRoomDirectory()">房间列表</button></div>
        <div class="game-grid hub-game-grid" :style="{ '--hub-columns': nonameAvailable ? 7 : 6 }">
          <button v-if="nonameAvailable" class="game-card" @click="viewMode = 'noname'">
            <span class="game-card-art"><svg viewBox="0 0 160 140" aria-hidden="true"><path d="M20 30 80 10l60 20v55l-60 45-60-45z" fill="#684877" stroke="#edc278" stroke-width="5"/><path d="m45 35 72 66m-2-67-70 69" stroke="#ffe5a3" stroke-width="8"/><text x="80" y="85" text-anchor="middle" fill="#fff1ca" font-size="42">杀</text></svg></span>
            <span class="game-card-copy"><span class="game-name">三国杀</span><span class="game-desc">无名杀 · 娱乐试玩</span></span>
          </button>
          <button class="game-card blackjack-card" :disabled="requestInFlight" @click="enterBlackjackModeSelect">
            <span class="game-card-art"><GameIcon name="blackjack" /></span>
            <span class="game-card-copy"><span class="game-name">21点</span><span class="game-desc">立即游玩</span></span>
            <span class="card-arrow" aria-hidden="true">◆</span>
          </button>
          <button v-for="gameType in availableTableGames" :key="gameType" :class="['game-card', `${gameType}-card`]" :disabled="requestInFlight" @click="openTableGame(gameType)">
            <span class="game-card-art"><GameIcon :name="gameType" /></span>
            <span class="game-card-copy"><span class="game-name">{{ tableGameRules[gameType].title }}</span><span class="game-desc">单人挑战 / 多人同桌</span></span>
            <span class="card-arrow" aria-hidden="true">◆</span>
          </button>
          <button class="game-card leaderboard-card" @click="lobbyStatsPanel = 'leaderboard'">
            <span class="game-card-art"><GameIcon name="leaderboard" /></span>
            <span class="game-card-copy"><span class="game-name">排行榜</span><span class="game-desc">当日盈利 / 总计盈利</span></span>
            <span class="card-arrow" aria-hidden="true">◆</span>
          </button>
        </div>
        <p class="lobby-footnote"><span aria-hidden="true">◆</span> 棋牌游戏使用账户灵石<template v-if="nonameAvailable"> · 三国杀为免费娱乐模式</template></p>
      </section>

      <GameViewport v-else-if="viewMode === 'noname' && profile">
        <NonameGame :username="profile.username" :api-call="apiCall" @back="enterGameHub" />
      </GameViewport>
      <GameViewport v-else-if="viewMode === 'table_games' && profile">
        <TableGames :key="selectedTableGame" :game-type="selectedTableGame" :initial-room-id="pendingTableRoom" :profile="profile" :api-call="apiCall" @back="enterGameHub" @balance="profile.balance = $event" @invite="openRoomInvite" @join-failed="enterGameHub(); errorMessage = $event" />
      </GameViewport>

      <section v-else-if="viewMode === 'blackjack_mode_select'" class="lobby-panel mode-panel">
        <div class="lobby-section-heading"><h3>21点 · 选个座位</h3><button class="game-button" @click="openRoomDirectory('blackjack')">房间列表</button></div>
        <div class="game-grid mode-game-grid">
          <button class="game-card mode-card solo-card" :disabled="requestInFlight" @click="enterSingleMode">
            <span class="game-card-art"><GameIcon name="solo" /></span>
            <span class="game-card-copy"><span class="game-name">单人对战</span><span class="game-desc">你 vs 月月</span></span>
            <span class="card-arrow" aria-hidden="true">◆</span>
          </button>
          <button class="game-card mode-card friends-card" :disabled="requestInFlight" @click="enterMultiMode">
            <span class="game-card-art"><GameIcon name="friends" /></span>
            <span class="game-card-copy"><span class="game-name">多人对战</span><span class="game-desc">最多3人同桌</span></span>
            <span class="card-arrow" aria-hidden="true">◆</span>
          </button>
        </div>
        <div class="toolbar-actions">
          <button class="game-button quiet" @click="blackjackRulesDialog?.showModal()">玩法规则</button>
          <button class="game-button" :disabled="requestInFlight" @click="enterGameHub">返回上一级</button>
        </div>
      </section>

      <GameViewport v-else-if="viewMode === 'single'">
      <section class="blackjack-table single-mode-view" :class="{ 'has-round-result': resultVisible && blackjackResult }" :data-dealing="hasDealingCards()" aria-label="单人21点牌桌">
        <div class="table-toolbar">
          <div class="table-heading">
            <strong>单人21点</strong>
            <span>{{ singleStateText }}</span>
          </div>
          <div class="toolbar-actions">
            <GameTools v-if="profile" :profile="profile" :api-call="apiCall" game-type="blackjack" />
            <button class="game-button" @click="blackjackRulesDialog?.showModal()">玩法规则</button>
            <button class="game-button" :disabled="requestInFlight" @click="enterBlackjackModeSelect">返回</button>
            <button class="game-button" :disabled="requestInFlight || !canSingleOperate" @click="forfeitSingleGame">放弃</button>
          </div>
        </div>

        <div class="table-scroll" tabindex="0" aria-label="牌桌与手牌">
          <div class="casino-stage single-board-content">
            <img class="felt-table" src="/ui/guochao/table-felt.svg" alt="" aria-hidden="true">
            <section class="hand-area dealer-seat">
              <BustBurst v-if="bustBursts['single:dealer']" :key="bustBursts['single:dealer']" />
              <div class="dealer-identity">
                <img :src="withAssetVersion(`/character/${singleResultText === '胜利' ? 'lose' : singleResultText === '失败' ? 'win' : 'normal'}.webp`)" alt="荷官月月" class="table-dealer-image">
                <div><span class="seat-role">荷官</span><h2>月月 <span class="score-badge">{{ singleGame?.dealer_score ?? 0 }}</span></h2></div>
              </div>
              <TransitionGroup name="card" tag="div" class="card-hand dealer-hand" :style="cardHandStyle(singleGame?.dealer_hand)" :data-dealing="hasDealingCards('single:dealer')">
                <img v-for="(card, index) in singleGame?.dealer_hand || []" :key="`${cardRoundKey}-single-dealer-${index}`" :class="{ 'card-dealing': cardMotion('single:dealer', index)?.kind === 'deal', 'card-flipping': cardMotion('single:dealer', index)?.kind === 'flip' }" :data-card-dealing="Boolean(cardMotion('single:dealer', index))" :data-card-motion="cardMotion('single:dealer', index)?.kind" :style="{ '--deal-delay': `${cardMotion('single:dealer', index)?.delay ?? 0}ms` }" :src="cardImageSrc(card)" :alt="card === 'Hidden' ? '暗牌' : card" class="playing-card">
                <span v-if="!singleGame?.dealer_hand.length" key="dealer-placeholder" class="card-placeholder">待发牌</span>
              </TransitionGroup>
            </section>
            <div v-if="!blackjackResult && !canSingleOperate" class="table-center-mark">
              <span class="table-brand">月月茶楼 · 以牌会友</span>
              <strong class="table-game-name">21 点</strong>
              <div class="table-chip-stack" aria-hidden="true"><i></i><i></i><i></i></div>
              <p class="center-caption">{{ singleGame ? '好运就在下一张' : '请下注，准备开局' }}</p>
            </div>
            <section class="hand-area single-player-seat seat-bottom">
              <div class="own-score" aria-label="你的点数" :data-score="ownScore" :data-bust="ownScore > 21" :data-twenty-one="ownScore === 21"><span>{{ ownScoreLabel }}</span><strong>{{ ownHandCount ? ownScore : '—' }}</strong></div>
              <BustBurst v-if="bustBursts['single:player']" :key="bustBursts['single:player']" class="player-bust-burst" />
              <TransitionGroup name="card" tag="div" class="card-hand player-hand" :style="cardHandStyle(singleGame?.player_hand)" :data-dealing="hasDealingCards('single:player')">
                <img v-for="(card, index) in singleGame?.player_hand || []" :key="`${cardRoundKey}-single-player-${index}`" :class="{ 'card-dealing': cardMotion('single:player', index)?.kind === 'deal' }" :data-card-dealing="Boolean(cardMotion('single:player', index))" :data-card-motion="cardMotion('single:player', index)?.kind" :style="{ '--deal-delay': `${cardMotion('single:player', index)?.delay ?? 0}ms` }" :src="cardImageSrc(card)" :alt="card" class="playing-card">
                <span v-if="!singleGame?.player_hand.length" key="player-placeholder" class="card-placeholder">你的手牌</span>
              </TransitionGroup>
              <span class="seat-bet"><i aria-hidden="true"></i>{{ singleGame?.bet_amount ?? 0 }}</span>
              <div class="player-info-tag" :class="{ 'turn-active': canSingleOperate }">
                <img :src="profile?.avatar_url || withAssetVersion('/character/normal.webp')" :alt="`${profile?.username || '玩家'}头像`" class="seat-player-avatar">
                <div class="seat-player-info"><h2>{{ profile?.username || '玩家' }} <span class="viewer-label">你</span></h2><p>持有 {{ profile?.balance ?? 0 }} 灵石</p></div>
              </div>
            </section>
            <div v-if="resultVisible && blackjackResult" class="blackjack-result"><RoundFeedback :key="resultKey" aria-label="本局结果" :title="blackjackResult.title" :subtitle="blackjackResult.subtitle" :detail="blackjackResult.detail" :tone="blackjackResult.tone" :animated="resultAnimated" /></div>
            <aside v-if="dealerSpeech && !blackjackResult" class="table-dealer-speech" aria-label="荷官对话">{{ dealerSpeech }}</aside>
          </div>
        </div>

        <footer class="action-dock single-action-zone" :class="{ 'betting-dock': !canSingleOperate }" aria-label="单人游戏操作">
          <p class="balance-text">余额：{{ profile?.balance ?? 0 }}<span v-if="singleGame"> · 本局下注：{{ singleGame.bet_amount }}</span></p>
          <div v-if="canSingleOperate" class="action-row">
            <button class="primary-btn game-button gold" @click="singleHit" :disabled="requestInFlight">要牌</button>
            <button class="game-button" @click="singleStand" :disabled="requestInFlight">停牌</button>
            <button class="game-button" @click="singleDouble" :disabled="requestInFlight || !canSingleDouble">双倍下注</button>
          </div>
          <template v-else>
            <div class="action-row bet-entry">
              <label class="sr-only" for="single-bet-input">单人下注金额</label>
              <input id="single-bet-input" v-model.number="singleBetInput" type="number" min="1" step="1" inputmode="numeric" placeholder="输入赌注" :disabled="requestInFlight">
              <button class="primary-btn game-button gold" :disabled="requestInFlight || !canSingleStart" @click="startSingleGame">{{ singleGame ? '再来一局' : '开始对战' }}</button>
            </div>
            <div class="action-row quick-bets" aria-label="快捷下注">
              <button class="game-button" v-for="option in singleBetOptions" :key="`single-${option.key}`" :disabled="requestInFlight" @click="applySingleBetOption(option.value)">{{ option.label }} <span>{{ option.value }}</span></button>
            </div>
          </template>
        </footer>
      </section>
      </GameViewport>

      <section v-else-if="viewMode === 'lobby'" class="lobby-panel room-lobby-panel">
        <div class="room-lobby-art"><GameIcon name="room" /><span>好友相聚，好牌开场</span></div>
        <div class="room-lobby-content">
        <div class="lobby-section-heading"><h3>多人房间大厅</h3><span>21点 · 最多3人</span></div>
        <button class="game-button" @click="openRoomDirectory('blackjack')">房间列表</button>

        <template v-if="isDiscordMode">
          <p class="hint-text">已连接 Discord 活动，可重连当前会话房间，或直接输入房间号加入。</p>
          <div class="lobby-actions">
            <button class="game-button gold" :disabled="requestInFlight" @click="autoJoinCurrentSession(true)">
              连接当前会话
            </button>
            <div class="join-group">
              <input
                v-model="roomInput"
                maxlength="16"
                placeholder="输入房间号"
                :disabled="requestInFlight"
              />
              <button class="game-button gold" :disabled="requestInFlight" @click="joinRoom">
                按房间号加入
              </button>
            </div>
            <button class="game-button quiet" :disabled="requestInFlight" @click="enterBlackjackModeSelect">
              返回模式选择
            </button>
          </div>
        </template>

        <template v-else>
          <div class="lobby-actions">
            <button class="game-button gold" :disabled="requestInFlight" @click="createRoom">
              创建房间
            </button>
            <div class="join-group">
              <input
                v-model="roomInput"
                maxlength="16"
                placeholder="输入房间号"
                :disabled="requestInFlight"
              />
              <button class="game-button gold" :disabled="requestInFlight" @click="joinRoom">
                加入房间
              </button>
            </div>
            <button class="game-button quiet" :disabled="requestInFlight" @click="enterBlackjackModeSelect">
              返回模式选择
            </button>
          </div>

          <p class="hint-text">创建房间后，将房间号分享给好友，即可一起入座。</p>
        </template>
        <div class="dealer-dialogue">{{ dealerSpeech }}</div>
        </div>
      </section>

      <GameViewport v-else-if="viewMode === 'table' && roomState">
      <section class="blackjack-table multi-mode-view" :class="{ 'has-round-result': resultVisible && blackjackResult }" :data-dealing="hasDealingCards()" aria-label="多人21点牌桌">
        <div class="table-toolbar">
          <div class="table-heading">
            <strong>房间 {{ roomState.room_id }}</strong>
            <span>{{ roomStateText }} · 房主：{{ hostDisplayName }}</span>
          </div>
          <div class="toolbar-actions">
            <button class="game-button" @click="blackjackRulesDialog?.showModal()">玩法规则</button>
            <CopyRoomCode :room-id="roomState.room_id" />
            <GameTools v-if="profile" :profile="profile" :api-call="apiCall" game-type="blackjack" />
            <button class="game-button" :disabled="requestInFlight" @click="refreshRoom(true)">同步</button>
            <button class="game-button" :disabled="requestInFlight" @click="recruitTeammates">招募队友</button>
            <button class="game-button" :disabled="requestInFlight" @click="leaveRoom">离开房间</button>
          </div>
        </div>

        <div class="table-scroll" tabindex="0" aria-label="多人牌桌">
          <div class="casino-stage multiplayer-board">
            <img class="felt-table" src="/ui/guochao/table-felt.svg" alt="" aria-hidden="true">
            <section class="hand-area dealer-seat table-dealer-cards">
              <BustBurst v-if="bustBursts['multi:dealer']" :key="bustBursts['multi:dealer']" />
              <div class="dealer-identity">
                <img :src="dealerAvatarSrc" alt="荷官月月" class="table-dealer-image">
                <div><span class="seat-role">荷官</span><h2>月月 <span class="score-badge">{{ dealer?.score ?? 0 }}</span></h2></div>
              </div>
              <TransitionGroup name="card" tag="div" class="card-hand dealer-hand" :style="cardHandStyle(dealer?.hand)" :data-dealing="hasDealingCards('multi:dealer')">
                <img v-for="(card, index) in dealer?.hand || []" :key="`${cardRoundKey}-multi-dealer-${index}`" :class="{ 'card-dealing': cardMotion('multi:dealer', index)?.kind === 'deal', 'card-flipping': cardMotion('multi:dealer', index)?.kind === 'flip' }" :data-card-dealing="Boolean(cardMotion('multi:dealer', index))" :data-card-motion="cardMotion('multi:dealer', index)?.kind" :style="{ '--deal-delay': `${cardMotion('multi:dealer', index)?.delay ?? 0}ms` }" :src="cardImageSrc(card)" :alt="card === 'Hidden' ? '暗牌' : card" class="playing-card">
                <span v-if="!dealer?.hand.length" key="dealer-placeholder" class="card-placeholder">待发牌</span>
              </TransitionGroup>
            </section>
            <div class="player-seats">
              <section v-for="{ seatIndex, position } in displaySeats" :key="seatIndex" class="seat-area" :class="[`seat-${position}`, { 'empty-seat-area': !seatPlayerMap[seatIndex], 'turn-active': seatPlayerMap[seatIndex]?.is_current_turn, 'viewer-seat': String(seatPlayerMap[seatIndex]?.user_id) === String(viewerUserId) }]">
                <template v-if="seatPlayerMap[seatIndex]">
                  <div v-if="String(seatPlayerMap[seatIndex]?.user_id) === viewerUserId" class="own-score" aria-label="你的点数" :data-score="ownScore" :data-bust="ownScore > 21" :data-twenty-one="ownScore === 21"><span>{{ ownScoreLabel }}</span><strong>{{ ownHandCount ? ownScore : '—' }}</strong></div>
                  <BustBurst v-if="bustBursts[`multi:${seatPlayerMap[seatIndex]?.user_id}`]" :key="bustBursts[`multi:${seatPlayerMap[seatIndex]?.user_id}`]" class="player-bust-burst" />
                  <div class="player-info-tag">
                    <img class="seat-player-avatar" :src="playerAvatarSrc(seatPlayerMap[seatIndex]!)" :alt="`${seatPlayerMap[seatIndex]?.username}头像`">
                    <div class="seat-player-info">
                      <h2>{{ seatPlayerMap[seatIndex]?.username }} <span v-if="String(seatPlayerMap[seatIndex]?.user_id) === String(viewerUserId)" class="viewer-label">你</span><span v-if="seatPlayerMap[seatIndex]?.is_bot" class="viewer-label">AI</span></h2>
                      <p class="player-status"><span class="score-badge">{{ seatPlayerMap[seatIndex]?.score ?? 0 }}</span> {{ getPlayerStatusText(seatPlayerMap[seatIndex]!) }}<span v-if="seatPlayerMap[seatIndex]?.result"> · {{ getPlayerResultText(seatPlayerMap[seatIndex]!) }}</span></p>
                    </div>
                  </div>
                  <span class="seat-bet"><i aria-hidden="true"></i>{{ seatPlayerMap[seatIndex]?.bet_amount ?? 0 }}</span>
                  <TransitionGroup name="card" tag="div" class="card-hand player-hand" :style="cardHandStyle(seatPlayerMap[seatIndex]?.hand)" :data-dealing="hasDealingCards(`multi:${seatPlayerMap[seatIndex]?.user_id}`)">
                    <img v-for="(card, index) in seatPlayerMap[seatIndex]?.hand || []" :key="`${cardRoundKey}-p${seatIndex}-${index}`" :class="{ 'card-dealing': cardMotion(`multi:${seatPlayerMap[seatIndex]?.user_id}`, index)?.kind === 'deal' }" :data-card-dealing="Boolean(cardMotion(`multi:${seatPlayerMap[seatIndex]?.user_id}`, index))" :data-card-motion="cardMotion(`multi:${seatPlayerMap[seatIndex]?.user_id}`, index)?.kind" :style="{ '--deal-delay': `${cardMotion(`multi:${seatPlayerMap[seatIndex]?.user_id}`, index)?.delay ?? 0}ms` }" :src="cardImageSrc(card)" :alt="card" class="playing-card">
                    <span v-if="!seatPlayerMap[seatIndex]?.hand.length" :key="`empty-hand-${seatIndex}`" class="card-placeholder">待发牌</span>
                  </TransitionGroup>
                </template>
                <div v-else class="empty-seat-label"><span class="empty-seat-icon" aria-hidden="true">＋</span><p>等待入座</p></div>
              </section>
            </div>
            <div v-if="resultVisible && blackjackResult" class="blackjack-result"><RoundFeedback :key="resultKey" aria-label="本局结果" :title="blackjackResult.title" :subtitle="blackjackResult.subtitle" :detail="blackjackResult.detail" :tone="blackjackResult.tone" :animated="resultAnimated" /></div>
            <aside v-if="dealerSpeech && !blackjackResult" class="table-dealer-speech" aria-label="荷官对话">{{ dealerSpeech }}</aside>
          </div>
        </div>

        <footer class="action-dock" :class="{ 'betting-dock': isRoomBettingStage }" aria-label="多人游戏操作">
          <p class="balance-text">余额：{{ profile?.balance ?? 0 }} · 你的下注：{{ viewerPlayer?.bet_amount ?? 0 }}<span v-if="roomState.state === 'waiting'"> · 已准备 {{ roomState.ready_player_count }}/{{ players.length }}</span></p>
          <template v-if="isRoomBettingStage">
            <div class="action-row multi-waiting-controls">
              <label class="sr-only" for="multi-bet-input">多人下注金额</label>
              <input id="multi-bet-input" v-model.number="betInput" type="number" min="1" step="1" inputmode="numeric" placeholder="输入下注" :disabled="requestInFlight || !canSetBet">
              <button class="game-button" :disabled="requestInFlight || !canSetBet" @click="setBet">下注</button>
              <button class="game-button" :disabled="requestInFlight || !canToggleReady" @click="toggleReady">{{ readyButtonText }}</button>
              <button class="game-button" v-if="roomState.state === 'finished'" :disabled="requestInFlight" @click="continueReady">沿用上局并准备</button>
              <button v-if="isHost" class="primary-btn game-button gold" :disabled="requestInFlight || !canStartRound" @click="startRound">开始本局</button>
              <button class="game-button" v-if="isHost" :disabled="requestInFlight || (!players.some(player => player.is_bot) && players.length >= roomState.max_players)" @click="toggleRoomBot">{{ players.some(player => player.is_bot) ? '移除月月陪玩' : '添加月月陪玩' }}</button>
            </div>
            <div v-if="!viewerPlayer?.is_ready && viewerPlayer" class="action-row quick-bets" aria-label="快捷下注">
              <button class="game-button" v-for="option in singleBetOptions" :key="`multi-${option.key}`" :disabled="requestInFlight || !canSetBet" @click="betInput = option.value">{{ option.label }} <span>{{ option.value }}</span></button>
            </div>
          </template>
          <div v-else-if="roomState.state === 'playing'" class="action-row multi-playing-controls">
            <span class="turn-hint">{{ isMyTurn ? '轮到你了' : '等待其他玩家操作' }}</span>
            <button class="primary-btn game-button gold" :disabled="requestInFlight || !isMyTurn" @click="hit">要牌</button>
            <button class="game-button" :disabled="requestInFlight || !isMyTurn" @click="stand">停牌</button>
          </div>
          <p v-else class="turn-hint">月月正在结算本局…</p>
        </footer>
      </section>
      </GameViewport>

      <div
        v-if="viewMode === 'game_hub' || viewMode === 'blackjack_mode_select'"
        class="home-dealer-section dealer-section"
      >
        <img
          :src="withAssetVersion('/character/normal.webp')"
          alt="看板娘"
          class="dealer-image"
        />
        <div v-if="dealerSpeech" class="dialogue-box">
          <p>{{ dealerSpeech }}</p>
        </div>
      </div>

      <GameStatsPanel v-if="lobbyStatsPanel && profile" :profile="profile" :api-call="apiCall" :initial-tab="lobbyStatsPanel" @close="lobbyStatsPanel = null" />
      <div v-if="statusMessage" class="status-message" role="status">{{ statusMessage }}</div>
      <div v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</div>
      <dialog ref="blackjackRulesDialog" class="blackjack-rules" aria-labelledby="blackjack-rules-title">
        <div class="rules-heading"><h2 id="blackjack-rules-title">21点玩法规则</h2><button class="game-button" aria-label="关闭21点规则" @click="blackjackRulesDialog?.close()">关闭</button></div>
        <h3>点数与目标</h3>
        <p>2–10 按牌面计分，J / Q / K 为 10，A 优先计 11，超出 21 时改计 1。目标是在不爆牌的前提下比荷官更接近 21。超过 21 立即失败。</p>
        <h3>天然 Blackjack 与派彩</h3>
        <p>首发两张组成 A 加 10 点牌为天然 Blackjack，优先于补牌形成的 21 点。双方天然 Blackjack 为平局。天然 Blackjack 获胜净赢下注的 1.5 倍（连本金返还 2.5 倍，灵石不足整数部分向下取整）；普通获胜净赢 1 倍（返还 2 倍）；平局退还本金，失败损失本局下注。</p>
        <h3>单人操作</h3>
        <p>下注后可要牌或停牌。仅首两张且余额充足时可双倍下注：追加同额赌注，只再发一张并自动停牌。放弃按本局失败处理。单人荷官在小于 17 或软 17（A 仍计 11）时要牌。本活动暂不提供分牌、保险或投降退半。</p>
        <h3>多人同桌</h3>
        <p>最多 3 名玩家各自与荷官比牌，彼此不争夺底池。每人下注并准备后由房主开局，按座位轮流要牌或停牌。多人暂不支持双倍下注；荷官不足 17 时要牌，达到 17（含软 17）停牌。真人回合超过 60 秒自动停牌，网络恢复后会同步当前牌局。</p>
        <h3>月月陪玩与离桌</h3>
        <p>房主可在等待或结算完成后添加月月陪玩，占用一个普通玩家座位。陪玩下注跟随房主、自动准备，低于 17 要牌，否则停牌；荷官仍独立结算。等待开局时离桌退回已下注灵石；对局进行中离桌视为放弃，没收本局下注。结算完成后退出不会再次扣款。</p>
      </dialog>
    </template>
    <RoomDirectory v-if="showRoomDirectory && profile" :api-call="apiCall" :balance="profile.balance" :game-type="directoryGameType" :join-room="joinListedRoom" @close="showRoomDirectory = false" />
    <RoomInvite v-if="inviteTarget" :room="inviteTarget" :client-id="runtimeDiscordClientId" :share="isDiscordMode ? shareRoom : undefined" @close="inviteTarget = null" />
    <LandscapeNotice />
  </div>
</template>

<style scoped>
.multi-root {
  min-height: 100vh;
  min-height: 100dvh;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: #fff1ce;
  background: linear-gradient(180deg, #381e360a, #391c395c), #b6684c url("/ui/guochao/teahouse-room.webp") center / cover fixed;
}

/* 横屏桌面按剩余高度分配牌桌空间，操作区不随牌桌滚动。 */
.multi-root.table-fullscreen {
  position: relative;
  height: 100vh;
  height: 100dvh;
  min-height: 0;
  padding: 0;
  gap: 0;
  overflow: hidden;
}
.multi-root.table-fullscreen:has(.blackjack-table):has(> .status-message, > .error-message) { padding-bottom: 24px; }

.blackjack-table {
  position: relative;
  display: grid;
  grid-template-rows: 50px minmax(0, 1fr);
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 0;
}

.table-toolbar,
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.table-toolbar {
  padding: 4px max(14px, env(safe-area-inset-right)) 4px max(14px, env(safe-area-inset-left));
  border-bottom: 2px solid #efd293;
  background: linear-gradient(180deg, #ad493b, #732f36);
  box-shadow: 0 3px 12px #41244559, inset 0 1px #fff0b980;
  overflow: hidden;
}

.table-heading {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  text-align: left;
}
.table-heading strong { color: #ffe7a7; font-size: 15px; white-space: nowrap; text-shadow: 0 2px #593054; }
.table-heading > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #ffe1c8; font-size: 11px; }
.toolbar-actions,
.action-row { display: flex; align-items: center; justify-content: flex-end; gap: 6px; min-width: 0; }
.table-toolbar .toolbar-actions { flex-shrink: 0; }

.table-scroll {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  container-type: size;
  padding: 0;
  background: linear-gradient(180deg, #33224205, #34274c30), #bc7657 url("/ui/guochao/teahouse-room.webp") center / cover;
}

.casino-stage {
  --card-width: clamp(26px, 14.5cqh, 88px);
  --avatar-size: clamp(25px, 11cqh, 42px);
  position: relative;
  isolation: isolate;
  width: 100%;
  height: 100%;
  min-height: 0;
  margin: 0 auto;
}

.felt-table {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: fill;
  pointer-events: none;
  user-select: none;
  filter: none;
}

.hand-area,
.seat-area { min-width: 0; }
.dealer-seat {
  position: absolute;
  top: 3%;
  left: 50%;
  transform: translateX(-50%);
  width: 34%;
  z-index: 2;
}
.dealer-identity {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: clamp(30px, 13cqh, 66px);
  text-align: left;
}
.table-dealer-image {
  width: clamp(28px, 12cqh, 58px);
  height: clamp(30px, 13cqh, 66px);
  object-fit: cover;
  object-position: center 22%;
  border-radius: 9px 9px 5px 5px;
  border: 2px solid #f2cb7f;
  background: linear-gradient(145deg, #7298c3, #3d498b);
  box-shadow: 0 2px 0 #936434, 0 3px 8px #31255766, inset 0 0 0 1px #ffefb3;
}
.seat-role { color: #ffe3a0; font-size: 9px; letter-spacing: 2px; text-shadow: 0 1px 3px #2c245c; }
.hand-area h2,
.seat-player-info h2 { margin: 1px 0 0; font-size: 13px; font-weight: 600; color: #fff4d8; text-shadow: 0 1px 2px #2b245c; }
.score-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  padding: 1px 5px;
  border: 1px solid #f4ce87;
  border-radius: 5px;
  background: linear-gradient(180deg, #5c73b6, #343c80);
  color: #fff2bb;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.card-hand {
  --hand-slots: 1;
  container-type: inline-size;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  min-height: calc(var(--card-width) * 1.38 + 4px);
  padding: 2px 3px;
  overflow: visible;
}
.playing-card {
  --fitted-card-width: min(var(--card-width), calc(100cqw / var(--hand-slots)));
  width: var(--fitted-card-width);
  height: calc(var(--fitted-card-width) * 1.38);
  flex: 0 0 var(--fitted-card-width);
  object-fit: contain;
  border-radius: 4px;
  box-shadow: 0 3px 6px #0007;
  transition: transform .2s, opacity .2s;
}
.playing-card + .playing-card { margin-left: calc(var(--fitted-card-width) * -.64); }
.playing-card[alt="暗牌"] { object-fit: fill; }
.playing-card.card-dealing { animation: blackjack-deal .44s var(--deal-delay, 0ms) cubic-bezier(.18,.75,.28,1) both; }
.playing-card.card-flipping { animation: blackjack-flip .52s ease-in-out both; }
.dealer-seat { --deal-y: -35px; --deal-x: 0px; }
.seat-bottom { --deal-y: -75px; --deal-x: 0px; }
.seat-left { --deal-y: -40px; --deal-x: 55px; }
.seat-right { --deal-y: -40px; --deal-x: -55px; }
@keyframes blackjack-deal {
  from { opacity: 0; transform: translate(var(--deal-x, 0px), var(--deal-y, -50px)) rotate(-12deg) scale(.72); }
  65% { opacity: 1; }
  to { opacity: 1; transform: translate(0, 0) rotate(0) scale(1); }
}
@keyframes blackjack-flip {
  0% { transform: perspective(500px) rotateY(-90deg); filter: brightness(.65); }
  60% { transform: perspective(500px) rotateY(12deg); filter: brightness(1.08); }
  100% { transform: perspective(500px) rotateY(0); filter: brightness(1); }
}
.card-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: var(--card-width);
  height: calc(var(--card-width) * 1.38);
  border: 1px dashed #f1ce8c96;
  border-radius: 4px;
  color: #ead8bd;
  background: #383d772b;
  font-size: 9px;
  white-space: nowrap;
}

.table-center-mark {
  position: absolute;
  left: 50%;
  top: 45%;
  transform: translate(-50%, -50%);
  width: 29%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  text-align: center;
  pointer-events: none;
}
.table-brand { color: #f4d5a9; font-size: 9px; letter-spacing: 4px; text-shadow: 0 1px 3px #39285b; }
.table-game-name { color: #ffe19c; font-family: "STKaiti", "KaiTi", serif; font-size: clamp(16px, 6cqh, 34px); letter-spacing: 5px; font-weight: 800; text-shadow: -1px -1px #393267, 1px -1px #393267, -1px 1px #393267, 1px 2px #393267, 0 3px 0 #a47654; }
.table-chip-stack { position: relative; width: 58px; height: 24px; margin-top: 3px; }
.table-chip-stack i,
.seat-bet i {
  display: block;
  width: 22px;
  height: 22px;
  background: url('/table-assets/chip-red.png') center / contain no-repeat;
  filter: drop-shadow(0 2px 2px #0005);
}
.table-chip-stack i { position: absolute; top: 3px; left: 3px; transform: scaleY(.68); }
.table-chip-stack i:nth-child(2) { top: 0; left: 17px; }
.table-chip-stack i:nth-child(3) { top: 5px; left: 33px; }
.center-caption { margin: 0; color: #fff0d1; font-size: 11px; line-height: 1.4; text-shadow: 0 1px 3px #3a285b; }
.round-result { margin: 0; color: #ffe5a2; font-size: 18px; font-weight: 700; }

.player-seats { position: absolute; inset: 0; pointer-events: none; }
.seat-area,
.single-player-seat { position: absolute; display: flex; flex-direction: column; align-items: center; width: 24%; z-index: 3; pointer-events: auto; }
.seat-left,
.seat-right { top: 5%; }
.seat-left { left: 6%; }
.seat-right { right: 6%; }
/* 自己的身份卡固定左下，大手牌独立居中，保持多人围坐视角。 */
.seat-bottom { --card-width: clamp(32px, 19cqh, 120px); display: block; width: 100%; left: 0; bottom: 5px; }
.seat-bottom .player-info-tag { position: absolute; left: max(12px, env(safe-area-inset-left)); bottom: 5px; max-width: 24%; }
.seat-bottom .seat-bet { position: absolute; left: 69%; bottom: 6px; }
.seat-bottom .player-hand { width: 42%; margin-inline: auto; }
.own-score {
  position: absolute;
  left: 74%;
  bottom: 4px;
  display: grid;
  justify-items: center;
  gap: 2px;
  min-width: 68px;
  padding: 7px 12px;
  border: 2px solid #f2cf86;
  border-radius: 10px;
  background: url("/ui/guochao/cloud-pattern.svg") center / 100px, linear-gradient(150deg, #6884c9, #3e3d83);
  box-shadow: inset 0 0 0 1px #fbeba24d, 0 3px 0 #9e6e43, 0 5px 12px #31204c73;
  color: #fff2ba;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.own-score > span { font-size: 10px; color: #fff0d6; }
.own-score > strong { font-size: 32px; line-height: 1; text-shadow: 0 2px #363368; }
.own-score[data-bust="true"] { color: #fff0d8; border-color: #ffbd80; background: linear-gradient(150deg, #df6952, #932f4b); }
.own-score[data-twenty-one="true"] { color: #fff6ad; border-color: #fff0a1; box-shadow: inset 0 0 0 1px #f2ce80, 0 3px 0 #a7743c, 0 0 18px #ffd35d8c; }
.blackjack-result { position: absolute; z-index: 4; left: 50%; top: 45%; transform: translate(-50%, -50%); width: min(360px, 38%); pointer-events: none; }
.seat-bottom .player-bust-burst { top: 50%; }

.player-info-tag {
  display: flex;
  align-items: center;
  gap: 7px;
  width: fit-content;
  max-width: 100%;
  padding: 5px 9px 5px 5px;
  margin: 0;
  border: 2px solid #eec582;
  border-radius: 10px 10px 6px 6px;
  background: linear-gradient(160deg, #6584bc, #3e427f);
  box-shadow: inset 0 1px #d5e4ff66, 0 3px 0 #92613d, 0 5px 8px #39294b55;
  text-align: left;
}
.turn-active .player-info-tag,
.player-info-tag.turn-active { border-color: #ffe695; box-shadow: inset 0 1px #ffffdd80, 0 0 0 2px #e6a44c6b, 0 0 18px #ffcf6659; }
.viewer-seat .player-info-tag { border-color: #ffe09b; }
.seat-player-avatar { width: var(--avatar-size); height: var(--avatar-size); flex: 0 0 var(--avatar-size); border: 1px solid #ffe6a4; border-radius: 6px; object-fit: cover; object-position: center 22%; background: #677bba; }
.seat-player-info { min-width: 0; }
.seat-player-info h2 { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.seat-player-info p { margin: 2px 0 0; font-size: 10px; white-space: nowrap; color: #ffedc5; }
.viewer-label { margin-left: 3px; font-size: 9px; color: #ffde89; }
.seat-bet { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-height: 19px; margin: 2px 0; color: #fff0c8; font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; text-shadow: 0 1px 3px #392455; }
.seat-bet i { width: 14px; height: 14px; }
.empty-seat-area { opacity: .85; }
.empty-seat-label { text-align: center; margin: 0; }
.empty-seat-label p { margin: 4px 0 0; font-size: 10px; color: #fff0d1; text-shadow: 0 1px 3px #392455; }
.empty-seat-icon { display: inline-flex; align-items: center; justify-content: center; width: 35px; height: 35px; border: 1px dashed #f4d492; border-radius: 10px; color: #ffe7b2; background: linear-gradient(160deg, #778dbecc, #49457bcc); font-size: 20px; box-shadow: 0 2px 0 #80554180; }
.table-dealer-speech { position: absolute; left: 36%; top: 29%; width: 28%; margin: 0; color: #ffeac4; font-size: 11px; line-height: 1.5; text-align: left; text-shadow: 0 1px 3px #392455; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }

/* 操作区与本人手牌共用中央轴线。 */
.action-dock {
  position: absolute;
  z-index: 5;
  left: 50%;
  transform: translateX(-50%);
  bottom: 26%;
  width: min(950px, calc(100% - 24px));
  display: grid;
  grid-template-columns: minmax(130px, 1fr) auto;
  align-items: center;
  column-gap: 12px;
  row-gap: 5px;
  min-width: 0;
  padding: 5px;
  border: 0;
  background: transparent;
  overflow: auto;
  max-height: 48cqh;
  scroll-padding: 5px;
}
.balance-text,
.turn-hint { margin: 0; font-size: 12px; line-height: 1.5; color: #fff0d0; text-shadow: 0 1px 3px #35214f; }
.balance-text { grid-column: 1; text-align: left; }
.betting-dock .balance-text { grid-row: 2; }
.single-action-zone.betting-dock .balance-text { grid-row: 1 / span 2; }
.action-dock > .action-row { grid-column: 2; }
/* 单人出牌操作与居中的手牌对齐，两侧等宽留给余额信息。 */
.single-action-zone:not(.betting-dock) {
  left: 50%;
  bottom: 26%;
  width: min(950px, calc(100% - 24px));
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
}
.action-dock > .multi-waiting-controls { grid-column: 1 / -1; grid-row: 1; }
.action-dock > .turn-hint { grid-column: 2; text-align: right; }
.action-row input { width: 90px; max-width: 100%; }
.quick-bets { grid-row: 2; }
.quick-bets button { flex: 0 0 auto; }
.quick-bets span { margin-left: 3px; color: #fff1c9; font-variant-numeric: tabular-nums; }
.action-dock .quick-bets button { padding-inline: 7px; }

.multi-root input,
.multi-root button:not(.game-button, .game-card, .profile-chip) {
  min-width: 0;
  min-height: 40px;
  max-width: 100%;
  margin: 0;
  border: 2px solid #e4b365;
  border-radius: 9px;
  background: #fff0cb;
  color: #533f54;
  padding: 7px 12px;
  font: inherit;
  font-size: 13px;
  letter-spacing: 0;
  touch-action: manipulation;
  white-space: nowrap;
}
.multi-root input { background: linear-gradient(180deg, #ffedc2, #fff8e8); color-scheme: light; box-shadow: inset 0 2px 3px #a86b2926; }
.multi-root input::placeholder { color: #957354; }
.multi-root button { cursor: pointer; }
.multi-root button:not(.game-button, .game-card, .profile-chip):hover { border-color: #fff0b2; background: #ffedbc; }
.multi-root button:disabled { cursor: not-allowed; opacity: .45; }
.multi-root :focus-visible { outline: 2px solid #fff0a8; outline-offset: 2px; }
.multi-root .primary-btn:not(.game-button) { background: linear-gradient(180deg, #ffcf55, #ef8141 55%, #d64e35); border-color: #ffe3a0; color: #fff5d0; font-weight: 700; }
.multi-root .primary-btn:not(.game-button):hover { background: #ff9c4c; }

.top-bar { max-width: 1120px; width: 100%; margin-inline: auto; flex-shrink: 0; }
.title-group { text-align: left; text-shadow: 0 1px 3px #66353b; }
.lobby-eyebrow { display: block; margin-bottom: 6px; color: #ffe8ab; font-size: 9px; font-weight: 600; letter-spacing: 4px; }
.title-group h1 { margin: 0; font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-size: 26px; font-weight: 800; color: #fff1b5; letter-spacing: 2px; text-shadow: -1px -1px #794149, 1px -1px #794149, -1px 1px #794149, 1px 2px #794149, 0 3px #b77848; }
.title-group p { margin: 6px 0 0; font-size: 12px; color: #fff2d8; }
.multi-root .profile-chip { display: flex; align-items: center; gap: 10px; background: url("/ui/guochao/cloud-pattern.svg") center / 110px, linear-gradient(145deg, #677caf, #404075); border: 2px solid #f5d088; border-radius: 13px 13px 7px 7px; padding: 9px 14px 9px 9px; min-width: 0; max-width: 42%; box-shadow: inset 0 1px #d3e3ff66, 0 3px 0 #975d3e, 0 5px 15px #5135404d; }
.profile-avatar { width: 39px; height: 39px; border-radius: 8px; object-fit: cover; object-position: center 22%; border: 1px solid #ffe5a8; background: #7988ba; }
.profile-meta { min-width: 0; text-align: left; }
.profile-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; color: #fff1cd; }
.profile-balance { margin-top: 5px; color: #ffe3a2; font-size: 11px; font-variant-numeric: tabular-nums; }
.panel { background: linear-gradient(160deg, #fff2d6, #edcc9d); color: #544263; border: 2px solid #bd884b; border-radius: 15px; padding: 18px; box-shadow: inset 0 0 0 2px #fff7d6, 0 6px 18px #6542444d; }
.loading-panel { margin: auto; max-width: 100%; }
.lobby-panel { max-width: 1120px; margin: 22px auto 0; width: 100%; }
.lobby-section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 0 0 16px; }
.lobby-section-heading h3 { margin: 0; color: #fff1bd; font-size: 17px; font-weight: 700; letter-spacing: 1px; text-shadow: 0 1px #673951, 0 2px 4px #573141; }
.lobby-section-heading > span { color: #fff0d4; font-size: 11px; text-shadow: 0 1px 3px #673951; }
.lobby-panel .toolbar-actions { margin-top: 18px; }
.lobby-actions { display: flex; flex-direction: column; gap: 11px; }
.game-grid { display: grid; gap: 14px; }
.hub-game-grid { grid-template-columns: repeat(var(--hub-columns, 6), minmax(0, 1fr)); }
.multi-root .game-card {
  --card-accent: #ffde93;
  --card-shade: #ac4e4b;
  --card-depth: #743347;
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 0;
  min-height: 0;
  gap: 0;
  padding: 12px 12px 20px;
  overflow: hidden;
  border: 3px solid var(--card-accent);
  border-radius: 24px 24px 18px 18px;
  background: url("/ui/guochao/cloud-pattern.svg") center / 130px, radial-gradient(ellipse at 50% 28%, #ffe7bd47, transparent 65%), linear-gradient(160deg, var(--card-shade), var(--card-depth));
  box-shadow: inset 0 0 0 2px #9b663e, inset 0 0 0 4px #f9d589b8, 0 4px 0 #945837, 0 8px 16px #61374866;
  font: inherit;
  text-align: center;
  white-space: normal;
  transition: border-color .18s, filter .18s;
}
.multi-root .game-card::before { content: ''; position: absolute; inset: 8px; z-index: -1; border: 1px solid #ffe4a866; border-radius: 17px 17px 11px 11px; background: linear-gradient(135deg, #ffe0a6 0 6px, transparent 6px) top left / 15px 15px no-repeat, linear-gradient(225deg, #ffe0a6 0 6px, transparent 6px) top right / 15px 15px no-repeat, linear-gradient(45deg, #ffe0a6 0 6px, transparent 6px) bottom left / 15px 15px no-repeat, linear-gradient(315deg, #ffe0a6 0 6px, transparent 6px) bottom right / 15px 15px no-repeat; }
.multi-root .game-card::after { content: ''; position: absolute; width: 68%; height: 8px; left: 16%; bottom: 4px; z-index: -1; border-top: 1px solid #ffe3a694; border-bottom: 1px solid #ffe3a640; border-radius: 50%; }
.multi-root .game-card:hover { border-color: #fff0b8; background: url("/ui/guochao/cloud-pattern.svg") center / 130px, radial-gradient(ellipse at 50% 28%, #ffe7bd59, transparent 65%), linear-gradient(160deg, var(--card-shade), var(--card-depth)); filter: brightness(1.07); }
.multi-root .blackjack-card { --card-shade: #bb5956; --card-depth: #71324d; }
.multi-root .texas-card { --card-shade: #577fb2; --card-depth: #3d3e80; }
.multi-root .landlord-card { --card-shade: #d98545; --card-depth: #a7463e; }
.multi-root .mahjong-card { --card-shade: #58a3a7; --card-depth: #3b577e; }
.multi-root .golden_flower-card { --card-shade: #aa7098; --card-depth: #633d76; }
.multi-root .leaderboard-card { --card-shade: #b99349; --card-depth: #765047; }
.lobby-profile-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; min-width: 0; max-width: 50%; }
.lobby-profile-actions .profile-chip { max-width: 100%; font: inherit; cursor: pointer; }
.profile-chip:focus-visible { outline: 3px solid #fff4cc; outline-offset: 4px; }
.game-card-art { display: block; width: min(100%, 170px); aspect-ratio: 4 / 3; margin: 6px auto 9px; filter: drop-shadow(0 5px 5px #39284c66); }
.hub-game-grid .game-card-art { position: relative; aspect-ratio: 1; flex: none; }
.hub-game-grid .game-card-art :deep(.game-icon) { position: absolute; inset: 0; }
.game-card-copy { display: flex; flex-direction: column; gap: 8px; }
.game-name { color: #fff0bb; font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-size: 21px; font-weight: 800; letter-spacing: 2px; text-shadow: -1px -1px #58345b, 1px -1px #58345b, -1px 1px #58345b, 1px 2px #58345b, 0 3px #9b6a42; }
.game-desc { color: #fff0d6; font-size: 11px; font-weight: 500; letter-spacing: .3px; text-shadow: 0 1px 2px #533c57; }
.card-arrow { position: absolute; right: 12px; top: 9px; color: #ffe1a0; font-size: 13px; font-weight: 400; text-shadow: 0 1px #865b38; }
.lobby-footnote { margin: 18px 0 0; color: #fff0cf; font-size: 11px; letter-spacing: .5px; text-align: center; text-shadow: 0 1px 3px #683b49; }
.lobby-footnote > span { color: #ffe195; font-size: 8px; margin-right: 7px; }
.mode-panel { max-width: 760px; }
.mode-game-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.multi-root .mode-card { display: grid; grid-template-columns: 45% 1fr; padding: 20px 28px 20px 12px; min-height: 164px; text-align: left; }
.mode-card .game-card-art { width: 100%; max-width: 156px; margin: 0; }
.mode-card .game-card-copy { gap: 10px; }
.multi-root .solo-card { --card-shade: #c9794e; --card-depth: #943f44; }
.multi-root .friends-card { --card-shade: #668cb8; --card-depth: #48447f; }
.room-lobby-panel { display: grid; grid-template-columns: minmax(130px, .7fr) minmax(0, 1.3fr); align-items: center; gap: 30px; max-width: 820px; padding: 20px 30px; border: 3px solid #f7d08d; border-radius: 22px; background: url("/ui/guochao/cloud-pattern.svg") center / 160px, linear-gradient(130deg, #fff2d5f7, #efd1a9f5); box-shadow: inset 0 0 0 2px #ba8152, inset 0 0 0 5px #fff2cb, 0 5px 0 #9a5c40, 0 12px 24px #61384b59; }
.room-lobby-art { display: flex; flex-direction: column; align-items: center; color: #986346; font-size: 12px; font-weight: 600; letter-spacing: 2px; }
.room-lobby-art > .game-icon { width: min(100%, 210px); height: auto; }
.room-lobby-content { min-width: 0; }
.room-lobby-content .lobby-section-heading { flex-wrap: wrap; gap: 6px; }
.room-lobby-content .lobby-section-heading h3 { color: #50447b; text-shadow: 0 1px #fff8dc; }
.room-lobby-content .lobby-section-heading > span { color: #9a5a43; text-shadow: none; }
.room-lobby-content .hint-text { font-size: 11px; }
.join-group { display: flex; gap: 8px; }
.join-group input { flex: 1 1 120px; width: 100%; min-width: 0; }
.hint-text { margin: 12px 0 0; font-size: 12px; line-height: 1.6; color: #756078; overflow-wrap: anywhere; }
.dealer-dialogue { margin-top: 12px; font-size: 12px; line-height: 1.5; color: #a25742; }
.home-dealer-section { display: flex; flex-direction: row-reverse; align-items: center; gap: 10px; width: 100%; max-width: 1120px; margin: auto auto 0; pointer-events: none; }
.home-dealer-section .dealer-image { width: clamp(80px, 12vw, 150px); height: auto; filter: drop-shadow(0 5px 10px #61374755); }
.home-dealer-section .dialogue-box { max-width: 330px; padding: 10px 14px; color: #74516a; background: linear-gradient(135deg, #fff4dbee, #f1d6b2ee); border: 2px solid #e7b77d; border-radius: 14px 14px 4px 14px; box-shadow: 0 3px 0 #955b4366; font-size: 12px; line-height: 1.6; }
.home-dealer-section .dialogue-box p { margin: 0; }
.status-message,
.error-message { flex-shrink: 0; border: 1px solid #f1cb89; border-radius: 8px; padding: 6px 12px; font-size: 12px; line-height: 1.4; overflow-wrap: anywhere; max-height: 16dvh; overflow: auto; }
.status-message { color: #fff0d4; background: linear-gradient(180deg, #607dad, #464580); }
.error-message { color: #fff1d5; background: linear-gradient(180deg, #c26550, #934047); border-color: #ffd9a3; }
.table-fullscreen > .status-message,
.table-fullscreen > .error-message { position: absolute; bottom: 0; left: 0; right: 0; z-index: 8; height: 24px; margin: 0; padding: 3px 12px; border-radius: 0; font-size: 11px; }
.table-fullscreen:has(> .error-message) > .status-message { visibility: hidden; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
.blackjack-rules { width: min(680px, calc(100vw - 24px)); max-height: calc(100dvh - 24px); margin: auto; padding: 18px; overflow: auto; border: 3px solid #e7bb75; border-radius: 18px; background: #fff0d4; color: #574967; text-align: left; box-shadow: inset 0 0 0 2px #fff8df, 0 7px 0 #8c573f, 0 16px 45px #341f4773; }
.blackjack-rules::backdrop { background: #35213eaf; backdrop-filter: blur(3px); }
.rules-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; position: sticky; top: -18px; padding: 6px 0; background: #fff0d4; }
.rules-heading h2 { margin: 0; font-family: "STKaiti", "KaiTi", "Microsoft YaHei", serif; font-size: 20px; color: #51447a; }
.blackjack-rules h3 { margin: 18px 0 6px; font-size: 15px; color: #a44d3d; }
.blackjack-rules p { margin: 6px 0; font-size: 13px; line-height: 1.7; }

@container game-viewport (max-height: 600px) or (max-width: 900px) {
  .blackjack-table { grid-template-rows: 44px minmax(0, 1fr); }
  .table-toolbar { gap: 8px; padding-inline: max(8px, env(safe-area-inset-left)) max(8px, env(safe-area-inset-right)); }
  .table-heading strong { font-size: 13px; }
  .table-heading > span { font-size: 10px; }
  .table-toolbar .toolbar-actions { gap: 5px; }
  .table-toolbar button,
  .action-dock button,
  .action-dock input { min-height: 36px; padding: 6px 9px; font-size: 12px; }
  .action-dock { grid-template-columns: minmax(120px, 1fr) auto; gap: 4px 8px; padding: 3px; }
  .action-row { gap: 5px; }
  .action-row input { width: 74px; }
  .balance-text,
  .turn-hint { font-size: 10px; }
  .seat-player-info h2 { font-size: 11px; }
  .seat-player-info p { font-size: 9px; }
  .player-info-tag { padding: 4px 7px 4px 4px; gap: 5px; border-radius: 6px; }
  .seat-bottom { bottom: max(3px, env(safe-area-inset-bottom)); }
  .seat-bet { min-height: 16px; margin: 1px 0; font-size: 10px; }
  .table-dealer-speech { display: none; }
  .own-score { min-width: 55px; padding: 5px 9px; border-radius: 7px; }
  .own-score > span { font-size: 9px; }
  .own-score > strong { font-size: 27px; }
}

@container game-viewport (max-height: 600px) {
  .blackjack-result { left: 15%; top: 56%; width: 166px; --result-title-size: 22px; --result-padding: 11px 15px; }
  .blackjack-result :deep(.round-feedback-subtitle) { font-size: 10px; }
  .blackjack-result :deep(.round-feedback-detail) { font-size: 10px; }
  .has-round-result .action-dock .balance-text { visibility: hidden; }
  .has-round-result .action-dock { bottom: 23%; }
}

@container game-viewport (max-width: 620px) {
  .blackjack-result { left: 18%; top: 57%; width: 142px; --result-title-size: 20px; --result-padding: 8px 13px; }
  .has-round-result .action-dock > .multi-waiting-controls { flex-wrap: nowrap; gap: 3px; }
  .has-round-result .multi-waiting-controls input { width: 47px; }
  .has-round-result .multi-waiting-controls button { padding-inline: 5px; font-size: 10px; }
}

@container game-viewport (max-height: 444px) {
  .seat-left,
  .seat-right { --card-width: clamp(25px, 12cqh, 48px); }
}

@container game-viewport (max-height: 334px) {
  .seat-left,
  .seat-right { top: 1%; }
  .seat-bottom { --card-width: clamp(30px, 18cqh, 50px); }
  .table-center-mark { top: 44%; gap: 1px; }
  .table-chip-stack,
  .table-brand { display: none; }
  .table-game-name { font-size: 15px; letter-spacing: 3px; }
  .center-caption { font-size: 10px; }
  .round-result { font-size: 14px; }
  .player-info-tag { padding: 3px 6px 3px 3px; }
  .seat-player-info h2 { font-size: 10px; }
  .seat-player-info p { font-size: 9px; }
  .score-badge { font-size: 10px; min-width: 17px; padding: 1px 3px; }
  .seat-role { font-size: 8px; }
  .dealer-identity h2 { font-size: 11px; }
  .card-placeholder { font-size: 8px; }
}

@container game-viewport (max-width: 620px) {
  .table-toolbar .toolbar-actions { gap: 4px; }
  .table-toolbar button { padding-inline: 7px; font-size: 11px; }
  .action-dock { bottom: 22%; grid-template-columns: minmax(85px, 1fr) minmax(0, auto); }
  .seat-bottom { bottom: 0; }
  .multi-waiting-controls { flex-wrap: wrap; }
  .multi-waiting-controls input { width: 60px; }
  .multi-waiting-controls button { padding-inline: 7px; font-size: 11px; }
  .balance-text { font-size: 9px; }
}

/* 放在尺寸规则之后，保证所有窗口的要牌、停牌都位于手牌正上方。 */
.action-dock:not(.betting-dock) {
  bottom: calc(clamp(32px, 19cqh, 120px) * 1.38 + 22px);
  grid-template-columns: minmax(0, 1fr);
  justify-items: center;
  overflow: visible;
}
.action-dock:not(.betting-dock) > .action-row { grid-column: 1; grid-row: 1; justify-content: center; }
.action-dock:not(.betting-dock) > .balance-text { grid-column: 1; grid-row: 2; text-align: center; }
.multi-playing-controls { position: relative; }
.multi-playing-controls > .turn-hint { position: absolute; bottom: calc(100% + 5px); left: 50%; transform: translateX(-50%); white-space: nowrap; text-align: center; }
.single-action-zone.betting-dock { grid-template-columns: minmax(0, 1fr); justify-items: center; }
.single-action-zone.betting-dock > .action-row { grid-column: 1; justify-content: center; }
.single-action-zone.betting-dock > .balance-text { grid-column: 1; grid-row: 3; text-align: center; }
/* Discord 手机横屏右侧会覆盖悬浮工具栏，下注与准备整组收进桌心。 */
.multi-mode-view .betting-dock {
  width: min(950px, calc(100% - max(16%, 96px)));
  grid-template-columns: minmax(0, 1fr);
  justify-items: center;
}
.multi-mode-view .betting-dock > .action-row { grid-column: 1; justify-content: center; }
.multi-mode-view .betting-dock > .balance-text { grid-column: 1; grid-row: 3; text-align: center; }
.multi-mode-view .betting-dock > .multi-waiting-controls { flex-wrap: wrap; }
@container game-viewport (max-height: 334px) {
  .action-dock:not(.betting-dock) { bottom: calc(clamp(30px, 18cqh, 50px) * 1.38 + 18px); }
}

@media (max-height: 600px) and (orientation: landscape) {
  .multi-root:not(.table-fullscreen) { height: 100dvh; min-height: 0; padding: 12px 20px 8px; gap: 10px; overflow: auto; }
  .top-bar { align-items: center; }
  .lobby-eyebrow { font-size: 7px; letter-spacing: 3px; margin-bottom: 3px; }
  .title-group h1 { font-size: 21px; }
  .title-group p { font-size: 10px; margin-top: 3px; }
  .multi-root .profile-chip { padding: 6px 10px 6px 6px; gap: 7px; border-radius: 9px; }
  .profile-avatar { width: 32px; height: 32px; }
  .profile-name { font-size: 11px; }
  .profile-balance { font-size: 10px; margin-top: 3px; }
  .lobby-panel { margin-top: 4px; }
  .lobby-section-heading { margin-bottom: 10px; }
  .lobby-section-heading h3 { font-size: 14px; }
  .lobby-section-heading > span { font-size: 9px; }
  .game-grid { gap: 9px; }
  .multi-root .game-card { padding: 6px 6px 12px; border-radius: 18px 18px 12px 12px; }
  .multi-root .game-card::before { inset: 6px; border-radius: 11px 11px 7px 7px; }
  .game-card-art { width: min(100%, 125px); margin: 1px auto 4px; }
  .game-card-copy { gap: 5px; }
  .game-name { font-size: 17px; letter-spacing: 1px; }
  .game-desc { font-size: 9px; letter-spacing: 0; }
  .card-arrow { right: 8px; top: 5px; font-size: 12px; }
  .lobby-footnote { margin-top: 10px; font-size: 9px; }
  .home-dealer-section { min-height: 0; flex: 1; gap: 8px; }
  .home-dealer-section .dealer-image { width: auto; height: min(15dvh, 80px); object-fit: contain; }
  .home-dealer-section .dialogue-box { font-size: 10px; padding: 6px 10px; max-width: 280px; }
  .multi-root .mode-card { min-height: 116px; padding: 8px 18px 8px 8px; }
  .mode-card .game-card-art { max-width: 135px; }
  .lobby-panel .toolbar-actions { margin-top: 12px; }
  .room-lobby-panel { gap: 20px; padding: 15px 22px; }
  .room-lobby-art > .game-icon { max-width: 155px; }
  .room-lobby-art { font-size: 10px; }
  .room-lobby-content .lobby-section-heading { margin-bottom: 9px; }
  .lobby-actions { gap: 8px; }
  .room-lobby-content .hint-text { margin-top: 8px; font-size: 10px; }
  .dealer-dialogue { font-size: 10px; margin-top: 8px; }
}

@media (max-width: 680px) and (orientation: landscape) {
  .multi-root:not(.table-fullscreen) { padding-inline: 12px; gap: 8px; }
  .hub-game-grid { gap: 7px; }
  .game-name { font-size: 15px; }
  .game-desc { font-size: 8px; }
  .game-card-art { max-width: 90px; }
  .card-arrow { display: none; }
  .room-lobby-panel { gap: 14px; padding: 13px 16px; }
  .room-lobby-art > .game-icon { max-width: 132px; }
}

@media (prefers-reduced-motion: reduce) {
  .playing-card { transition: none; animation: none !important; }
  .multi-root .game-card { transition: none; }
}
</style>
