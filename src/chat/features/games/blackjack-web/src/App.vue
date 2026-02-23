<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import dialogueConfig from "./dialogue.json";

type ViewMode =
  | "loading"
  | "game_hub"
  | "blackjack_mode_select"
  | "single"
  | "lobby"
  | "table";
type RoomStage = "waiting" | "playing" | "dealer_turn" | "finished";

type PublicConfigResponse = {
  discord_client_id?: string;
};

type ProfileResponse = {
  success: boolean;
  user_id: string;
  username: string;
  avatar_url: string;
  balance: number;
};

type PlayerState = {
  user_id: number;
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
  host_user_id: number;
  max_players: number;
  state: RoomStage;
  current_turn_user_id: number | null;
  ready_player_count: number;
  all_players_ready: boolean;
  dealer: DealerState;
  players: PlayerState[];
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
  user_id: number;
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
const loadingText = ref("初始化中...");
const statusMessage = ref("");
const errorMessage = ref("");
const roomInput = ref("");
const betInput = ref<number | null>(null);
const singleBetInput = ref<number | null>(null);
const roomState = ref<RoomState | null>(null);
const singleGame = ref<SingleGameStatePayload | null>(null);
const profile = ref<ProfileResponse | null>(null);
const requestInFlight = ref(false);
const dealerSpeech = ref("月月正在观察牌局...");
let dealerSpeechTimer: number | null = null;

const queryParams = new URLSearchParams(window.location.search);
const isEmbedded = queryParams.get("frame_id") != null;
const shouldUseDiscordAuth = ref(isEmbedded);
const runtimeDiscordClientId = ref("");
const discordSessionKey = ref("");

const devUserId = queryParams.get("dev_user_id")?.trim() ?? "";
const devUsername = queryParams.get("dev_username")?.trim() ?? "";
const devAvatarUrl = queryParams.get("dev_avatar")?.trim() ?? "";

const ASSET_VERSION =
  String(import.meta.env.VITE_ASSET_VERSION ?? "dev").trim() || "dev";

let accessToken: string | null = null;
let roomPollTimer: number | null = null;

const seatIndices = [0, 1, 2];
const seatClassByIndex: Record<number, string> = {
  0: "seat-top-left",
  1: "seat-bottom-left",
  2: "seat-bottom-right",
};

const viewerUserId = computed(() => Number(profile.value?.user_id ?? 0));
const isDiscordMode = computed(() => shouldUseDiscordAuth.value);

const hostDisplayName = computed(() => {
  if (!roomState.value) return "";
  const hostId = Number(roomState.value.host_user_id);
  return (
    roomState.value.players.find((p) => Number(p.user_id) === hostId)?.username ??
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
  return players.value.find((p) => Number(p.user_id) === uid) ?? null;
});

const isHost = computed(() => {
  if (!roomState.value) return false;
  return Number(roomState.value.host_user_id) === viewerUserId.value;
});

const isMyTurn = computed(() => Boolean(viewerPlayer.value?.is_current_turn));

const canSetBet = computed(() => {
  return roomState.value?.state === "waiting" && Boolean(viewerPlayer.value);
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
    return withAssetVersion("/cards/Background.webp");
  }
  return withAssetVersion(`/cards/${card}.webp`);
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

function getSingleActionText(gameState: SingleGameState): string {
  if (gameState === "player_turn") return "你的回合";
  if (gameState === "dealer_turn") return "结算中";
  if (gameState === "finished_blackjack") return "BlackJack";
  if (gameState === "finished_win") return "胜利";
  if (gameState === "finished_push") return "平局";
  return "失败";
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
  retries = 1,
): Promise<T> {
  let lastError: unknown = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const includeJson = method !== "GET";
      const headers = buildRequestHeaders(includeJson);

      const response = await fetch(endpoint, {
        method,
        headers,
        body: includeJson && body !== undefined ? JSON.stringify(body) : undefined,
      });

      if (!response.ok) {
        const errorData = await response
          .json()
          .catch(() => ({ detail: "请求失败，服务端返回异常响应" }));
        throw new Error(errorData.detail || "请求失败");
      }

      return (await response.json()) as T;
    } catch (error) {
      lastError = error;
      if (attempt < retries) {
        await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
      }
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
  runtimeDiscordClientId.value = String(configData.discord_client_id ?? "").trim();
}

async function setupDiscordSdk(resolvedClientId: string): Promise<string> {
  const sdkModule = await import("@discord/embedded-app-sdk");
  const DiscordSDKCtor = sdkModule.DiscordSDK;
  const discordSdk = new DiscordSDKCtor(resolvedClientId);

  await discordSdk.ready();
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

function applyRoomEnvelope(data: RoomEnvelope | { room_closed?: boolean; room_id?: string }) {
  if ("room_closed" in data && data.room_closed) {
    roomState.value = null;
    viewMode.value = "lobby";
    statusMessage.value = `房间 ${data.room_id ?? ""} 已关闭`;
    stopRoomPolling();
    return;
  }

  const envelope = data as RoomEnvelope;
  if (envelope.room) {
    roomState.value = envelope.room;
    viewMode.value = "table";
    roomInput.value = envelope.room.room_id;
    startRoomPolling();
  } else {
    roomState.value = null;
    viewMode.value = "lobby";
    stopRoomPolling();
  }

  if (profile.value && envelope.viewer_balance !== undefined) {
    profile.value.balance = Number(envelope.viewer_balance);
  }
}

function applySingleEnvelope(data: SingleGameEnvelope) {
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
    applyRoomEnvelope(data);

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
  stopRoomPolling();
  viewMode.value = "game_hub";
}

function enterBlackjackModeSelect() {
  stopRoomPolling();
  viewMode.value = "blackjack_mode_select";
}

function openComingSoon(gameName: string) {
  clearNotices();
  statusMessage.value = `${gameName} 功能开发中，敬请期待。`;
}

async function enterSingleMode() {
  clearNotices();
  stopRoomPolling();
  viewMode.value = "single";
}

async function forfeitSingleGame() {
  if (requestInFlight.value) return;

  requestInFlight.value = true;
  clearNotices();
  try {
    await apiCall<{ success: boolean; message: string }>("/api/game/forfeit", "POST", {});
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
    applySingleEnvelope(data);
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
    viewMode.value = "table";
    return;
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
    applyRoomEnvelope(data);
    statusMessage.value = "房间已创建";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "创建房间失败";
  } finally {
    requestInFlight.value = false;
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
    applyRoomEnvelope(data);
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
    applyRoomEnvelope(data);
    statusMessage.value = "已离开房间";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "离开房间失败";
  } finally {
    requestInFlight.value = false;
  }
}

async function refreshRoom(showNotice = false) {
  if (!roomState.value) return;

  try {
    const roomId = encodeURIComponent(roomState.value.room_id);
    const data = await apiCall<RoomEnvelope>(`/api/multi/room/${roomId}`, "GET", undefined, 0);
    applyRoomEnvelope(data);
    if (showNotice) {
      statusMessage.value = "房间状态已同步";
    }
  } catch (error) {
    if (showNotice) {
      errorMessage.value = error instanceof Error ? error.message : "同步房间失败";
    }
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

async function startRound() {
  if (requestInFlight.value || !roomState.value) return;

  requestInFlight.value = true;
  clearNotices();

  try {
    const data = await apiCall<RoomEnvelope>("/api/multi/room/start", "POST", {
      room_id: roomState.value.room_id,
    });
    applyRoomEnvelope(data);
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
  if (roomPollTimer !== null) {
    window.clearInterval(roomPollTimer);
    roomPollTimer = null;
  }
}

function startRoomPolling() {
  stopRoomPolling();
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
        shouldUseDiscordAuth.value = false;
        accessToken = null;
        discordSessionKey.value = "";
        console.warn("[Bootstrap] Discord 鉴权失败，降级到本地调试身份模式", embeddedError);
      }
    } else {
      shouldUseDiscordAuth.value = false;
    }

    await loadProfile();
    viewMode.value = "game_hub";
    loadingText.value = "初始化完成";
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
  void bootstrap();
});

onBeforeUnmount(() => {
  stopRoomPolling();
  stopDealerSpeechLoop();
});
</script>

<template>
  <div class="multi-root">
    <div v-if="viewMode === 'loading'" class="panel loading-panel">
      <h2>月月游戏中心</h2>
      <p>{{ loadingText }}</p>
    </div>

    <template v-else>
      <header class="top-bar">
        <div class="title-group">
          <h1>月月游戏中心</h1>
          <p>先选游戏，再选模式，和月月开战</p>
        </div>

        <div v-if="profile" class="profile-chip">
          <img class="profile-avatar" :src="playerAvatarSrc({
            user_id: Number(profile.user_id),
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
        </div>
      </header>

      <section v-if="viewMode === 'game_hub'" class="panel lobby-panel">
        <h3>选择游戏</h3>
        <div class="game-grid">
          <button class="game-card primary-btn" :disabled="requestInFlight" @click="enterBlackjackModeSelect">
            <span class="game-name">21点</span>
            <span class="game-desc">立即游玩</span>
          </button>
          <button class="game-card" :disabled="requestInFlight" @click="openComingSoon('四人麻将')">
            <span class="game-name">四人麻将</span>
            <span class="game-desc">待开发</span>
          </button>
          <button class="game-card" :disabled="requestInFlight" @click="openComingSoon('斗地主')">
            <span class="game-name">斗地主</span>
            <span class="game-desc">待开发</span>
          </button>
        </div>
        <div class="dealer-dialogue">{{ dealerSpeech }}</div>
      </section>

      <section v-else-if="viewMode === 'blackjack_mode_select'" class="panel lobby-panel">
        <h3>21点模式</h3>
        <div class="game-grid">
          <button class="game-card primary-btn" :disabled="requestInFlight" @click="enterSingleMode">
            <span class="game-name">单人对战</span>
            <span class="game-desc">你 vs 月月</span>
          </button>
          <button class="game-card primary-btn" :disabled="requestInFlight" @click="enterMultiMode">
            <span class="game-name">多人对战</span>
            <span class="game-desc">最多3人同桌</span>
          </button>
        </div>
        <div class="toolbar-actions">
          <button :disabled="requestInFlight" @click="enterGameHub">返回上一级</button>
        </div>
        <div class="dealer-dialogue">{{ dealerSpeech }}</div>
      </section>

      <section v-else-if="viewMode === 'single'" id="game-view" class="table-wrapper single-mode-view">
        <div id="game-table" style="position: relative; width: 100%; height: 100%; min-height: 70vh;">
            <div class="toolbar-actions" style="position: absolute; top: 10px; right: 10px; z-index: 100;">
              <button :disabled="requestInFlight" @click="enterBlackjackModeSelect">返回</button>
              <button :disabled="requestInFlight || !singleGame" @click="forfeitSingleGame">放弃</button>
            </div>

            <div style="display: flex; flex-direction: column; align-items: center; justify-content: space-between; height: 100%; padding: 4vh 0 6vh 0;">
                
                <!-- Dealer Area -->
                <div class="game-area" style="display: flex; flex-direction: column; align-items: center; justify-content: center; width: 100%;">
                    <h2 style="font-family: 'Playfair Display', serif; font-size: 1.8em; color: #f0e6d2; margin-bottom: 10px; opacity: 0.9; border: none; min-width: auto; padding-bottom: 0;">月月 (<span>{{ singleGame?.dealer_score ?? 0 }}</span>)</h2>
                    <TransitionGroup name="card" tag="div" class="hand" style="min-height: unset; margin: 0;">
                        <img v-for="(card, index) in singleGame?.dealer_hand || []" :key="'dealer-' + index + '-' + card" :src="cardImageSrc(card)" class="card large-card">
                    </TransitionGroup>
                </div>

                <!-- Messages Text -->
                <div class="messages" v-if="singleResultText" style="margin: 15px 0; font-weight: bold; font-size: 1.5em; color: #c0a062;">结果: {{singleResultText}}</div>
                <div class="messages" v-else style="height: 36px; margin: 15px 0;"></div>
                
                <!-- Player Area -->
                <div class="game-area" style="display: flex; flex-direction: column; align-items: center; justify-content: center; width: 100%;">
                    <h2 style="font-family: 'Playfair Display', serif; font-size: 1.8em; color: #f0e6d2; margin-bottom: 10px; opacity: 0.9; border: none; min-width: auto; padding-bottom: 0;">玩家 (<span>{{ singleGame?.player_score ?? 0 }}</span>)</h2>
                    <TransitionGroup name="card" tag="div" class="hand" style="min-height: unset; margin: 0;">
                        <img v-for="(card, index) in singleGame?.player_hand || []" :key="'player-' + index + '-' + card" :src="cardImageSrc(card)" class="card large-card">
                    </TransitionGroup>
                </div>

                <!-- Controls & Betting -->
                <div style="margin-top: 30px; display: flex; flex-direction: column; align-items: center; width: 100%; z-index: 20;">
                    <div id="controls" v-if="canSingleOperate" style="display: flex; gap: 10px;">
                        <button @click="singleHit" :disabled="requestInFlight">要牌</button>
                        <button @click="singleStand" :disabled="requestInFlight">停牌</button>
                        <button @click="singleDouble" :disabled="requestInFlight || !canSingleDouble">双倍下注</button>
                    </div>

                    <div v-if="!singleGame || !canSingleOperate" id="betting-area" style="width: 100%; max-width: 800px; display: flex; flex-direction: column; align-items: center;">
                        <div class="balance-text" style="background-color: rgba(0, 0, 0, 0.6); padding: 8px 20px; border-radius: 6px; margin-bottom: 15px; display: inline-block; font-size: 1.2em;">
                            您的余额: <span>{{ profile?.balance ?? 0 }}</span>
                                  <span v-if="singleGame"> | 刚才下注: <span>{{ singleGame?.bet_amount ?? 0 }}</span></span>
                              </div>
                              <div id="betting-controls" style="display: flex; gap: 10px; align-items: center;">
                                  <div id="manual-bet-container" style="display: flex; gap: 10px;">
                                      <input v-model.number="singleBetInput" type="number" min="1" placeholder="输入赌注" :disabled="requestInFlight" style="width: 120px;">
                                      <button :disabled="requestInFlight || !canSingleStart" @click="startSingleGame">再来一局</button>
                                  </div>
                              </div>
                          </div>
                  </div>
              </div>
          </div>
          <div id="game-dealer-section" class="dealer-section">
              <img :src="withAssetVersion(`/character/${singleResultText === '胜利' ? 'lose' : singleResultText === '失败' ? 'win' : 'normal'}.webp`)" alt="荷官" class="dealer-image">
              <div v-if="dealerSpeech" class="dialogue-box">
                  <p>{{ dealerSpeech }}</p>
              </div>
          </div>
      </section>

      <section v-else-if="viewMode === 'lobby'" class="panel lobby-panel">
        <h3>多人房间大厅</h3>

        <template v-if="isDiscordMode">
          <p class="hint-text">已连接 Discord 活动，点击下方按钮可重新加入当前会话房间。</p>
          <div class="lobby-actions">
            <button class="primary-btn" :disabled="requestInFlight" @click="autoJoinCurrentSession(true)">
              连接当前会话
            </button>
            <button :disabled="requestInFlight" @click="enterBlackjackModeSelect">
              返回模式选择
            </button>
          </div>
        </template>

        <template v-else>
          <div class="lobby-actions">
            <button class="primary-btn" :disabled="requestInFlight" @click="createRoom">
              创建房间
            </button>
            <div class="join-group">
              <input
                v-model="roomInput"
                maxlength="16"
                placeholder="输入房间号"
                :disabled="requestInFlight"
              />
              <button class="primary-btn" :disabled="requestInFlight" @click="joinRoom">
                加入房间
              </button>
            </div>
            <button :disabled="requestInFlight" @click="enterBlackjackModeSelect">
              返回模式选择
            </button>
          </div>

          <p class="hint-text">
            本地多人测试可在地址栏添加参数：
            <code>?dev_user_id=1001&dev_username=玩家A</code>
          </p>
        </template>
        <div class="dealer-dialogue">{{ dealerSpeech }}</div>
      </section>

      <section v-else-if="viewMode === 'table' && roomState" id="game-view" class="table-wrapper">
        <div id="game-table" class="green-table" style="height: auto; min-height: 70vh;">
          <div class="toolbar-actions" style="position: absolute; top: 10px; left: 10px; z-index: 100;">
            <button :disabled="requestInFlight" @click="refreshRoom(true)">同步</button>
            <button :disabled="requestInFlight" @click="leaveRoom">离开房间</button>
          </div>
          <div style="position: absolute; top: 10px; right: 10px; z-index: 100;">
            <span>状态: {{ roomStateText }}</span> | <span>房主: {{ hostDisplayName }}</span>
          </div>

          <!-- Dealer -->
          <div id="game-dealer-section" class="dealer-section">
              <img :src="dealerAvatarSrc" class="dealer-image" alt="荷官" />
              <div v-if="dealerSpeech" class="dialogue-box">
                  <p>{{ dealerSpeech }}</p>
              </div>
          </div>
          <div class="game-area position-top-cards" style="position:absolute; top: 5%; left: 50%; transform: translateX(-50%);">
              <h2>月月 (<span>{{ dealer?.score ?? 0 }}</span>)</h2>
              <TransitionGroup name="card" tag="div" class="hand">
                  <img v-for="(card, idx) in dealer?.hand || []" :key="`dealer-${idx}-${card}`" :src="cardImageSrc(card)" class="card">
              </TransitionGroup>
          </div>

          <!-- Players distributed left, bottom, right -->
          <!-- Left Player (Seat 0) -->
          <div class="game-area position-left" :class="{'empty-seat-area': !seatPlayerMap[0]}" style="position:absolute; top: 40%; left: 5%; transform: translateY(-50%);">
              <div v-if="seatPlayerMap[0]" class="player-info-tag" :class="{'turn-active': seatPlayerMap[0]?.is_current_turn}">
                  {{ seatPlayerMap[0]?.username }} ({{ seatPlayerMap[0]?.score ?? 0 }})
                  <br>下注: {{ seatPlayerMap[0]?.bet_amount ?? 0 }}
                  <span v-if="seatPlayerMap[0]?.result"><br>{{ getPlayerResultText(seatPlayerMap[0]!) }}</span>
                  <span v-if="roomState.state === 'waiting'"><br>{{ seatPlayerMap[0]?.is_ready ? '已准备' : '未准备' }}</span>
              </div>
              <div v-else class="player-info-tag">空位</div>
              <TransitionGroup v-if="seatPlayerMap[0]" name="card" tag="div" class="hand" style="flex-direction: column; min-height: unset;">
                  <img v-for="(card, idx) in seatPlayerMap[0]?.hand || []" :key="`p0-${idx}-${card}`" :src="cardImageSrc(card)" class="card" style="margin-top: -80px; margin-left: 0;">
              </TransitionGroup>
          </div>

          <!-- Bottom Player (Seat 1 - usually viewer) -->
          <div class="game-area position-bottom" :class="{'empty-seat-area': !seatPlayerMap[1]}" style="position:absolute; bottom: 15%; left: 50%; transform: translateX(-50%);">
              <h2><span v-if="seatPlayerMap[1]">{{ seatPlayerMap[1]?.username }}</span><span v-else>空位</span> (<span v-if="seatPlayerMap[1]">{{ seatPlayerMap[1]?.score ?? 0 }}</span><span v-else>0</span>)</h2>
              <TransitionGroup v-if="seatPlayerMap[1]" name="card" tag="div" class="hand">
                  <img v-for="(card, idx) in seatPlayerMap[1]?.hand || []" :key="`p1-${idx}-${card}`" :src="cardImageSrc(card)" class="card">
              </TransitionGroup>
              <div v-if="seatPlayerMap[1]" class="balance-text" :class="{'turn-active': seatPlayerMap[1]?.is_current_turn}" style="font-size: 1.2em; padding: 5px 20px; margin: 10px 0;">
                  <span v-if="Number(seatPlayerMap[1]?.user_id) === viewerUserId">余额：{{ profile?.balance ?? 0 }} | </span>下注: {{ seatPlayerMap[1]?.bet_amount ?? 0 }}
                  <span v-if="seatPlayerMap[1]?.result"> | {{ getPlayerResultText(seatPlayerMap[1]!) }}</span>
                  <span v-if="roomState.state === 'waiting'"> | {{ seatPlayerMap[1]?.is_ready ? '已准备' : '未准备' }}</span>
              </div>
          </div>
          
          <div v-if="roomState.state === 'waiting' && !viewerPlayer?.is_ready && viewerPlayer" class="multi-controls" style="bottom: 8%;">
              <div id="bet-options-container">
                  <button class="bet-option-button" @click="betInput = Math.max(10, Math.floor((profile?.balance || 0) * 0.05))" :disabled="requestInFlight">小</button>
                  <button class="bet-option-button" @click="betInput = Math.max(50, Math.floor((profile?.balance || 0) * 0.15))" :disabled="requestInFlight">中</button>
                  <button class="bet-option-button" @click="betInput = Math.max(100, Math.floor((profile?.balance || 0) * 0.30))" :disabled="requestInFlight">大</button>
                  <button class="bet-option-button" @click="betInput = profile?.balance || 0" :disabled="requestInFlight">梭哈</button>
              </div>
          </div>

          <!-- Right Player (Seat 2) -->
          <div class="game-area position-right" :class="{'empty-seat-area': !seatPlayerMap[2]}" style="position:absolute; top: 40%; right: 5%; transform: translateY(-50%);">
              <div v-if="seatPlayerMap[2]" class="player-info-tag" :class="{'turn-active': seatPlayerMap[2]?.is_current_turn}">
                  {{ seatPlayerMap[2]?.username }} ({{ seatPlayerMap[2]?.score ?? 0 }})
                  <br>下注: {{ seatPlayerMap[2]?.bet_amount ?? 0 }}
                  <span v-if="seatPlayerMap[2]?.result"><br>{{ getPlayerResultText(seatPlayerMap[2]!) }}</span>
                  <span v-if="roomState.state === 'waiting'"><br>{{ seatPlayerMap[2]?.is_ready ? '已准备' : '未准备' }}</span>
              </div>
              <div v-else class="player-info-tag">空位</div>
              <TransitionGroup v-if="seatPlayerMap[2]" name="card" tag="div" class="hand" style="flex-direction: column; min-height: unset;">
                  <img v-for="(card, idx) in seatPlayerMap[2]?.hand || []" :key="`p2-${idx}-${card}`" :src="cardImageSrc(card)" class="card" style="margin-top: -80px; margin-left: 0;">
              </TransitionGroup>
          </div>
          
          <!-- Shared Multi Controls -->
          <div class="multi-controls">
            <div id="controls" style="background: rgba(0,0,0,0.5); padding: 10px; border-radius: 10px;">
                <div v-if="roomState.state === 'waiting'" style="display: flex; gap: 5px; align-items: center; flex-wrap: wrap;">
                    <input v-model.number="betInput" type="number" min="1" placeholder="输入下注" :disabled="requestInFlight || !canSetBet" style="width: 80px;" />
                    <button :disabled="requestInFlight || !canSetBet" @click="setBet">下注</button>
                    <button :disabled="requestInFlight || !canToggleReady" @click="toggleReady">{{ readyButtonText }}</button>
                    <button v-if="isHost" :disabled="requestInFlight || !canStartRound" @click="startRound">开始本局</button>
                </div>
                <div v-if="roomState.state === 'playing'" style="display: flex; gap: 5px;">
                    <button :disabled="requestInFlight || !isMyTurn" @click="hit">要牌</button>
                    <button :disabled="requestInFlight || !isMyTurn" @click="stand">停牌</button>
                </div>
            </div>
          </div>
        </div>
      </section>

      <div v-if="statusMessage" class="status-message">{{ statusMessage }}</div>
      <div v-if="errorMessage" class="error-message">{{ errorMessage }}</div>
    </template>
  </div>
</template>

<style scoped>
:global(html),
:global(body),
:global(#app) {
  min-height: 100%;
}

:global(body) {
  overflow: auto !important;
}

.multi-root {
  min-height: 100vh;
  color: #f4f0e8;
  padding: 16px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.title-group h1 {
  margin: 0;
  font-size: 24px;
}

.title-group p {
  margin: 4px 0 0;
  opacity: 0.9;
}

.profile-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(23, 33, 50, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  padding: 8px 12px;
}

.profile-avatar {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  object-fit: cover;
  border: 2px solid #d8bd84;
}

.profile-meta {
  text-align: left;
}

.profile-name {
  font-weight: 700;
}

.profile-balance {
  font-size: 13px;
  opacity: 0.9;
}

.panel {
  background: rgba(13, 24, 39, 0.62);
  border: 1px solid rgba(215, 191, 140, 0.35);
  border-radius: 14px;
  padding: 14px;
  box-sizing: border-box;
}

.loading-panel {
  text-align: center;
  margin-top: 20vh;
}

.lobby-panel {
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
}

.lobby-panel h3 {
  margin-top: 0;
}

.lobby-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.game-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-top: 10px;
}

.game-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  text-align: left;
  padding: 12px;
}

.game-name {
  font-size: 16px;
  font-weight: 700;
}

.game-desc {
  font-size: 13px;
  opacity: 0.88;
}

.join-group {
  display: flex;
  gap: 10px;
}

.join-group input {
  flex: 1;
  min-width: 120px;
}

.hint-text {
  margin: 12px 0 0;
  opacity: 0.9;
  word-break: break-all;
}

.hint-text code {
  background: rgba(255, 255, 255, 0.08);
  padding: 2px 6px;
  border-radius: 6px;
}

.dealer-dialogue {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid rgba(215, 191, 140, 0.3);
  background: rgba(32, 22, 14, 0.45);
  font-size: 13px;
  line-height: 1.5;
  color: #ffe7bd;
}

.dealer-dialogue-inline {
  margin-top: 6px;
  margin-bottom: 8px;
}

.table-wrapper {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.table-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.room-info {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

.board {
  position: relative;
  min-height: 560px;
  border-radius: 16px;
  border: 1px solid rgba(212, 175, 55, 0.35);
  background:
    radial-gradient(circle at center, rgba(47, 109, 76, 0.9), rgba(22, 58, 38, 0.95)),
    rgba(13, 21, 31, 0.4);
  padding: 20px;
  box-sizing: border-box;
}

.seat {
  position: absolute;
  width: 260px;
  min-height: 170px;
  background: rgba(8, 17, 29, 0.58);
  border: 1px solid rgba(216, 189, 132, 0.3);
  border-radius: 12px;
  padding: 10px;
  box-sizing: border-box;
  text-align: left;
  backdrop-filter: blur(4px);
}

.seat-top-left {
  top: 14px;
  left: 14px;
}

.seat-bottom-left {
  bottom: 14px;
  left: 14px;
}

.seat-bottom-right {
  bottom: 14px;
  right: 14px;
}

.seat-top-right {
  top: 14px;
  right: 14px;
}

.dealer-seat {
  border-color: rgba(235, 203, 133, 0.6);
  background: rgba(35, 24, 16, 0.56);
}

.single-player-seat {
  bottom: 14px;
  left: 14px;
}

.avatar-ring {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  border: 2px solid rgba(216, 189, 132, 0.85);
  overflow: hidden;
  margin-bottom: 8px;
}

.avatar-ring img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.dealer-ring {
  border-color: rgba(233, 205, 130, 0.95);
}

.avatar-turn {
  box-shadow: 0 0 0 3px rgba(109, 214, 138, 0.45);
}

.seat-name {
  font-weight: 700;
  margin-bottom: 4px;
}

.seat-meta {
  font-size: 13px;
  opacity: 0.92;
  margin-bottom: 4px;
}

.seat-result {
  font-size: 13px;
  color: #ffde8d;
  margin-bottom: 6px;
}

.empty-seat {
  font-weight: 700;
  opacity: 0.8;
  margin-bottom: 6px;
}

.card-row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  min-height: 62px;
  align-items: flex-start;
}

.card {
  width: 42px;
  height: 58px;
  border-radius: 6px;
  object-fit: cover;
  border: 1px solid rgba(255, 255, 255, 0.22);
}

.control-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.control-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.control-row input {
  width: 160px;
  max-width: 100%;
}

.control-hint {
  opacity: 0.92;
  font-size: 14px;
}

input,
button {
  border: 1px solid rgba(214, 190, 140, 0.42);
  border-radius: 8px;
  background: rgba(18, 34, 53, 0.68);
  color: #f4f0e8;
  padding: 8px 12px;
  box-sizing: border-box;
}

button {
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.primary-btn {
  background: linear-gradient(180deg, rgba(189, 148, 70, 0.9), rgba(140, 103, 39, 0.9));
  color: #fff8eb;
}

.status-message,
.error-message {
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
}

.status-message {
  background: rgba(36, 108, 76, 0.5);
  border: 1px solid rgba(135, 219, 171, 0.35);
}

.error-message {
  background: rgba(120, 42, 42, 0.5);
  border: 1px solid rgba(255, 141, 141, 0.35);
}

@media (max-width: 980px) {
  .board {
    min-height: unset;
    display: grid;
    grid-template-columns: 1fr;
    gap: 10px;
    padding: 12px;
  }

  .seat {
    position: relative !important;
    top: auto !important;
    right: auto !important;
    bottom: auto !important;
    left: auto !important;
    width: 100%;
    min-height: 120px;
  }

  .dealer-seat {
    order: -1;
  }

  .card {
    width: 36px;
    height: 50px;
  }
}
</style>