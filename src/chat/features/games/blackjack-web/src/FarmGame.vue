<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import FarmPlant from './FarmPlant.vue';
import FarmSprite from './FarmSprite.vue';
import { FARM_ART, FARM_ATLASES, actionAnimation, itemArt, petAnimation, petArt, type FarmAnimation } from './farmAtlas';
import GameTools from './GameTools.vue';
import { playGameSound, playGameVoice } from './gameAudio';
import type { CropQuality, FarmAction, FarmActionBody, FarmApiCall, FarmCrop, FarmPlot, FarmProfile, FarmState, FarmVisit } from './farmTypes';

const props = defineProps<{ profile: FarmProfile; apiCall: FarmApiCall }>();
const emit = defineEmits<{ back: []; balance: [number] }>();
type FarmTab = 'shop' | 'bag' | 'upgrades' | 'visits' | 'guide' | 'pets' | 'items';
const tab = ref<FarmTab>('shop');
const panelOpen = ref(false);
const selectedPlot = ref<number | null>(null);
const sceneFrame = ref<HTMLElement | null>(null);
const sceneSize = ref({ width: 1536, height: 1024 });
let sceneObserver: ResizeObserver | undefined;
const sceneStyle = computed(() => {
  const scale = Math.max(sceneSize.value.width / 1536, sceneSize.value.height / 1024);
  return { width: `${1536 * scale}px`, height: `${1024 * scale}px`, '--world-scale': scale };
});
function plotPosition(id: number) {
  const row = Math.floor((id - 1) / 3);
  const column = (id - 1) % 3;
  const rows = Math.ceil((state.value?.plots.length ?? 3) / 3);
  const spacing = rows === 1 ? 0 : rows === 2 ? 21 : rows === 3 ? 15 : 11.5;
  const first = rows === 1 ? 59 : rows === 2 ? 48 : rows === 3 ? 42 : 40;
  const wide = sceneSize.value.width / sceneSize.value.height > 1.95;
  const top = wide ? 43 + (first + row * spacing - 43) * .68 : first + row * spacing;
  return { left: `${38 + column * 13 + row * 2.1}%`, top: `${top}%`, width: rows > 2 ? '12.5%' : '14%', height: rows > 2 ? '13.5%' : wide ? '15%' : '18%', zIndex: 3 + row * 2 + column };
}
function selectPlot(plotId: number) { selectedPlot.value = plotId; panelOpen.value = false; }
const dockIcons: Record<FarmTab, string> = { shop: 'seed', pets: 'pet', items: 'water', bag: 'bag', upgrades: 'upgrade', visits: 'visits', guide: 'guide' };
const state = ref<FarmState | null>(null);
const loading = ref(true);
const busy = ref(false);
const error = ref('');
const notice = ref('');
const now = ref(Date.now() / 1000);
const ownerId = ref('');
const visits = ref<FarmVisit[]>([]);
const visitsLoading = ref(false);
const visitsError = ref('');
const quantity = ref<Record<string, number>>({});
const pendingRetry = ref<FarmActionBody | null>(null);
const activeEffects = ref<Record<number, { kind: FarmAction; label: string; animation?: FarmAnimation; caught?: boolean; sequence: number }>>({});
const effectTimers = new Map<number, ReturnType<typeof setTimeout>>();
let effectSequence = 0;
const matureEffects = ref<number[]>([]);
const pageHidden = ref(false);
const guardianCelebrating = ref(false);
const itemDialog = ref<HTMLDialogElement | null>(null);
const selectedItem = ref('');
let matureTimer: ReturnType<typeof setTimeout> | undefined;
let guardianTimer: ReturnType<typeof setTimeout> | undefined;
const plantDialog = ref<HTMLDialogElement | null>(null);
const plantingPlot = ref<number | null>(null);
const confirmDialog = ref<HTMLDialogElement | null>(null);
const confirmation = ref<{ action: 'expand' | 'upgrade_aura'; title: string; description: string; cost: number } | null>(null);
let requestSequence = 0;
let visitsSequence = 0;
let disposed = false;
let serverOffset = 0;
let ticker: ReturnType<typeof setInterval> | undefined;
let lastRefresh = 0;
let refreshInFlight = false;
const tabs: { id: FarmTab; label: string }[] = [{ id: 'shop', label: '种子铺' }, { id: 'pets', label: '灵兽' }, { id: 'items', label: '道具' }, { id: 'bag', label: '仓库' }, { id: 'upgrades', label: '洞天' }, { id: 'visits', label: '串门' }, { id: 'guide', label: '灵草图鉴' }];
const qualityNames: Record<CropQuality, string> = { normal: '凡品', spirit: '灵品', celestial: '仙品' };
const cropMap = computed(() => new Map(state.value?.catalog.crops.map(crop => [crop.id, crop]) ?? []));
const seedMap = computed(() => new Map(state.value?.inventory.seeds.map(seed => [seed.crop_id, seed.quantity]) ?? []));
const availableSeeds = computed(() => state.value?.catalog.crops.filter(crop => (seedMap.value.get(crop.id) ?? 0) > 0 && crop.unlock_level <= state.value!.farm.level) ?? []);
const nextLand = computed(() => state.value?.catalog.land_levels.find(level => level.plot_count > state.value!.farm.unlocked_plots));
const nextAura = computed(() => state.value?.catalog.aura_levels.find(level => level.level > state.value!.farm.aura_level));
const totalHarvest = computed(() => state.value?.plots.filter(plot => plot.status === 'mature').length ?? 0);
const nextCrop = computed(() => state.value?.catalog.crops.find(crop => crop.unlock_level > state.value!.farm.level));
const inventoryCount = computed(() => state.value?.inventory.produce.reduce((sum, item) => sum + item.quantity, 0) ?? 0);
const protectionRemaining = computed(() => Math.max(0, (state.value?.farm.protected_until ?? 0) - now.value));
const pricingNote = computed(() => String(state.value?.catalog.rules.pricing_note || '灵草名称与背景参考《凡人修仙传》，价格和生长时间为本活动的经营设定。'));
const stealingNote = computed(() => String(state.value?.catalog.rules.steal_note || '新手前 24 小时免偷。每人每日最多偷采 10 次，每茬所有访客合计最多偷 1 份。'));
const xpProgress = computed(() => state.value?.farm.next_level_xp ? Math.min(100, state.value.farm.xp / state.value.farm.next_level_xp * 100) : 100);
const petMap = computed(() => new Map((state.value?.pets ?? []).map(pet => [pet.pet_id, pet])));
const itemMap = computed(() => new Map((state.value?.inventory.items ?? []).map(item => [item.item_id, item.quantity])));
const guardianRemaining = computed(() => Math.max(0, (state.value?.guardian?.guard_until ?? 0) - now.value));
const guardianActive = computed(() => Boolean(state.value?.guardian && guardianRemaining.value > 0));
const actionLocked = computed(() => busy.value || loading.value || Boolean(pendingRetry.value));
const canFeed = computed(() => ownFarm.value && Boolean(state.value?.guardian) && (itemMap.value.get('pet_food') ?? 0) > 0 && guardianRemaining.value + Number(state.value?.catalog.rules.pet_food_seconds ?? 86400) <= Number(state.value?.catalog.rules.pet_guard_max_seconds ?? 259200));
const selectedItemName = computed(() => state.value?.catalog.items?.find(item => item.id === selectedItem.value)?.name ?? '道具');
const ownFarm = computed(() => state.value?.is_owner ?? true);
function number(value: number | null | undefined) { return (Number.isFinite(value) ? value! : 0).toLocaleString('zh-CN'); }
function cropName(id: string) { return cropMap.value.get(id)?.name ?? id; }
function cropIcon(id: string | null) { return id ? cropMap.value.get(id)?.icon_key ?? id : ''; }
function activityTime(timestamp: number | undefined) { return timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) : ''; }
function secondsLabel(seconds: number) {
  const time = Math.max(0, Math.ceil(seconds));
  if (time >= 86400) return `${Math.floor(time / 86400)}天${Math.floor(time % 86400 / 3600)}时`;
  if (time >= 3600) return `${Math.floor(time / 3600)}时${Math.floor(time % 3600 / 60)}分`;
  if (time >= 60) return `${Math.floor(time / 60)}分${time % 60}秒`;
  return `${time}秒`;
}
function plotRemaining(plot: FarmPlot) { return plot.mature_at ? Math.max(0, plot.mature_at - now.value) : 0; }
function plotProgress(plot: FarmPlot) {
  if (plot.status === 'mature') return 100;
  if (!plot.planted_at || !plot.mature_at) return 0;
  return Math.min(100, Math.max(0, (now.value - plot.planted_at) / Math.max(1, plot.mature_at - plot.planted_at) * 100));
}
function plotLabel(plot: FarmPlot) {
  if (plot.status === 'empty') return '待播种';
  if (plot.status === 'mature') return `${qualityNames[plot.quality ?? 'normal']} · 可收获`;
  return plotRemaining(plot) ? `${secondsLabel(plotRemaining(plot))}后成熟` : '正在确认成熟…';
}
function stage(plot: FarmPlot) { return plotProgress(plot) < 15 ? 'seed' : plotProgress(plot) < 65 ? 'sprout' : 'grown'; }
function orderQuantity(id: string) { return Math.max(1, Math.min(99, Math.trunc(Number(quantity.value[id]) || 1))); }
function saleQuantity(value: number) { return Math.min(9999, value); }
function validQuantity(id: string) { const value = quantity.value[id] ?? 1; return Number.isInteger(value) && value >= 1 && value <= 99; }
function quantityChanged(id: string) { quantity.value[id] = orderQuantity(id); }
function purchaseDisabled(crop: FarmCrop) { return busy.value || !ownFarm.value || crop.unlock_level > (state.value?.farm.level ?? 0) || !validQuantity(crop.id) || crop.seed_price * orderQuantity(crop.id) > (state.value?.balance ?? 0); }
function avatarFallback(event: Event) { const image = event.target as HTMLImageElement; if (!image.src.endsWith('/ui/player-avatar.svg')) image.src = '/ui/player-avatar.svg'; }
function setState(value: FarmState) {
  const previous = state.value;
  if (previous?.owner.user_id === value.owner.user_id) {
    const matured = value.plots.filter(plot => plot.status === 'mature' && previous.plots.find(old => old.plot_id === plot.plot_id)?.status === 'growing').map(plot => plot.plot_id);
    if (matured.length) {
      matureEffects.value = matured;
      if (matureTimer) clearTimeout(matureTimer);
      matureTimer = setTimeout(() => { matureEffects.value = []; }, 2400);
    }
  } else matureEffects.value = [];
  if (previous && previous.owner.user_id !== value.owner.user_id) {
    activeEffects.value = {};
    for (const timer of effectTimers.values()) clearTimeout(timer);
    effectTimers.clear();
  }
  state.value = value;
  serverOffset = value.server_time - Date.now() / 1000;
  now.value = value.server_time;
  lastRefresh = Date.now();
  if (value.is_owner && typeof value.balance === 'number') emit('balance', value.balance);
  else if (typeof value.result?.balance === 'number') emit('balance', value.result.balance);
}
async function loadFarm(id = ownerId.value, silent = false) {
  const sequence = ++requestSequence;
  refreshInFlight = true;
  if (!silent) { loading.value = true; error.value = ''; }
  try {
    const response = await props.apiCall<FarmState>(`/api/farm${id ? `?owner_id=${encodeURIComponent(id)}` : ''}`, 'GET', undefined, 0);
    if (disposed || sequence !== requestSequence) return;
    ownerId.value = id;
    setState(response);
  } catch (reason) {
    if (!disposed && sequence === requestSequence && !silent) error.value = reason instanceof Error ? reason.message : '灵田暂时无法加载';
  } finally {
    if (!disposed && sequence === requestSequence) { loading.value = false; refreshInFlight = false; }
  }
}
async function loadVisits() {
  const sequence = ++visitsSequence;
  visitsLoading.value = true; visitsError.value = '';
  try {
    const response = await props.apiCall<{ entries: FarmVisit[] }>('/api/farm/visits', 'GET', undefined, 0);
    if (!disposed && sequence === visitsSequence) visits.value = response.entries;
  } catch (reason) {
    if (!disposed && sequence === visitsSequence) visitsError.value = reason instanceof Error ? reason.message : '暂时无法查看邻居';
  } finally {
    if (!disposed && sequence === visitsSequence) visitsLoading.value = false;
  }
}
function chooseTab(value: FarmTab) { tab.value = value; panelOpen.value = true; selectedPlot.value = null; if (value === 'visits') void loadVisits(); }
async function visit(id = '') {
  if (busy.value || pendingRetry.value) return;
  notice.value = ''; activeEffects.value = {};
  for (const timer of effectTimers.values()) clearTimeout(timer);
  effectTimers.clear();
  await loadFarm(id);
  if (!error.value) { panelOpen.value = false; selectedPlot.value = null; }
  if (id && !error.value && ownerId.value === id) void playGameVoice('farm_visit');
}
function showPlant(plotId: number) {
  if (busy.value || loading.value || !ownFarm.value) return;
  plantingPlot.value = plotId;
  plantDialog.value?.showModal();
}
function closePlant() { plantDialog.value?.close(); plantingPlot.value = null; }
function requestUpgrade(action: 'expand' | 'upgrade_aura') {
  if (!state.value || busy.value || loading.value) return;
  const upgrade = action === 'expand' ? nextLand.value : nextAura.value;
  if (!upgrade) return;
  confirmation.value = { action, cost: upgrade.cost, title: action === 'expand' ? '开辟新灵田' : '升级聚灵阵', description: action === 'expand' ? `解锁至 ${(upgrade as NonNullable<typeof nextLand.value>).plot_count} 块灵田，可同时种下更多灵草。` : `成长速度提升至 ${(upgrade as NonNullable<typeof nextAura.value>).growth_multiplier} 倍，正在生长的灵草也会缩短剩余等待。新的变异加成从下次播种开始。` };
  confirmDialog.value?.showModal();
}
function closeConfirm() { confirmDialog.value?.close(); confirmation.value = null; }
function eligibleItemPlot(plot: FarmPlot, item = selectedItem.value) {
  if (item === 'spirit_dew') return plot.status === 'growing' && !plot.dew_used;
  if (item === 'ward_talisman') return plot.status !== 'empty' && !plot.ward_used;
  return false;
}
function showItem(item: string) {
  if (actionLocked.value || !ownFarm.value || !(itemMap.value.get(item) ?? 0)) return;
  if (item === 'pet_food') { if (canFeed.value) void act('use_item', { item_id: item }); return; }
  selectedItem.value = item; itemDialog.value?.showModal();
}
function closeItem() { itemDialog.value?.close(); selectedItem.value = ''; }
function shopDisabled(id: string, price: number, level: number, multiple = false) {
  return actionLocked.value || !ownFarm.value || level > (state.value?.farm.level ?? 0) || (multiple && !validQuantity(id)) || price * (multiple ? orderQuantity(id) : 1) > (state.value?.balance ?? 0);
}
function wardRemaining(plot: FarmPlot) { return Math.max(0, (plot.ward_until ?? 0) - now.value); }
function holdEffect(plotId: number, duration = 2400) {
  const previousTimer = effectTimers.get(plotId);
  if (previousTimer) clearTimeout(previousTimer);
  effectTimers.set(plotId, setTimeout(() => { delete activeEffects.value[plotId]; effectTimers.delete(plotId); }, duration));
}
function displayEffect(body: FarmActionBody, message: string, caught = false) {
  if (caught || body.action === 'buy_pet' || body.action === 'equip_pet' || body.item_id === 'pet_food') {
    guardianCelebrating.value = true;
    if (guardianTimer) clearTimeout(guardianTimer);
    guardianTimer = setTimeout(() => { guardianCelebrating.value = false; }, 2200);
  }
  if (body.plot_id === undefined) return;
  const plotId = body.plot_id;
  const previousTimer = effectTimers.get(plotId);
  if (previousTimer) clearTimeout(previousTimer);
  activeEffects.value[plotId] = { kind: body.action, label: message, animation: caught ? undefined : body.item_id === 'spirit_dew' ? 'growth' : actionAnimation(body.action), caught, sequence: ++effectSequence };
  holdEffect(plotId, activeEffects.value[plotId]?.animation ? 15000 : 2400);
}
async function act(action: FarmAction, fields: Partial<Omit<FarmActionBody, 'action' | 'request_id'>> = {}, retryBody?: FarmActionBody) {
  if (busy.value || loading.value || (pendingRetry.value && !retryBody)) return;
  const body = retryBody ?? { ...fields, action, request_id: crypto.randomUUID() };
  const sequence = ++requestSequence;
  busy.value = true; error.value = ''; notice.value = ''; refreshInFlight = false;
  try {
    // 不自动创建新请求重试；相同流水号由服务端保证扣款、收获只发生一次。
    const response = await props.apiCall<FarmState>('/api/farm/action', 'POST', body, 0);
    if (disposed || sequence !== requestSequence) return;
    pendingRetry.value = null;
    setState(response);
    const message = response.result?.message || '已完成';
    notice.value = message;
    displayEffect(body, message, response.result?.caught ?? false);
    playGameSound(response.result?.caught ? 'click' : action === 'harvest' || action === 'sell' || action === 'steal' ? 'win' : 'click');
    const actionVoices: Partial<Record<FarmAction, string>> = { plant: 'farm_plant', water: 'farm_water', pest: 'farm_pest', harvest: 'farm_harvest' };
    const voice = action === 'harvest' && response.result?.quality && response.result.quality !== 'normal' ? 'farm_mutation' : actionVoices[action];
    if (voice) void playGameVoice(voice);
    if (action === 'plant') closePlant();
    if (action === 'use_item') closeItem();
    if (action === 'expand' || action === 'upgrade_aura') closeConfirm();
  } catch (reason) {
    if (disposed || sequence !== requestSequence) return;
    const status = Number((reason as { status?: number })?.status);
    const unknownResult = !status || status >= 500;
    pendingRetry.value = unknownResult ? body : null;
    error.value = unknownResult ? '还未确认这次操作的结果。请点击“核对这次操作”，同一次操作不会重复扣款。' : reason instanceof Error ? reason.message : '操作未完成';
    if (!unknownResult) { closePlant(); closeConfirm(); closeItem(); }
  } finally {
    if (!disposed && sequence === requestSequence) busy.value = false;
  }
}
function retryAction() { const body = pendingRetry.value; if (body) void act(body.action, {}, body); }
function onVisibility() { pageHidden.value = document.hidden; if (!document.hidden && !busy.value && !pendingRetry.value) void loadFarm(ownerId.value, Boolean(state.value)); }
onMounted(() => {
  pageHidden.value = document.hidden;
  if (sceneFrame.value) {
    const measure = () => { if (sceneFrame.value) sceneSize.value = { width: sceneFrame.value.clientWidth, height: sceneFrame.value.clientHeight }; };
    measure(); sceneObserver = new ResizeObserver(measure); sceneObserver.observe(sceneFrame.value);
  }
  // 预热小型图集，实际加载完成后才开始收起动画的倒计时。
  for (const atlas of Object.values(FARM_ATLASES)) { const image = new Image(); image.src = atlas.src; }
  void loadFarm();
  ticker = setInterval(() => {
    now.value = Date.now() / 1000 + serverOffset;
    if (document.hidden || busy.value || pendingRetry.value || refreshInFlight) return;
    const due = state.value?.plots.some(plot => plot.status === 'growing' && plotRemaining(plot) <= 0);
    if (Date.now() - lastRefresh > (due ? 5000 : 20000)) { lastRefresh = Date.now(); void loadFarm(ownerId.value, true); }
  }, 1000);
  document.addEventListener('visibilitychange', onVisibility);
});
onBeforeUnmount(() => {
  disposed = true; requestSequence++; visitsSequence++;
  sceneObserver?.disconnect();
  if (ticker) clearInterval(ticker);
  for (const timer of effectTimers.values()) clearTimeout(timer);
  effectTimers.clear();
  if (matureTimer) clearTimeout(matureTimer);
  if (guardianTimer) clearTimeout(guardianTimer);
  document.removeEventListener('visibilitychange', onVisibility);
  plantDialog.value?.close(); confirmDialog.value?.close(); itemDialog.value?.close();
});
</script>

<template>
  <section ref="sceneFrame" class="farm-game farm-world" :class="{ 'farm-hidden': pageHidden, 'farm-drawer-open': panelOpen, 'farm-wide': sceneSize.width / sceneSize.height > 1.95, 'farm-dense': (state?.plots.length ?? 0) > 6 }" aria-label="灵草洞天" :aria-busy="loading || busy">
    <div class="farm-world-backdrop" :style="sceneStyle"><img :src="`${FARM_ART}/garden-world.webp`" alt="" draggable="false"><div class="world-qi"><i /><i /><i /></div></div>
    <header class="farm-heading">
      <div class="farm-heading-main"><button class="farm-back farm-button quiet" :disabled="busy || loading || Boolean(pendingRetry)" @click="emit('back')"><img :src="`${FARM_ART}/icons/back.webp`" alt="">游戏大厅</button><div><span class="farm-eyebrow">月月茶楼 · 山水间的一方洞天</span><h2>灵草洞天</h2></div></div>
      <div class="farm-heading-actions"><span class="farm-wallet">灵石 <b>{{ number(ownFarm ? state?.balance ?? profile.balance : profile.balance) }}</b></span><GameTools :profile="profile" :api-call="apiCall" audio-only /><button v-if="!ownFarm" class="farm-button" :disabled="busy || loading || Boolean(pendingRetry)" @click="visit()">回我的灵田</button><button class="farm-button quiet farm-refresh" aria-label="刷新农场" :disabled="busy || loading || Boolean(pendingRetry)" @click="loadFarm()"><img :src="`${FARM_ART}/icons/refresh.webp`" alt="">刷新</button></div>
    </header>
    <div v-if="error" class="farm-alert" role="alert"><span>{{ error }}</span><button v-if="pendingRetry" class="farm-button" :disabled="busy || loading" @click="retryAction">核对这次操作</button><button v-else-if="!state" class="farm-button" :disabled="loading" @click="loadFarm()">重新加载</button></div>
    <p v-if="notice" class="farm-notice" role="status">{{ notice }}</p>
    <div v-if="loading && !state" class="farm-loading" role="status"><span class="loading-leaf">✦</span>正在推开洞天的门…</div>
    <template v-if="state">
      <div class="farm-summary">
        <div class="farm-owner"><img :src="state.owner.avatar_url || '/ui/player-avatar.svg'" alt="" @error="avatarFallback"><div><strong>{{ ownFarm ? '我的灵田' : `${state.owner.username}的灵田` }}</strong><span>{{ ownFarm ? '离线也会自然成长' : '每茬最多摘一份，给主人留足收成' }}</span></div></div>
        <div class="farm-level"><span>洞天 <b>{{ state.farm.level }}</b> 级</span><div class="farm-meter" role="progressbar" aria-label="农场升级经验" :aria-valuenow="state.farm.xp" :aria-valuemax="state.farm.next_level_xp || state.farm.xp" aria-valuemin="0"><i :style="{ width: `${xpProgress}%` }" /></div><small>{{ state.farm.next_level_xp ? `${number(state.farm.xp)} / ${number(state.farm.next_level_xp)} 修为` : '已至圆满' }}</small></div>
        <div class="farm-resource"><span>聚灵阵 {{ state.farm.aura_level }} 阶</span><strong>{{ state.farm.growth_multiplier }}<small>× 生长</small></strong></div>
        <div class="farm-resource"><span>成熟灵田</span><strong>{{ totalHarvest }}<small> / {{ state.farm.unlocked_plots }} 块</small></strong></div>
      </div>
      <div class="farm-layout">
        <main class="farm-garden" :style="sceneStyle">
          <div class="garden-caption"><div><span>灵田</span><h3>{{ ownFarm ? '灵田正好，万物有灵' : '来邻居的洞天走走' }}</h3></div><span class="garden-weather"><i /> 灵气流转</span></div>
          <div class="farm-plots">
            <article v-for="plot in state.plots" :key="`${state.owner.user_id}-${plot.plot_id}`" class="farm-plot" :style="plotPosition(plot.plot_id)" :class="[`plot-${plot.status}`, { 'plot-has-pest': plot.has_pest && !plot.pest_cleared, 'plot-effect': Boolean(activeEffects[plot.plot_id]), 'plot-selected': selectedPlot === plot.plot_id, 'plot-actions-up': (state.plots.length > 6 && plot.plot_id > 9) || (sceneSize.width / sceneSize.height > 1.95 && plot.plot_id > 6) }]" :aria-label="`${plot.plot_id}号灵田 ${plot.crop_name || '空地'} ${plotLabel(plot)}`">
              <button class="plot-hitbox" :aria-label="`选择${plot.plot_id}号灵田 ${plot.crop_name || '空地'}`" :aria-pressed="selectedPlot === plot.plot_id" @click="selectPlot(plot.plot_id)"></button>
              <header><span class="plot-number">{{ String(plot.plot_id).padStart(2, '0') }}</span><span v-if="plot.has_pest && !plot.pest_cleared" class="plot-badge pest-badge">有虫害</span><span v-else-if="plot.status === 'mature'" class="plot-badge" :class="`quality-${plot.quality}`">{{ qualityNames[plot.quality ?? 'normal'] }}</span><span v-else-if="wardRemaining(plot)" class="plot-watered">护田符 {{ secondsLabel(wardRemaining(plot)) }}</span><span v-else-if="plot.watered" class="plot-watered">已浇水</span></header>
              <div class="plot-scene" :class="{ 'scene-water': activeEffects[plot.plot_id]?.kind === 'water' }">
                <img class="plot-soil" :src="`${FARM_ART}/soil-tile.webp`" alt="" draggable="false">
                <FarmPlant v-if="plot.crop_id" :name="plot.crop_name || ''" :icon="cropIcon(plot.crop_id)" :stage="stage(plot)" :quality="plot.quality" :paused="pageHidden" />
                <FarmSprite v-if="matureEffects.includes(plot.plot_id) && !activeEffects[plot.plot_id]?.animation" class="plot-sprite plot-mature-sprite" animation="growth" :paused="pageHidden" label="灵草成熟动画" />
                <FarmSprite v-if="activeEffects[plot.plot_id]?.animation" :key="activeEffects[plot.plot_id]!.sequence" class="plot-sprite" :animation="activeEffects[plot.plot_id]!.animation" :paused="pageHidden" @ready="holdEffect(plot.plot_id)" :label="activeEffects[plot.plot_id]!.kind === 'water' ? '浇水动画' : activeEffects[plot.plot_id]!.kind === 'harvest' ? '收获动画' : '灵草生长动画'" />
                <span v-if="!plot.crop_id" class="plot-empty-mark" aria-hidden="true">＋</span>
                <div v-if="Boolean(activeEffects[plot.plot_id])" class="plot-action-effect" :class="`effect-${activeEffects[plot.plot_id]!.kind}`" aria-hidden="true">{{ activeEffects[plot.plot_id]!.caught ? '被灵兽抓住了！' : activeEffects[plot.plot_id]!.kind === 'harvest' || activeEffects[plot.plot_id]!.kind === 'steal' ? '收入仓库' : activeEffects[plot.plot_id]!.kind === 'water' ? '清泉滋养' : activeEffects[plot.plot_id]!.kind === 'pest' ? '虫害已除' : activeEffects[plot.plot_id]!.kind === 'use_item' ? '灵物生效' : '种下希望' }}</div>
              </div>
              <div class="plot-info"><strong>{{ plot.crop_name || '一畦新土' }}</strong><span>{{ plotLabel(plot) }}</span><div v-if="plot.status === 'growing'" class="plot-progress"><i :style="{ width: `${plotProgress(plot)}%` }" /></div><small v-else-if="plot.status === 'mature'">剩余 {{ plot.yield_remaining }} 份<template v-if="plot.stolen_count"> · 已被采 {{ plot.stolen_count }} 份</template></small><small v-else>种下一粒，收获一季</small><small v-if="wardRemaining(plot)" class="plot-ward-note">护田符保护 {{ secondsLabel(wardRemaining(plot)) }}</small><small v-else-if="plot.dew_used" class="plot-dew-note">灵露已滋养</small></div>
              <div v-if="ownFarm && selectedPlot === plot.plot_id" class="plot-actions">
                <button v-if="plot.status === 'empty'" class="farm-button primary" :disabled="busy || loading || Boolean(pendingRetry)" @click="showPlant(plot.plot_id)">播种</button>
                <button v-if="plot.status === 'growing' && !plot.watered" class="farm-button water" :disabled="busy || loading || Boolean(pendingRetry)" @click="act('water', { plot_id: plot.plot_id })">浇水</button>
                <button v-if="plot.status !== 'empty' && plot.has_pest && !plot.pest_cleared" class="farm-button" :disabled="busy || loading || Boolean(pendingRetry)" @click="act('pest', { plot_id: plot.plot_id })">除虫</button>
                <button v-if="plot.status === 'mature'" class="farm-button harvest" :disabled="busy || loading || Boolean(pendingRetry)" @click="act('harvest', { plot_id: plot.plot_id })">收获 {{ plot.yield_remaining }} 份</button>
                <span v-if="plot.status === 'growing' && plot.watered && (!plot.has_pest || plot.pest_cleared)" class="plot-tended">已照料 · 静待成熟</span>
              </div>
              <div v-else-if="!ownFarm && selectedPlot === plot.plot_id" class="plot-actions"><button v-if="plot.status === 'mature'" class="farm-button harvest" :disabled="busy || loading || !plot.can_steal || Boolean(pendingRetry)" @click="act('steal', { plot_id: plot.plot_id, target_user_id: state.owner.user_id })">{{ plot.can_steal ? '偷采 1 份' : '本轮不可偷采' }}</button><span v-else class="plot-tended">{{ plot.status === 'empty' ? '地主还没播种' : '还未成熟，过会儿再来' }}</span></div>
            </article>

          </div>
          <div class="garden-guardian" :class="{ 'guardian-resting': !guardianActive, 'guardian-celebrating': guardianCelebrating }">
            <template v-if="state.guardian"><FarmSprite class="guardian-sprite" :animation="petAnimation(state.guardian.pet_id)" :fallback="petArt(state.guardian.pet_id)" :loop="guardianActive" :frame="guardianActive ? undefined : 0" :paused="pageHidden" :label="state.guardian.name" /><div class="guardian-caption"><span>{{ guardianActive ? '正在巡守' : '休息中' }}</span><strong>{{ state.guardian.name }}</strong><p>{{ guardianActive ? `抓偷概率 ${Math.round(state.guardian.guard_chance * 100)}% · ${secondsLabel(guardianRemaining)}` : '守护已到期，喂一份口粮唤醒它' }}</p><button v-if="ownFarm" class="farm-button" :disabled="actionLocked || !canFeed" @click="showItem('pet_food')">喂口粮 <small>×{{ itemMap.get('pet_food') ?? 0 }}</small></button></div></template>
            <template v-else><FarmSprite class="guardian-preview" animation="fox-idle" :fallback="petArt('qingling_fox')" :loop="true" :paused="pageHidden" label="青团灵狐" /><div class="guardian-caption"><span>田间伙伴</span><strong>{{ ownFarm ? '请一只灵兽来守护灵田' : '这片灵田还没有灵兽巡守' }}</strong><p>可爱的伙伴，也能抓住偷菜者</p><button v-if="ownFarm" class="farm-button" @click="chooseTab('pets')">去灵兽阁</button></div></template>
            <button v-if="ownFarm && nextLand" class="farm-plot plot-expand" :disabled="busy || loading || Boolean(pendingRetry) || state.farm.level < nextLand.required_level || (state.balance ?? 0) < nextLand.cost" @click="requestUpgrade('expand')"><span class="expand-stone">＋</span><strong>开辟灵田</strong><span>洞天 {{ nextLand.required_level }} 级 · {{ number(nextLand.cost) }} 灵石</span><small>扩至 {{ nextLand.plot_count }} 块</small></button>
          </div>

        </main>
        <aside v-show="panelOpen" class="farm-panel" aria-label="农场经营"><button class="farm-drawer-close" aria-label="收起经营面板" @click="panelOpen = false"><img :src="`${FARM_ART}/icons/close.webp`" alt="">收起</button>

          <div class="farm-panel-content">
            <template v-if="tab === 'shop'">
              <div class="panel-title"><h3>灵种小铺</h3><span>好收成，从一粒种子开始</span></div><p v-if="!ownFarm" class="panel-hint">回到自己的灵田后，即可购买和播种。</p>
              <article v-for="crop in state.catalog.crops" :key="crop.id" class="crop-shop-item" :class="{ 'crop-locked': crop.unlock_level > state.farm.level }"><div class="crop-shop-art"><FarmPlant :name="crop.name" :icon="crop.icon_key" /></div><div class="crop-shop-info"><h4>{{ crop.name }}<span v-if="crop.unlock_level > state.farm.level">{{ crop.unlock_level }} 级解锁</span></h4><p>{{ secondsLabel(crop.grow_seconds / state.farm.growth_multiplier) }} · {{ crop.base_yield }} 份 / 茬</p><small>售价 {{ number(crop.sale_price) }} 灵石 / 份 · 持有 {{ seedMap.get(crop.id) ?? 0 }} 粒</small><div class="crop-purchase"><label>数量 <input v-model.number="quantity[crop.id]" :aria-label="`${crop.name}购买数量`" type="number" min="1" max="99" step="1" :placeholder="'1'" :disabled="busy || loading || !ownFarm" @blur="quantityChanged(crop.id)"></label><button class="farm-button" :disabled="purchaseDisabled(crop) || Boolean(pendingRetry)" @click="act('buy_seed', { crop_id: crop.id, quantity: orderQuantity(crop.id) })">{{ number(crop.seed_price * orderQuantity(crop.id)) }} 灵石 · 购买</button></div></div></article>
            </template>
            <template v-else-if="tab === 'pets'">
              <div class="panel-title"><h3>灵兽阁</h3><span>一位伙伴，一份安心</span></div>
              <p v-if="!ownFarm" class="panel-hint">只能查看邻居的当前守护灵兽；回到自己的灵田后可购置和喂养。</p>
              <article v-for="pet in state.catalog.pets ?? []" :key="pet.id" class="pet-shop-card" :class="{ 'pet-equipped': petMap.get(pet.id)?.equipped }">
                <div class="pet-portrait"><img :src="petArt(pet.icon_key)" :alt="pet.name" draggable="false"><span v-if="petMap.get(pet.id)?.equipped">已出战</span><span v-else-if="pet.unlock_level > state.farm.level">{{ pet.unlock_level }} 级解锁</span></div>
                <div class="pet-card-body"><h4>{{ pet.name }}<small>抓偷 {{ Math.round(pet.guard_chance * 100) }}%</small></h4><p>{{ pet.description }}</p><small v-if="petMap.has(pet.id)" class="pet-expiry">{{ Math.max(0, petMap.get(pet.id)!.guard_until - now) > 0 ? `守护剩余 ${secondsLabel(petMap.get(pet.id)!.guard_until - now)}` : '守护已结束，出战后可喂养' }}</small>
                  <button v-if="!petMap.has(pet.id)" class="farm-button primary" :disabled="shopDisabled(pet.id, pet.price, pet.unlock_level)" @click="act('buy_pet', { pet_id: pet.id })">{{ number(pet.price) }} 灵石 · 请回家</button><button v-else class="farm-button" :disabled="actionLocked || !ownFarm || petMap.get(pet.id)?.equipped" @click="act('equip_pet', { pet_id: pet.id })">{{ petMap.get(pet.id)?.equipped ? '正在守护这片灵田' : '出战守护' }}</button>
                </div>
              </article>
              <p class="panel-hint">{{ state.catalog.rules.pet_note }}</p>
            </template>
            <template v-else-if="tab === 'items'">
              <div class="panel-title"><h3>灵物铺</h3><span>把田间的日子，照料得更好</span></div><p v-if="!ownFarm" class="panel-hint">回到自己的灵田后，可购买和使用道具。</p>
              <article v-for="item in state.catalog.items ?? []" :key="item.id" class="item-shop-card"><img :src="itemArt(item.icon_key)" :alt="item.name" draggable="false"><div><h4>{{ item.name }}<small>持有 {{ itemMap.get(item.id) ?? 0 }}</small></h4><p>{{ item.description }}</p><div class="crop-purchase"><label>数量 <input v-model.number="quantity[item.id]" :aria-label="`${item.name}购买数量`" type="number" min="1" max="99" step="1" placeholder="1" :disabled="actionLocked || !ownFarm" @blur="quantityChanged(item.id)"></label><button class="farm-button" :disabled="shopDisabled(item.id, item.price, item.unlock_level, true)" @click="act('buy_item', { item_id: item.id, quantity: orderQuantity(item.id) })">{{ number(item.price * orderQuantity(item.id)) }} 灵石 · 购买</button></div><button class="farm-button item-use" :disabled="actionLocked || !ownFarm || !(itemMap.get(item.id) ?? 0) || (item.id === 'pet_food' ? !canFeed : !state.plots.some(plot => eligibleItemPlot(plot, item.id)))" @click="showItem(item.id)">{{ item.id === 'pet_food' ? '喂给当前灵兽' : '选择灵田使用' }}</button></div></article><p class="panel-hint">{{ state.catalog.rules.item_note }}</p>
            </template>
            <template v-else-if="tab === 'bag'">
              <div class="panel-title"><h3>我的仓库</h3><span>成熟后收获，入库后出售</span></div>
              <p v-if="!ownFarm" class="panel-empty">邻居的仓库只对本人开放。</p>
              <template v-else><h4 class="bag-section-title">收成 <span>{{ inventoryCount }} 份</span></h4><p v-if="!state.inventory.produce.length" class="panel-empty">仓库还空着。第一株灵草成熟后，记得来收获。</p><article v-for="item in state.inventory.produce" :key="`${item.crop_id}-${item.quality}`" class="bag-item"><div class="bag-art"><FarmPlant :name="cropName(item.crop_id)" :icon="cropIcon(item.crop_id)" :quality="item.quality" /></div><div><h4>{{ cropName(item.crop_id) }} <span :class="`quality-${item.quality}`">{{ qualityNames[item.quality] }}</span></h4><p>{{ item.quantity }} 份 · 每份 {{ number(item.unit_price) }} 灵石</p><button class="farm-button" :disabled="busy || loading || Boolean(pendingRetry)" @click="act('sell', { crop_id: item.crop_id, quality: item.quality, quantity: saleQuantity(item.quantity) })">出售 {{ saleQuantity(item.quantity) }} 份 · {{ number(saleQuantity(item.quantity) * item.unit_price) }} 灵石</button></div></article><h4 class="bag-section-title">种子</h4><div class="bag-seeds"><span v-for="seed in state.inventory.seeds.filter(item => item.quantity > 0)" :key="seed.crop_id">{{ cropName(seed.crop_id) }} <b>× {{ seed.quantity }}</b></span><p v-if="!state.inventory.seeds.some(item => item.quantity > 0)" class="panel-empty">种子用完了，去种子铺补一些吧。</p></div></template>
            </template>
            <template v-else-if="tab === 'upgrades'">
              <div class="panel-title"><h3>经营一方洞天</h3><span>收获积攒修为，灵石建设灵田</span></div>
              <div class="upgrade-card"><span class="upgrade-symbol">田</span><div><h4>开辟灵田</h4><p>当前 {{ state.farm.unlocked_plots }} 块<template v-if="nextLand"> → {{ nextLand.plot_count }} 块</template></p><small v-if="nextLand">需要洞天 {{ nextLand.required_level }} 级</small><small v-else>灵田已全部开辟</small></div><button v-if="nextLand" class="farm-button primary" :disabled="busy || loading || !ownFarm || Boolean(pendingRetry) || state.farm.level < nextLand.required_level || (state.balance ?? 0) < nextLand.cost" @click="requestUpgrade('expand')">{{ number(nextLand.cost) }} 灵石 · 扩建</button></div>
              <div class="upgrade-card"><span class="upgrade-symbol aura-symbol">灵</span><div><h4>聚灵阵</h4><p>{{ state.farm.growth_multiplier }} 倍生长 · 变异额外 +{{ Math.round(state.farm.mutation_bonus * 100) }}%</p><small v-if="nextAura">下一阶 {{ nextAura.growth_multiplier }} 倍生长 · 洞天 {{ nextAura.required_level }} 级解锁</small><small v-else>聚灵阵已至圆满</small></div><button v-if="nextAura" class="farm-button primary" :disabled="busy || loading || !ownFarm || Boolean(pendingRetry) || state.farm.level < nextAura.required_level || (state.balance ?? 0) < nextAura.cost" @click="requestUpgrade('upgrade_aura')">{{ number(nextAura.cost) }} 灵石 · 升级</button></div>
              <div class="farm-path"><h4>修行有序，慢慢来</h4><ol><li>从赠送的种子起步，完成第一轮收获。</li><li>出售成品积攒灵石，收获修为提升洞天等级。</li><li>开新地扩大种植，再升级聚灵阵缩短等待。</li><li>解锁珍稀灵草，静候灵品、仙品变异。</li></ol><p v-if="nextCrop">下一株：洞天 {{ nextCrop.unlock_level }} 级解锁 <b>{{ nextCrop.name }}</b></p></div>
            </template>
            <template v-else-if="tab === 'visits'">
              <div class="panel-title"><h3>拜访邻居</h3><button class="farm-button quiet" :disabled="visitsLoading" @click="loadVisits">换一批</button></div><p class="panel-hint">{{ stealingNote }}</p><p v-if="visitsLoading" class="panel-empty" role="status">正在寻找邻居的洞天…</p><p v-else-if="visitsError" class="farm-alert" role="alert">{{ visitsError }}</p><p v-else-if="!visits.length" class="panel-empty">还没有可拜访的邻居。邀请朋友来种第一株灵草吧。</p><article v-for="neighbor in visits" :key="neighbor.user_id" class="farm-neighbor"><img :src="neighbor.avatar_url || '/ui/player-avatar.svg'" alt="" @error="avatarFallback"><div><h4>{{ neighbor.username }}</h4><span>洞天 {{ neighbor.level }} 级 · {{ neighbor.mature_plots }} 块成熟</span></div><button class="farm-button" :disabled="busy || loading || Boolean(pendingRetry) || String(neighbor.user_id) === String(state.owner.user_id)" @click="visit(String(neighbor.user_id))">拜访</button></article>
            </template>
            <template v-else>
              <div class="panel-title"><h3>灵草图鉴</h3><span>{{ state.catalog.crops.length }} 种灵草 · 成品一览</span></div><p class="panel-hint">{{ pricingNote }}</p><div class="quality-guide"><span>凡品 ×1</span><span class="quality-spirit">灵品 ×2</span><span class="quality-celestial">仙品 ×5</span></div><p class="panel-hint">成熟时揭晓品质。聚灵阵提高变异机会，不保证每轮变异。</p><div class="crop-gallery"><article v-for="crop in state.catalog.crops" :key="crop.id"><FarmPlant :name="crop.name" :icon="crop.icon_key" /><h4>{{ crop.name }}</h4><span>洞天 {{ crop.unlock_level }} 级</span><p>{{ crop.description }}</p><small>凡品 {{ number(crop.sale_price) }} 灵石 / 份</small><a v-if="crop.source_url.startsWith('https://')" :href="crop.source_url" target="_blank" rel="noopener noreferrer">灵草出处 ↗</a></article></div>
            </template>
          </div>
        </aside>
      </div>
    </template>
    <div v-if="state" class="farm-world-notes"><div class="farm-garden-note"><span aria-hidden="true">✦</span> 浇水可缩短本轮等待；有虫也会成熟，及时除虫可保住收成。</div>
          <p v-if="protectionRemaining" class="farm-protection">新手守护还剩 {{ secondsLabel(protectionRemaining) }}，期间灵草不会被偷采。</p>
          <details v-if="state.activity.length" class="farm-activity"><summary>洞天记事 <span>最近 {{ state.activity.length }} 条</span></summary><ol><li v-for="(item, index) in state.activity" :key="index"><span>{{ item.message || '完成一次照料' }}</span><time>{{ activityTime(item.created_at ?? item.timestamp) }}</time></li></ol></details></div>
    <nav class="farm-tabs farm-world-dock" aria-label="农场功能"><button v-for="item in tabs" :key="item.id" :class="{ active: panelOpen && tab === item.id }" :aria-pressed="panelOpen && tab === item.id" @click="chooseTab(item.id)"><img :src="`${FARM_ART}/icons/${dockIcons[item.id]}.webp`" alt=""><span>{{ item.label }}</span><small v-if="item.id === 'bag' && inventoryCount">{{ inventoryCount }}</small></button></nav>
    <dialog ref="plantDialog" class="farm-dialog" aria-labelledby="farm-plant-title" @close="plantingPlot = null" @cancel="plantingPlot = null"><div class="dialog-heading"><div><span>第 {{ plantingPlot ?? 1 }} 块灵田</span><h3 id="farm-plant-title">选一粒灵种</h3></div><button class="farm-button quiet" aria-label="关闭播种面板" :disabled="busy || loading" @click="closePlant">关闭</button></div><p>种下后自然成长，浇水还能再快一些。</p><div class="plant-seed-list"><button v-for="crop in availableSeeds" :key="crop.id" class="plant-seed-option" :disabled="busy || loading || Boolean(pendingRetry)" @click="plantingPlot !== null && act('plant', { plot_id: plantingPlot, crop_id: crop.id })"><FarmPlant :name="crop.name" :icon="crop.icon_key" /><strong>{{ crop.name }}</strong><span>持有 {{ seedMap.get(crop.id) }} 粒</span><small>{{ secondsLabel(crop.grow_seconds / (state?.farm.growth_multiplier ?? 1)) }} · 收获 {{ crop.base_yield }} 份</small><b>播种</b></button></div><p v-if="!availableSeeds.length" class="panel-empty">没有可用种子。去种子铺挑一包吧。</p><button v-if="!availableSeeds.length" class="farm-button primary" @click="closePlant(); chooseTab('shop')">去种子铺</button><div v-if="pendingRetry" class="farm-alert" role="alert">{{ error }}<button class="farm-button" :disabled="busy || loading" @click="retryAction">核对这次操作</button></div></dialog>
    <dialog ref="itemDialog" class="farm-dialog" aria-labelledby="farm-item-title" @close="selectedItem = ''" @cancel="selectedItem = ''"><div class="dialog-heading"><div><span>持有 {{ itemMap.get(selectedItem) ?? 0 }} 份</span><h3 id="farm-item-title">{{ selectedItemName }} · 选择灵田</h3></div><button class="farm-button quiet" :disabled="busy" @click="closeItem">关闭</button></div><div class="item-target-list"><button v-for="plot in state?.plots ?? []" :key="plot.plot_id" class="item-target" :disabled="actionLocked || !eligibleItemPlot(plot)" @click="act('use_item', { item_id: selectedItem, plot_id: plot.plot_id })"><FarmPlant v-if="plot.crop_id" :name="plot.crop_name ?? ''" :icon="cropIcon(plot.crop_id)" :stage="stage(plot)" /><span v-else class="empty-item-target">待播种</span><strong>{{ plot.plot_id }}号灵田 · {{ plot.crop_name || '空田' }}</strong><span>{{ eligibleItemPlot(plot) ? '消耗 1 份使用' : plot.status === 'empty' ? '请先播种' : selectedItem === 'spirit_dew' && plot.status === 'mature' ? '已经成熟' : '本茬已经使用' }}</span></button></div><div v-if="pendingRetry" class="farm-alert" role="alert">{{ error }}<button class="farm-button" :disabled="busy || loading" @click="retryAction">核对这次操作</button></div></dialog>
    <dialog ref="confirmDialog" class="farm-dialog confirm-farm-dialog" aria-labelledby="farm-confirm-title" @close="confirmation = null" @cancel="confirmation = null"><template v-if="confirmation"><div class="dialog-heading"><h3 id="farm-confirm-title">{{ confirmation.title }}</h3><button class="farm-button quiet" :disabled="busy || loading" @click="closeConfirm">取消</button></div><p>{{ confirmation.description }}</p><div class="confirm-cost">消耗 <strong>{{ number(confirmation.cost) }}</strong> 灵石<small>当前余额 {{ number(state?.balance) }} 灵石</small></div><button class="farm-button primary" :disabled="busy || loading || Boolean(pendingRetry)" @click="act(confirmation.action)">确认{{ confirmation.action === 'expand' ? '扩建' : '升级' }}</button><div v-if="pendingRetry" class="farm-alert" role="alert">{{ error }}<button class="farm-button" :disabled="busy || loading" @click="retryAction">核对这次操作</button></div></template></dialog>
  </section>
</template>

<style scoped>
.farm-game { --farm-ink: #4b2f1d; --farm-muted: #896647; --farm-line: #d4b582; --farm-green: #356d55; --farm-gold: #b57c32; color: var(--farm-ink); color-scheme: light; display: flex; flex-direction: column; flex: 1 1 0; min-width: 0; min-height: 0; overflow: auto; padding: clamp(12px, 1.5vw, 22px); background: linear-gradient(#fff5dfed 0 135px, #d8dfb100 330px), url('/ui/farm-v2/garden-world.webp') center / cover fixed, #b5c585; border: 2px solid #b58143; border-radius: 15px; box-shadow: inset 0 0 0 3px #f4d99c88; container-type: inline-size; }
.farm-game button { font-family: inherit; }
.farm-heading, .farm-heading-main, .farm-heading-actions { display: flex; align-items: center; gap: 18px; }
.farm-heading { justify-content: space-between; flex: none; margin-bottom: 20px; }
.farm-heading h2 { margin: 3px 0 0; color: #344e3c; font-family: 'STKaiti', 'KaiTi', serif; font-size: 29px; letter-spacing: 3px; font-weight: 500; }
.farm-heading-actions :deep(.game-button) { min-height: 36px; padding: 7px 12px; border: 1px solid #b6c5ad; border-radius: 8px; color: #637764; font-size: 12px; background: transparent; box-shadow: none; }
.farm-eyebrow { font-size: 10px; color: #7c876a; letter-spacing: 3px; }
.farm-wallet { font-size: 12px; white-space: nowrap; color: #797d6b; }
.farm-wallet b { margin-left: 5px; color: #876b35; font-size: 18px; }
.farm-button { min-height: 36px; border: 1px solid #b6c5ad; border-radius: 8px; padding: 7px 12px; color: #486348; font-size: 12px; background: #f4f5e9; box-shadow: 0 2px 0 #71896415; transition: background .15s, transform .15s; }
.farm-button:not(:disabled):hover { background: #e3ecd8; }
.farm-button:not(:disabled):active { transform: translateY(1px); }
.farm-button:disabled { opacity: .48; }
.farm-button.primary { background: #54745b; border-color: #54745b; color: #faf5e3; }
.farm-button.primary:not(:disabled):hover { background: #426148; }
.farm-button.harvest { background: #eee1bd; border-color: #cdb684; color: #796232; }
.farm-button.water { background: #e8f0ed; color: #4f7d7e; border-color: #b9cdca; }
.farm-button.quiet { border-color: transparent; background: transparent; box-shadow: none; color: #637764; }
.farm-summary { flex: none; display: grid; grid-template-columns: minmax(190px, 1.3fr) minmax(140px, 1fr) minmax(100px, .6fr) minmax(100px, .6fr); align-items: center; gap: 22px; border-top: 1px solid #cbd5be; border-bottom: 1px solid #cbd5be; padding: 17px 0; margin-bottom: 22px; }
.farm-owner { display: flex; gap: 11px; align-items: center; }
.farm-owner img { width: 42px; height: 42px; object-fit: cover; border-radius: 50%; border: 2px solid #f7f5df; }
.farm-owner strong { display: block; font-size: 15px; }
.farm-owner span { display: block; color: #788473; font-size: 10px; margin-top: 5px; }
.farm-level > span, .farm-resource > span { font-size: 11px; color: #6c7b69; }
.farm-level b { color: #496d4f; font-size: 15px; }
.farm-meter, .plot-progress { overflow: hidden; background: #d8dfc9; border-radius: 8px; height: 5px; }
.farm-meter { margin: 7px 0 3px; }
.farm-meter i, .plot-progress i { height: 100%; display: block; border-radius: inherit; background: #8da275; transition: width .6s; }
.farm-level > small { color: #7a846e; font-size: 10px; }
.farm-resource > strong { display: block; font-weight: 500; font-size: 23px; color: #517055; margin-top: 5px; }
.farm-resource small { color: #808a73; font-weight: 400; font-size: 10px; }
.farm-layout { display: grid; grid-template-columns: minmax(0, 2.35fr) minmax(290px, 1fr); gap: 18px; min-height: 0; flex: 1 0 auto; align-items: start; }
.farm-garden { position: relative; min-width: 0; padding: 18px 22px 14px; border: 0; border-radius: 8px; background: linear-gradient(#ffefc233, #ffefc211 62%, #fff2d6e8 95%); }
.garden-caption { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; gap: 12px; }
.garden-caption div > span { color: #879377; font-size: 10px; letter-spacing: 3px; }
.garden-caption h3 { font-family: 'STKaiti', 'KaiTi', serif; font-size: 20px; font-weight: 500; color: #566b4e; margin: 5px 0 0; }
.garden-weather { font-size: 10px; color: #7b8e6b; white-space: nowrap; }
.garden-weather i { display: inline-block; height: 5px; width: 5px; background: #91ab79; border-radius: 50%; margin-right: 5px; box-shadow: 0 0 9px #a6c583; }
.farm-plots { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px 24px; }
.farm-plot { position: relative; min-width: 0; min-height: 239px; display: flex; flex-direction: column; align-items: stretch; padding: 0 7px 10px; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
.farm-plot > header { height: 20px; display: flex; justify-content: space-between; align-items: center; }
.plot-number { color: #9da68c; font-size: 10px; letter-spacing: 1px; }
.plot-badge { background: #e4e8cf; color: #68804d; padding: 3px 6px; border-radius: 4px; font-size: 10px; }
.plot-watered { color: #7a9890; font-size: 9px; }
.plot-badge.pest-badge { color: #996948; background: #eddfc8; }
.plot-scene { position: relative; height: 112px; display: grid; place-items: center; }
.plot-scene :deep(.farm-plant) { position: relative; height: 114px; width: 136px; max-width: 110%; z-index: 1; margin-top: -9px; }
.plot-scene :deep(.painted-plant) { clip-path: inset(0 0 18% 0); transform-origin: center 75%; }
.plot-empty-mark { color: #d7dac0; z-index: 1; margin-top: 57px; font-size: 33px; font-weight: 200; }
.plot-info { text-align: center; display: flex; flex-direction: column; gap: 4px; min-height: 58px; }
.plot-info strong { font-size: 14px; font-weight: 500; color: #4d6547; }
.plot-info > span { font-size: 10px; color: #879073; }
.plot-info > small { font-size: 9px; color: #91997f; }
.plot-progress { height: 3px; margin: 4px 12px 0; }
.plot-actions { display: flex; gap: 5px; justify-content: center; margin-top: auto; min-height: 36px; align-items: center; }
.plot-actions .farm-button { flex: 1; padding-inline: 6px; min-height: 32px; font-size: 11px; }
.plot-tended { color: #9ba28c; font-size: 10px; text-align: center; }
.plot-mature .plot-info strong { color: #8d5416; }
.plot-has-pest .plot-soil { filter: saturate(.7); }
.farm-plot.plot-expand { align-items: center; justify-content: center; gap: 7px; border: 1px dashed #a67c3f99; background: #ffefc533; font-family: inherit; color: #758869; text-align: center; }
.plot-expand:disabled { opacity: .68; cursor: not-allowed; }
.expand-stone { display: grid; place-items: center; width: 46px; height: 46px; border-radius: 50%; background: #e7ecdc; border: 1px solid #bdc7ad; font-size: 27px; color: #8e9e7b; margin-bottom: 6px; }
.plot-expand > strong { font-size: 14px; font-weight: 500; }.plot-expand > span:not(.expand-stone) { font-size: 10px; }.plot-expand > small { font-size: 10px; opacity: .8; }
.farm-garden-note { display: flex; justify-content: center; gap: 6px; font-size: 10px; color: #879275; margin: 17px 0 0; line-height: 1.8; }
.farm-garden-note > span { color: #b4ab70; }
.farm-protection { margin: 9px 0; color: #77896a; font-size: 11px; text-align: center; }
.farm-activity { border-top: 1px solid #d0d9c3; padding-top: 12px; margin-top: 18px; font-size: 12px; color: #708662; }
.farm-activity summary { cursor: pointer; padding: 7px 0; }.farm-activity summary > span { float: right; color: #939e85; font-size: 10px; }
.farm-activity ol { list-style: none; padding: 0; margin: 8px 0 0; }.farm-activity li { display: flex; justify-content: space-between; gap: 15px; border-bottom: 1px solid #dfe4d5; padding: 9px 0; color: #7c8d70; font-size: 11px; line-height: 1.6; }.farm-activity time { font-size: 10px; color: #9aa58d; white-space: nowrap; }
.farm-panel { min-width: 0; border: 3px solid #926133; background: #fff4dceb; border-radius: 9px; box-shadow: 0 4px 0 #704c2c40, inset 0 0 0 2px #e9c383; overflow: hidden; }
.farm-tabs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid #bc9156; background: #e5c593; padding: 4px; gap: 3px; }
.farm-tabs button { position: relative; flex: 1; padding: 12px 4px 13px; background: transparent; color: #8b927e; border: 0; font-size: 11px; white-space: nowrap; }
.farm-tabs button.active { color: #fff4d8; background: #9e4232; border-radius: 3px; font-weight: 600; }
.farm-tabs small { font-size: 8px; margin-left: 3px; padding: 1px 3px; color: #fff9e2; background: #a99962; border-radius: 8px; }
.farm-panel-content { max-height: 670px; overflow: auto; padding: 17px; scrollbar-width: thin; scrollbar-color: #bcc9ad transparent; }
.panel-title { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 13px; }.panel-title h3 { margin: 0; color: #516748; font-size: 16px; font-weight: 500; }.panel-title > span { color: #979b83; font-size: 9px; }
.panel-hint { font-size: 11px; color: #8c967e; line-height: 1.8; margin: 0 0 16px; }.panel-empty { font-size: 12px; color: #8b947b; text-align: center; padding: 30px 12px; line-height: 1.9; }
.crop-shop-item { display: flex; gap: 9px; padding: 15px 0; border-bottom: 1px solid #e1e3d5; }.crop-shop-item:last-child { border: 0; padding-bottom: 0; }.crop-shop-item.crop-locked { opacity: .7; }
.crop-shop-art { flex: none; width: 66px; height: 74px; align-self: center; }.crop-shop-info { flex: 1; min-width: 0; }.crop-shop-info h4, .bag-item h4 { color: #516b4e; margin: 0 0 5px; font-size: 13px; font-weight: 500; }.crop-shop-info h4 span { float: right; color: #a6aa95; font-size: 9px; margin-top: 2px; }
.crop-shop-info p { font-size: 10px; margin: 4px 0; color: #818d73; }.crop-shop-info > small { color: #939b86; font-size: 9px; }
.crop-purchase { margin-top: 9px; display: flex; align-items: center; gap: 8px; justify-content: space-between; }.crop-purchase label { white-space: nowrap; font-size: 9px; color: #929a86; }.crop-purchase input { width: 40px; height: 29px; padding: 3px; border-radius: 5px; border: 1px solid #d2dac5; background: #fffcf3; color: #64775c; text-align: center; font-size: 11px; }.crop-purchase .farm-button { min-height: 30px; padding: 5px 8px; font-size: 10px; }
.bag-section-title { font-size: 11px; color: #8b937c; font-weight: 500; border-bottom: 1px solid #dfe3d2; padding-bottom: 7px; margin: 17px 0 6px; }.bag-section-title > span { float: right; font-size: 10px; }
.bag-item { display: flex; align-items: center; gap: 10px; padding: 14px 0; border-bottom: 1px solid #e1e5d6; }.bag-art { width: 63px; height: 65px; }.bag-item > div:last-child { min-width: 0; }.bag-item h4 > span { font-size: 9px; }.bag-item p { font-size: 10px; color: #87957a; margin: 4px 0 9px; }.bag-item .farm-button { font-size: 10px; min-height: 30px; }
.bag-seeds { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 0; }.bag-seeds > span { font-size: 10px; color: #7a8a6b; padding: 7px 9px; background: #edf0e2; border-radius: 5px; }.bag-seeds b { color: #73885e; font-weight: 500; margin-left: 6px; }
.upgrade-card { display: grid; grid-template-columns: 48px 1fr; align-items: center; column-gap: 14px; row-gap: 12px; padding: 18px 0; border-bottom: 1px solid #dbe1cd; }.upgrade-symbol { display: grid; place-items: center; width: 46px; height: 46px; background: #e9ebd5; border: 1px solid #c9cea8; color: #8b9262; font-family: 'STKaiti', serif; font-size: 27px; border-radius: 12px; }.aura-symbol { background: #dfece6; color: #80a79a; border-color: #bdd4c7; }.upgrade-card h4 { font-size: 14px; font-weight: 500; color: #526a49; margin: 0 0 7px; }.upgrade-card p { margin: 0 0 5px; font-size: 11px; color: #7c8a6e; }.upgrade-card small { font-size: 10px; color: #949c86; }.upgrade-card .farm-button { grid-column: 2; justify-self: start; min-height: 31px; font-size: 11px; }
.farm-path { font-size: 11px; color: #88977b; line-height: 2; }.farm-path h4 { color: #6f835f; font-weight: 500; margin-bottom: 6px; }.farm-path ol { padding-left: 19px; }.farm-path > p { padding: 10px; background: #e9efde; border-radius: 5px; color: #7b9166; }
.farm-neighbor { display: flex; align-items: center; gap: 10px; padding: 14px 0; border-bottom: 1px solid #e0e5d4; }.farm-neighbor img { width: 38px; height: 38px; border-radius: 50%; object-fit: cover; }.farm-neighbor > div { flex: 1; min-width: 0; }.farm-neighbor h4 { margin: 0 0 4px; font-size: 12px; font-weight: 500; color: #5a704f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.farm-neighbor span { font-size: 10px; color: #8a987b; }.farm-neighbor .farm-button { font-size: 10px; min-height: 31px; }
.quality-guide { display: flex; justify-content: space-around; padding: 11px 6px; border-radius: 7px; background: #eeeee2; font-size: 11px; color: #839175; margin-bottom: 10px; }.quality-spirit { color: #4e9390 !important; }.quality-celestial { color: #a3863e !important; }
.crop-gallery { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.crop-gallery article { padding: 10px; border: 1px solid #dce2cb; border-radius: 9px; background: #f3f4e8; text-align: center; }.crop-gallery :deep(.farm-plant) { height: 90px; }.crop-gallery h4 { color: #5e7651; font-size: 13px; margin: 7px 0 4px; font-weight: 500; }.crop-gallery article > span, .crop-gallery small { font-size: 9px; color: #93a084; }.crop-gallery p { color: #85937a; font-size: 10px; line-height: 1.7; margin: 8px 0; text-align: left; }.crop-gallery a { display: block; color: #829b77; font-size: 9px; margin-top: 6px; text-underline-offset: 3px; }
.farm-alert { border: 1px solid #d8bea2; background: #fbf0dd; color: #956d46; padding: 10px 12px; margin-bottom: 12px; border-radius: 8px; font-size: 12px; display: flex; gap: 10px; align-items: center; line-height: 1.6; }.farm-alert > span { flex: 1; }.farm-alert .farm-button { flex: none; }
.farm-notice { margin: 0 0 13px; padding: 9px 12px; border-radius: 7px; color: #52744b; background: #dce9cf; font-size: 12px; }.farm-loading { display: grid; justify-items: center; gap: 17px; padding: 80px 20px; color: #82966f; font-size: 13px; }.loading-leaf { font-size: 27px; animation: farm-loading-pulse 1.8s ease-in-out infinite; }
.farm-dialog { color-scheme: light; width: 610px; padding: 25px; border: 1px solid #c5cfb4; border-radius: 17px; background: #f7f5e8; color: #647957; box-shadow: 0 20px 80px #172b254d; overflow: auto; }.farm-dialog::backdrop { background: #263d3180; backdrop-filter: blur(3px); }.dialog-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; }.dialog-heading span { font-size: 10px; letter-spacing: 2px; color: #94a082; }.dialog-heading h3 { margin: 4px 0; font-size: 21px; font-family: 'STKaiti', 'KaiTi', serif; font-weight: 500; color: #506b4b; }.farm-dialog > p { font-size: 12px; line-height: 1.7; color: #8d997e; }
.plant-seed-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 13px; margin-top: 20px; }.plant-seed-option { display: flex; flex-direction: column; align-items: center; gap: 5px; background: #eff1e2; border: 1px solid #d3dcc3; border-radius: 10px; padding: 10px; color: #607851; font-family: inherit; }.plant-seed-option:disabled { opacity: .5; }.plant-seed-option :deep(.farm-plant) { height: 95px; }.plant-seed-option strong { font-size: 14px; font-weight: 500; }.plant-seed-option span { font-size: 10px; color: #8b9b7d; }.plant-seed-option small { font-size: 9px; color: #9ba78c; }.plant-seed-option b { font-size: 11px; font-weight: 500; margin-top: 4px; padding: 5px 22px; border-radius: 6px; background: #728d60; color: #f5f2de; }
.confirm-farm-dialog { width: 410px; }.confirm-cost { padding: 18px 0 24px; color: #8b9277; font-size: 12px; }.confirm-cost strong { font-size: 25px; color: #9c8141; font-weight: 500; margin: 0 5px; }.confirm-cost small { display: block; font-size: 11px; margin-top: 8px; }.confirm-farm-dialog > .farm-button { width: 100%; }
.plot-action-effect { position: absolute; z-index: 3; top: 23%; left: 50%; width: max-content; max-width: 95%; transform: translateX(-50%); padding: 6px 9px; border-radius: 6px; font-size: 11px; color: #fff9e6; background: #78905dde; animation: farm-result-float 1.7s ease-out forwards; pointer-events: none; }.effect-water { background: #6b9fa4de; }.effect-pest { background: #9a8a63de; }@keyframes farm-result-float { 0% { opacity: 0; margin-top: 10px; } 15%, 65% { opacity: 1; } 100% { opacity: 0; margin-top: -26px; } }
@keyframes farm-loading-pulse { 50% { opacity: .4; transform: translateY(-5px); } }
@container (min-width: 1100px) { .farm-button { font-size: 13px; }.farm-owner strong { font-size: 16px; }.farm-owner span, .farm-level > span, .farm-resource > span { font-size: 12px; }.farm-level > small, .farm-resource small { font-size: 11px; }.garden-caption h3 { font-size: 23px; }.farm-plot { min-height: 250px; }.plot-scene { height: 124px; }.plot-scene :deep(.farm-plant) { width: 154px; height: 130px; }.plot-info strong { font-size: 16px; }.plot-info > span, .plot-actions .farm-button { font-size: 12px; }.plot-info > small, .plot-tended { font-size: 11px; }.farm-tabs button { font-size: 13px; }.panel-title h3 { font-size: 18px; }.panel-title > span { font-size: 11px; }.crop-shop-info h4 { font-size: 15px; }.crop-shop-info p { font-size: 12px; }.crop-shop-info > small, .crop-shop-info h4 span { font-size: 11px; }.crop-purchase label, .crop-purchase .farm-button { font-size: 12px; }.crop-purchase input { font-size: 13px; }.crop-shop-art { width: 75px; height: 83px; }.farm-garden-note { font-size: 12px; } }
@container (max-width: 900px) { .farm-layout { gap: 10px; grid-template-columns: minmax(0, 1.8fr) minmax(250px, 1fr); }.farm-plots { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }.farm-garden { padding: 12px 6px; }.farm-plot.plot-expand { width: 112px; min-height: 100px; }.plot-expand > strong { font-size: 12px; }.plot-expand > span:not(.expand-stone) { font-size: 9px; }.expand-stone { width: 30px; height: 30px; font-size: 21px; margin: 0; }.farm-summary { gap: 15px; grid-template-columns: minmax(160px, 1.2fr) 1fr .65fr .65fr; }.farm-panel-content { padding: 13px; }.crop-shop-art { width: 49px; }.farm-heading { gap: 12px; }.farm-heading-main { gap: 9px; }.farm-heading-actions { gap: 9px; } }
@container (max-width: 670px) { .farm-layout { grid-template-columns: 1fr; }.farm-plots { grid-template-columns: repeat(3, minmax(0, 1fr)); }.farm-panel-content { max-height: none; }.farm-summary { grid-template-columns: minmax(170px, 1.4fr) 1fr; gap: 15px 24px; }.farm-resource { display: flex; align-items: center; justify-content: space-between; }.farm-resource > strong { margin: 0; font-size: 19px; }.farm-heading h2 { font-size: 23px; }.farm-eyebrow { font-size: 8px; letter-spacing: 2px; }.farm-heading-main { flex-wrap: wrap; }.farm-heading-actions { flex-wrap: wrap; justify-content: flex-end; }.farm-heading .farm-back { padding-left: 0; }.farm-wallet { font-size: 10px; }.farm-wallet b { font-size: 15px; }.farm-plot { min-height: 215px; padding-inline: 5px; }.plot-scene { height: 101px; }.plot-scene :deep(.farm-plant) { height: 103px; }.crop-shop-art { width: 65px; }.crop-shop-info h4 span { float: none; margin-left: 7px; }.crop-gallery { grid-template-columns: repeat(3, minmax(0, 1fr)); }.farm-alert { flex-wrap: wrap; } }
@container (max-width: 430px) { .farm-plots { grid-template-columns: repeat(2, minmax(0, 1fr)); }.farm-heading { align-items: flex-start; }.farm-heading-main { gap: 7px; }.farm-heading-main > div { flex-basis: 100%; }.farm-summary { grid-template-columns: 1fr 1fr; gap: 13px; }.farm-owner { grid-column: 1 / -1; }.farm-level { grid-column: 1 / -1; }.crop-gallery { grid-template-columns: repeat(2, minmax(0, 1fr)); }.garden-caption h3 { font-size: 17px; }.farm-heading-actions { max-width: 45%; }.farm-heading { margin-bottom: 9px; } }
@media (max-height: 500px) { .farm-game { padding: 12px 16px; border-radius: 12px; }.farm-heading { margin-bottom: 10px; }.farm-heading h2 { font-size: 23px; }.farm-summary { padding: 10px 0; margin-bottom: 13px; }.farm-panel-content { max-height: none; }.farm-dialog { padding: 16px; }.plant-seed-list { margin-top: 10px; }.plant-seed-option :deep(.farm-plant) { height: 72px; } }
@media (prefers-reduced-motion: reduce) { .plot-action-effect, .loading-leaf { animation: none; }.farm-button, .farm-meter i, .plot-progress i { transition: none; } }

/* 庭院中的田畦与雕木经营栏共用暖金、朱红、青绿。 */
.farm-heading h2 { color: #743d22; font-size: 30px; font-weight: 700; text-shadow: 0 1px #fff9e8; }
.farm-eyebrow { color: #90643e; }.farm-wallet { color: #835d36; }.farm-wallet b { color: #8d3c28; }
.farm-heading { margin-bottom: 9px; }.farm-heading-actions :deep(.game-button) { color: #714b2a; border-color: #c19a5d; background: #fff3dbaa; }
.farm-summary { min-height: 68px; background: #fff3d9e6; border: 1px solid #cfac72; border-radius: 5px; padding: 11px 16px; margin-bottom: 14px; box-shadow: 0 2px 0 #98633721; }
.farm-owner strong, .farm-level b { color: #68442a; }.farm-owner span, .farm-level > span, .farm-resource > span { color: #886748; }.farm-resource > strong { color: #34664d; }
.farm-button { border-color: #b88d54; background: linear-gradient(#ffedc7, #efd39c); color: #62421f; border-radius: 5px; box-shadow: inset 0 1px #fff7e0, 0 2px 0 #ad7d4030; }
.farm-button:not(:disabled):hover { background: #ffe4a9; }.farm-button.primary { background: linear-gradient(#b5533c, #913b2d); border-color: #7b3529; color: #fff7dc; box-shadow: inset 0 1px #d38b5b, 0 2px 0 #5d291e2b; }.farm-button.primary:not(:disabled):hover { background: #a84631; }
.farm-button.water { color: #fff9e5; background: #367963; border-color: #245941; }.farm-button.water:not(:disabled):hover { background: #27654f; }.farm-button.harvest { color: #fff8df; background: #a76e22; border-color: #82551d; }.farm-button.harvest:not(:disabled):hover { background: #915b17; }.farm-button.quiet { color: #85603d; }
.garden-caption { min-height: 50px; margin-bottom: 10px; padding: 8px 15px; background: linear-gradient(90deg, #fff0cbe8, #fff0cb70, transparent); border-left: 3px solid #9d4831; }.garden-caption h3 { color: #573820; font-weight: 700; text-shadow: 0 1px #fff3cf; }.garden-caption div > span { color: #96713f; }.garden-weather { color: #3d6447; }
.plot-scene { isolation: isolate; height: 112px; margin-top: -7px; overflow: visible; }.plot-soil { position: absolute; z-index: -1; width: 110%; max-width: none; height: 100%; object-fit: contain; bottom: -17px; left: -5%; pointer-events: none; }.plot-scene :deep(.farm-plant) { width: 128px; height: 120px; bottom: 4px; filter: drop-shadow(0 5px 4px #31411c24); }
.plot-number { display: inline-block; color: #fff0c8; padding: 3px 7px; background: #80562cd9; border: 1px solid #e3c388; border-radius: 3px; box-shadow: 0 1px 2px #42301630; }.plot-watered { color: #235d48; background: #ecf2d4dc; padding: 3px 5px; border-radius: 3px; }.plot-badge { color: #6d441d; background: #fff0bde8; border: 1px solid #c69544; }.pest-badge { color: #963d2e; border-color: #ba7962; }
.plot-empty-mark { color: #ffe8a3; text-shadow: 0 2px #6b4727; font-size: 35px; opacity: .85; }.plot-info { margin: 8px 0; padding: 5px 3px; background: #fff5d3c9; border-radius: 4px; text-shadow: 0 1px #fffbe9; }.plot-info strong { color: #503720; font-weight: 600; }.plot-info > span { color: #5c6b3b; }.plot-info > small { color: #7e6542; }.plot-info > .plot-ward-note { color: #8f4b29; }.plot-info > .plot-dew-note { color: #337259; }.plot-progress { background: #d4ba7c; margin-top: 5px; }.plot-progress i { background: #477c51; }.plot-actions { gap: 4px; }.plot-tended { color: #584623; background: #fff4d2cf; border-radius: 3px; padding: 5px; }
.plot-sprite { position: absolute; z-index: 5; left: 50%; bottom: -18px; transform: translateX(-50%); width: min(220px, 160%); height: auto; }.plot-mature-sprite { z-index: 4; }.plot-action-effect { top: 5%; z-index: 6; max-width: 100%; white-space: normal; text-align: center; font-size: 11px; }
.farm-plot.plot-expand { min-height: 110px; width: 145px; margin-left: auto; flex: none; align-self: center; color: #6e572d; border-radius: 5px; }.expand-stone { color: #8a6836; border-color: #bba15b; background: #f7df9f9c; }
.garden-guardian { display: flex; align-items: center; gap: 6px; min-height: 138px; margin: 2px 0 0; padding: 0 12px; border-bottom: 1px solid #bc9a5a77; }.guardian-sprite, .guardian-preview { width: 152px; height: 152px; flex: none; object-fit: contain; }.guardian-caption { min-width: 0; }.guardian-caption > span { font-size: 10px; color: #7e623b; letter-spacing: 2px; }.guardian-caption strong { display: block; margin: 3px 0; color: #674229; font-family: KaiTi, STKaiti, serif; font-size: 20px; }.guardian-caption p { font-size: 11px; line-height: 1.6; margin: 4px 0 7px; color: #577040; }.guardian-caption .farm-button { min-height: 30px; font-size: 11px; }.guardian-caption small { margin-left: 5px; }.guardian-resting .guardian-sprite { filter: saturate(.65); }.guardian-celebrating .guardian-caption { animation: guardian-cheer .5s ease-in-out 3; }
.farm-garden-note, .farm-protection { color: #6f5836; }.farm-activity { border-color: #c6a36b; color: #654b2d; }.farm-activity li { color: #6b583b; border-color: #d8c5a0; }.farm-activity time { color: #9a805d; }
.farm-tabs button { color: #795130; padding: 9px 3px; }.panel-title h3 { color: #774327; font-family: KaiTi, STKaiti, serif; font-weight: 700; font-size: 20px; }.panel-title > span { color: #a28058; }.panel-hint, .panel-empty { color: #907350; }.crop-shop-info h4, .bag-item h4 { color: #6d4628; }.crop-shop-info p { color: #6f7c48; }.crop-shop-info > small, .crop-shop-info h4 span { color: #967b58; }.crop-purchase label { color: #896940; }.crop-purchase input { border-color: #c9ab77; color: #6a4b2e; }.crop-shop-item, .bag-item { border-color: #ddc5a0; }
.pet-shop-card { display: grid; grid-template-columns: 96px minmax(0, 1fr); align-items: center; gap: 7px; padding: 13px 0; border-bottom: 1px solid #d7ba87; }.pet-portrait { position: relative; width: 100%; aspect-ratio: 1; }.pet-portrait img { width: 100%; height: 100%; object-fit: contain; }.pet-portrait span { position: absolute; bottom: 0; left: 50%; transform: translateX(-50%); white-space: nowrap; font-size: 10px; padding: 3px 7px; color: #fff4d7; background: #467756; border-radius: 3px; }.pet-card-body h4, .item-shop-card h4 { color: #79472a; margin: 0 0 6px; font-size: 15px; }.pet-card-body h4 small, .item-shop-card h4 small { display: block; color: #9b7d50; margin-top: 4px; font-size: 10px; font-weight: 400; }.pet-card-body p, .item-shop-card p { font-size: 11px; line-height: 1.7; color: #877043; margin: 4px 0 8px; }.pet-card-body .farm-button { font-size: 11px; padding-inline: 7px; min-height: 32px; }.pet-expiry { font-size: 10px; line-height: 1.6; display: block; color: #3f7352; margin-bottom: 6px; }.pet-equipped .pet-portrait { background: radial-gradient(ellipse, #d6e3a7aa, transparent 72%); }
.item-shop-card { display: grid; grid-template-columns: 72px minmax(0, 1fr); align-items: start; gap: 8px; padding: 15px 0; border-bottom: 1px solid #ddc5a0; }.item-shop-card > img { width: 72px; height: 87px; object-fit: contain; }.item-shop-card .crop-purchase { flex-wrap: wrap; gap: 6px; }.item-use { margin-top: 8px; font-size: 11px; padding: 5px 8px; min-height: 30px; }
.item-target-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 15px; }.item-target { min-width: 0; padding: 8px; color: #654726; border: 1px solid #cfac72; background: #fff0c9; border-radius: 5px; }.item-target :deep(.farm-plant) { width: 100%; height: 85px; }.empty-item-target { display: grid; place-items: center; height: 85px; }.item-target strong { font-size: 11px; display: block; }.item-target span { font-size: 10px; line-height: 1.6; }.item-target:disabled { opacity: .45; }
.farm-dialog { background: #fff2d7; border: 3px solid #ab7b43; color: #654528; }.farm-hidden :deep(*) { animation-play-state: paused !important; }
@keyframes guardian-cheer { 50% { transform: translateY(-4px); } }
@container (min-width: 1100px) { .plot-scene { height: 112px; }.plot-scene :deep(.farm-plant) { width: 142px; height: 130px; }.farm-plots { gap: 10px 26px; } }
@container (max-width: 900px) { .farm-summary { grid-template-columns: minmax(150px, 1.3fr) 1fr .7fr .7fr; gap: 9px; }.farm-plot { min-height: 215px; padding-inline: 2px; }.plot-scene { height: 102px; }.plot-scene :deep(.farm-plant) { width: 102px; height: 105px; }.plot-info strong { font-size: 12px; }.plot-info > span { font-size: 10px; }.plot-actions .farm-button { font-size: 10px; padding-inline: 3px; }.garden-caption h3 { font-size: 19px; }.garden-weather { display: none; }.farm-panel-content { padding: 10px; }.pet-shop-card { grid-template-columns: 76px minmax(0, 1fr); }.crop-shop-art { width: 48px; }.panel-title { flex-wrap: wrap; gap: 4px; }.guardian-sprite, .guardian-preview { width: 124px; height: 124px; } }
@container (max-width: 670px) { .farm-layout { grid-template-columns: 1fr; }.farm-summary { grid-template-columns: 1fr 1fr; }.farm-panel-content { max-height: none; }.pet-shop-card { grid-template-columns: 100px minmax(0, 1fr); }.farm-garden { padding: 8px 2px; }.garden-guardian { padding-inline: 0; flex-wrap: wrap; }.farm-plot.plot-expand { margin-inline: auto; }.farm-heading-actions { max-width: 48%; }.farm-heading-main { min-width: 0; }.farm-heading h2 { font-size: 24px; }.farm-panel { margin-top: 10px; } }
@media (prefers-reduced-motion: reduce) { .guardian-celebrating .guardian-caption { animation: none; } }


/* 与背景共用1536×1024坐标，田地、灵植、灵兽不会在窗口缩放时漂移。 */
.farm-world { position: relative; isolation: isolate; padding: 0; overflow: hidden; border: 0; border-radius: 0; background: #366c62; box-shadow: none; min-height: 0; }
.farm-world-backdrop, .farm-world .farm-garden { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); flex: none; max-width: none; }
.farm-world-backdrop { z-index: -1; pointer-events: none; }.farm-world-backdrop > img { width: 100%; height: 100%; object-fit: fill; }
.farm-world .farm-layout { position: absolute; inset: 0; display: block; pointer-events: none; }
.farm-world .farm-garden { margin: 0; padding: 0; border: 0; border-radius: 0; background: none; pointer-events: none; }
.farm-world .garden-caption { display: none; }.farm-world .farm-plots { position: absolute; inset: 0; display: block; }
.farm-world .farm-plot { position: absolute; width: 13%; height: 17%; min-height: 0; padding: 0; margin: 0; transform: translate(-50%, -50%); display: block; border: 0; border-radius: 0; background: none; box-shadow: none; pointer-events: none; }
.farm-world .farm-plot.plot-selected { z-index: 22 !important; }
.plot-hitbox { position: absolute; z-index: 8; left: 0; right: 0; top: 10%; bottom: 0; background: none; border: 0; padding: 0; cursor: pointer; pointer-events: auto; border-radius: 30% 45% 20% 25%; }
.plot-hitbox:focus-visible { outline: 2px solid #fbe5a2; outline-offset: 3px; }.plot-hitbox:hover ~ .plot-scene .plot-soil, .plot-selected .plot-soil { filter: brightness(1.17) drop-shadow(0 0 6px #b8eaa9aa); }
.farm-world .plot-scene { position: absolute; inset: 0; margin: 0; width: 100%; height: 100%; overflow: visible; display: block; }
.farm-world .plot-soil { inset: auto; width: 124%; height: 100%; left: -12%; bottom: -12%; z-index: 0; object-fit: contain; }
.farm-world .plot-scene :deep(.farm-plant) { position: absolute; z-index: 2; width: 83%; max-width: none; height: 105%; left: 8.5%; bottom: 15%; margin: 0; filter: drop-shadow(0 7px 3px #174b2738); }
.farm-world .plot-scene :deep(.painted-plant) { clip-path: inset(0 0 12% 0); }
.farm-world .farm-plot > header { position: absolute; z-index: 3; height: auto; display: flex; align-items: center; justify-content: flex-start; top: 0; left: 18%; gap: 3px; transform: scale(clamp(.7, var(--world-scale), 1)); transform-origin: center bottom; }
.farm-world .plot-number { display: none; }.farm-world .plot-badge, .farm-world .plot-watered { padding: 2px 4px; border-radius: 2px; background: #203d35de; border: 1px solid #b49f61; color: #fff0b7; font-size: 10px; }.farm-world .plot-watered { color: #aee4d3; }.farm-world .pest-badge { color: #ffba91; }
.farm-world .plot-info { position: absolute; z-index: 3; top: 80%; left: 50%; transform: translateX(-50%); width: max-content; min-width: 58%; max-width: 110%; min-height: 0; padding: 4px 9px; gap: 2px; margin: 0; background: #183e35de; border: 1px solid #bba668b3; border-radius: 3px; box-shadow: 0 2px 4px #10302735; text-shadow: 0 1px #0c2e23; }
.farm-world .plot-info strong { color: #fff1bd; font-size: clamp(10px, 1.25cqw, 16px); font-family: KaiTi, STKaiti, serif; font-weight: 500; }.farm-world .plot-info > span { color: #e3e5ba; font-size: clamp(8px, .85cqw, 11px); }.farm-world .plot-info > small { display: none; }.farm-world .plot-selected .plot-info > .plot-ward-note { display: block; color: #f3d995; }.farm-world .plot-selected .plot-info > .plot-dew-note { display: block; color: #a6d5bd; }.farm-world .plot-progress { margin: 1px 0 0; height: 2px; background: #16372b; }.farm-world .plot-progress i { background: #c4bf77; }
.farm-world .plot-actions { position: absolute; z-index: 20; top: calc(80% + 51px); left: 50%; transform: translateX(-50%); display: flex; width: max-content; max-width: 205%; min-height: 30px; margin: 0; padding: 4px; gap: 4px; border: 1px solid #c3a569; background: #173d35f0; border-radius: 5px; box-shadow: 0 4px 12px #12382d52; pointer-events: auto; }
.farm-world .plot-actions .farm-button { flex: none; white-space: nowrap; min-height: 32px; padding: 5px 12px; font-size: 12px; }.farm-world .plot-actions .plot-tended { background: transparent; color: #d3d9b1; padding: 5px 8px; white-space: nowrap; }
.farm-world .plot-empty-mark { position: absolute; top: 47%; left: 50%; margin: 0; transform: translate(-50%, -50%); color: #eae1b2; text-shadow: 0 2px #385037; font-size: 26px; }
.farm-world .plot-sprite { width: 130%; height: auto; bottom: 5%; z-index: 7; }.farm-world .plot-sprite[data-animation='water'] { left: 72%; }.farm-world .plot-action-effect { top: 4%; font-size: 11px; }
.farm-world .farm-heading { position: absolute; z-index: 30; top: 13px; left: 15px; right: 15px; margin: 0; gap: 12px; align-items: flex-start; pointer-events: none; }
.farm-world .farm-heading-main, .farm-world .farm-heading-actions { pointer-events: auto; }.farm-world .farm-heading-main { gap: 9px; }.farm-world .farm-heading-main > div { padding: 8px 17px 8px 10px; background: linear-gradient(90deg, #183b33ed, #173d35c9); border-block: 1px solid #c1b783; border-right: 1px solid #b4ac7d; border-radius: 0 32px 32px 0; }.farm-world .farm-heading h2 { font-size: 25px; margin: 0; color: #fff0bb; text-shadow: 0 2px #0c3025; line-height: 1.15; }.farm-world .farm-eyebrow { color: #c6d6ba; font-size: 8px; letter-spacing: 1px; }
.farm-world .farm-back, .farm-world .farm-refresh { display: flex; align-items: center; gap: 3px; font-size: 10px; padding: 3px 6px; color: #fff0c9; border: 1px solid #b5ab78; border-radius: 20px; background: #17382eda; text-shadow: 0 1px #102e26; }.farm-world .farm-back img, .farm-world .farm-refresh img { width: 24px; height: 24px; object-fit: contain; }.farm-world .farm-heading-actions { gap: 7px; align-items: center; }.farm-world .farm-wallet { color: #e2d6b2; padding: 7px 13px; border: 1px solid #bcb180; border-radius: 22px; background: #153d34e0; font-size: 11px; }.farm-world .farm-wallet b { color: #fff0ba; font-size: 18px; }.farm-world .farm-heading-actions :deep(.game-button) { color: #ffebaf; border-color: #b5ab77; background: #173d35db; padding: 6px 10px; border-radius: 20px; }
.farm-world .farm-summary { position: absolute; z-index: 25; left: 15px; top: 83px; width: 265px; max-width: 35%; min-height: 0; display: grid; grid-template-columns: 1.25fr 1fr; gap: 7px 13px; align-items: center; padding: 9px 12px; margin: 0; background: #153c33ce; border: 1px solid #a79b67c7; border-radius: 0 16px 5px 5px; box-shadow: 0 3px 6px #163b2330; text-shadow: 0 1px #143528; }.farm-world .farm-owner { grid-column: 1 / -1; gap: 8px; }.farm-world .farm-owner img { width: 32px; height: 32px; border-color: #bbba8b; }.farm-world .farm-owner strong { color: #eee6bc; font-size: 13px; }.farm-world .farm-owner span { display: none; }.farm-world .farm-level { grid-column: 1 / -1; display: flex; align-items: center; gap: 7px; }.farm-world .farm-level > span { color: #e2deb3; font-size: 11px; white-space: nowrap; }.farm-world .farm-level b { color: #f4dda3; }.farm-world .farm-meter { width: 75px; height: 3px; margin: 0; background: #163a2e; }.farm-world .farm-meter i { background: #beb67d; }.farm-world .farm-level > small { font-size: 9px; color: #b6c7a2; }.farm-world .farm-resource { display: flex; align-items: baseline; gap: 5px; }.farm-world .farm-resource > span { color: #adc5ac; font-size: 9px; }.farm-world .farm-resource > strong { color: #e3ddb3; font-size: 14px; margin: 0; }.farm-world .farm-resource small { color: #a5bba2; font-size: 8px; }
.farm-world .garden-guardian { position: absolute; left: 80%; top: 66%; width: 18%; height: 22%; transform: translate(-50%, -50%); min-height: 0; padding: 0; margin: 0; display: block; border: 0; pointer-events: none; }.farm-world .guardian-sprite, .farm-world .guardian-preview { position: absolute; width: 95%; height: 95%; object-fit: contain; left: 0; top: 0; }.farm-world .guardian-caption { position: absolute; top: 79%; left: 50%; transform: translateX(-50%); width: max-content; min-width: 78%; max-width: 120%; text-align: center; background: #16392ed1; border: 1px solid #a49565a8; border-radius: 4px; padding: 5px 8px; pointer-events: auto; }.farm-world .guardian-caption > span { display: none; }.farm-world .guardian-caption strong { color: #f4e3b3; font-size: clamp(12px, 1.5cqw, 20px); margin: 0; }.farm-world .guardian-caption p { color: #cbddb2; font-size: clamp(8px, .8cqw, 11px); margin: 3px 0; }.farm-world .guardian-caption .farm-button { min-height: 24px; padding: 3px 8px; font-size: 10px; background: #d5c391; border-color: #9a8654; }.farm-world .farm-plot.plot-expand { left: 55%; top: -20%; width: 60px; height: auto; min-height: 0; padding: 4px; margin: 0; background: #153c33ce; border: 1px solid #b1a16b; border-radius: 4px; pointer-events: auto; color: #e8d6a9; }.farm-world .plot-expand .expand-stone { width: 24px; height: 24px; margin: auto; border: 0; background: transparent; color: #d7c58d; font-size: 20px; }.farm-world .plot-expand > strong { font-size: 9px; }.farm-world .plot-expand > span:not(.expand-stone), .farm-world .plot-expand > small { display: none; }
.farm-world .farm-panel { position: absolute; z-index: 50; top: 76px; right: 14px; width: min(310px, 31%); max-height: calc(100% - 180px); margin: 0; border: 2px solid #9e8954; border-radius: 6px 6px 3px 3px; background: #f5ecd7f7; box-shadow: 0 7px 24px #17372d55, inset 0 0 0 3px #d4c395; pointer-events: auto; display: flex; flex-direction: column; }
.farm-world .farm-panel-content { overflow-y: auto; padding: 12px; max-height: none; min-height: 0; flex: 1 1 auto; }.farm-drawer-close { display: flex; justify-content: flex-end; align-items: center; gap: 3px; width: 100%; flex: none; height: 30px; border: 0; border-bottom: 1px solid #b5a16e; color: #eedcad; background: #285143; padding: 3px 8px; font-size: 11px; }.farm-drawer-close img { width: 19px; height: 19px; object-fit: contain; }
.farm-world .farm-world-dock { position: absolute; z-index: 40; right: 12px; bottom: 10px; display: flex; gap: 8px; padding: 5px 10px 2px; margin: 0; border: 1px solid #b4a87a; border-radius: 26px 4px 5px 5px; background: #163d34c9; box-shadow: 0 3px 8px #2142343d; }.farm-world .farm-world-dock button { flex: none; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0; min-width: 46px; padding: 0 3px 4px; border: 0; border-radius: 50% 50% 4px 4px; color: #f5e5b6; font-family: KaiTi, STKaiti, serif; font-size: 13px; background: transparent; text-shadow: 0 1px 3px #102e22; }.farm-world .farm-world-dock button.active { background: #bca86d38; }.farm-world-dock img { width: 43px; height: 43px; object-fit: contain; filter: drop-shadow(0 1px 2px #0d3226); }.farm-world .farm-world-dock small { position: absolute; top: 3px; right: 0; font-size: 8px; background: #a84d34; }
.farm-world-notes { position: absolute; bottom: 16px; left: 15px; z-index: 35; width: min(330px, 30%); pointer-events: auto; }.farm-world-notes .farm-garden-note { display: none; }.farm-world-notes .farm-protection { margin: 0 0 4px; padding: 4px 9px; color: #dcebc9; font-size: 10px; background: #163c34b5; border-radius: 4px; text-align: left; }.farm-world-notes .farm-activity { margin: 0; padding: 0 9px; max-height: 130px; overflow-y: auto; color: #efe0b6; background: #173d34de; border: 1px solid #a89a63aa; border-radius: 4px; }.farm-world-notes .farm-activity summary > span { color: #b6c6aa; }.farm-world-notes .farm-activity li { color: #d6dfbd; border-color: #79957550; }.farm-world-notes .farm-activity time { color: #b1bc99; }
.farm-world .farm-alert, .farm-world .farm-notice { position: absolute; z-index: 70; left: 50%; top: 14%; transform: translateX(-50%); max-width: 70%; margin: 0; padding: 8px 14px; background: #fff1d6f0; border: 1px solid #a76c42; box-shadow: 0 3px 12px #173d3426; border-radius: 4px; font-size: 12px; }.farm-world .farm-notice { top: 13%; background: #163c33e8; color: #f2dfac; border-color: #c1ac77; pointer-events: none; }.farm-world .farm-dialog .farm-alert { position: static; transform: none; max-width: none; margin-top: 10px; }
.world-qi { position: absolute; inset: 0; overflow: hidden; }.world-qi i { position: absolute; width: 28%; height: 10%; border-radius: 50%; border-top: 2px solid #aeeff070; filter: blur(2px); box-shadow: 0 -4px 12px #a3fff72a; transform: rotate(-20deg); animation: world-qi-drift 13s ease-in-out infinite; }.world-qi i:nth-child(1) { left: 26%; top: 56%; }.world-qi i:nth-child(2) { left: 60%; top: 33%; animation-delay: -4s; width: 18%; }.world-qi i:nth-child(3) { left: 49%; top: 79%; animation-delay: -9s; }
@keyframes world-qi-drift { 0%,100% { opacity: .25; transform: translate(-10px,5px) rotate(-20deg); } 50% { opacity: .65; transform: translate(17px,-9px) rotate(-13deg); } }
@container (max-width: 1000px) { .farm-world .farm-heading { top: 8px; left: 9px; right: 9px; }.farm-world .farm-heading h2 { font-size: 20px; }.farm-world .farm-heading-main > div { padding: 5px 12px 5px 7px; }.farm-world .farm-summary { top: 66px; left: 10px; width: 205px; padding: 6px 8px; gap: 4px 7px; }.farm-world .farm-level > small { display: none; }.farm-world .farm-owner strong { font-size: 11px; }.farm-world .farm-owner img { width: 26px; height: 26px; }.farm-world .farm-resource > span { font-size: 8px; }.farm-world .farm-resource > strong { font-size: 12px; }.farm-world .farm-world-dock { gap: 5px; padding: 3px 7px 1px; }.farm-world .farm-world-dock button { min-width: 38px; font-size: 11px; }.farm-world-dock img { width: 35px; height: 35px; }.farm-world .farm-panel { top: 56px; max-height: calc(100% - 130px); width: min(330px, 43%); }.farm-world .plot-actions { top: calc(80% + 43px); }.farm-world .plot-actions .farm-button { font-size: 10px; min-height: 28px; padding: 4px 8px; }.farm-world .plot-info { padding: 3px 5px; }.farm-world-notes { left: 10px; bottom: 9px; }.farm-world-notes .farm-activity { font-size: 10px; } }
@container (max-width: 670px) { .farm-world .farm-heading-main { flex-wrap: nowrap; }.farm-world .farm-heading-main > div { flex-basis: auto; }.farm-world .farm-heading-actions { max-width: none; flex-wrap: nowrap; }.farm-world .farm-summary { max-width: 43%; }.farm-world .farm-owner { grid-column: 1/-1; }.farm-world .farm-level { grid-column: 1/-1; }.farm-world .farm-wallet { padding: 5px 7px; }.farm-world .farm-wallet b { font-size: 14px; }.farm-world .farm-panel { width: 48%; }.farm-world .farm-world-dock { max-width: 75%; gap: 2px; }.farm-world .farm-world-dock button { min-width: 29px; font-size: 9px; }.farm-world-dock img { width: 28px; height: 28px; }.farm-world .farm-heading h2 { font-size: 16px; }.farm-world .farm-eyebrow { display: none; }.farm-world .farm-back { font-size: 9px; }.farm-world .farm-refresh { font-size: 0; }.farm-world .farm-back img { width: 18px; height: 18px; }.farm-world .farm-heading-actions { gap: 4px; }.farm-world-notes { width: 22%; }.farm-world-notes .farm-activity summary > span { display: none; } }
@media (prefers-reduced-motion: reduce) { .world-qi i { animation: none; opacity: .3; } }


.farm-world.farm-wide .garden-guardian { top: 55%; width: 16%; height: 24%; left: 80%; }.farm-world.farm-wide .guardian-caption { top: 73%; }.farm-world.farm-wide .guardian-caption strong { font-size: 12px; }.farm-world.farm-wide .guardian-caption p { font-size: 8px; }.farm-world.farm-wide .farm-world-dock { padding-top: 1px; }.farm-world.farm-wide .farm-world-dock img { width: 29px; height: 29px; }.farm-world.farm-wide .farm-world-dock button { font-size: 10px; }.farm-world.farm-wide .farm-plot.plot-expand { left: 47%; top: -5%; }.farm-world.farm-wide .plot-info { top: 74%; }.farm-world.farm-wide .plot-actions { top: calc(74% + 39px); }

.farm-world.farm-wide .plot-info { min-width: 52%; padding: 2px 4px; }.farm-world.farm-wide .plot-info strong { font-size: 10px; }.farm-world.farm-wide .plot-info > span { font-size: 8px; }.farm-world.farm-wide .plot-scene :deep(.farm-plant) { height: 88%; bottom: 19%; }.farm-world.farm-wide .plot-actions { top: calc(74% + 31px); }
.farm-world.farm-dense .plot-scene :deep(.farm-plant) { height: 90%; bottom: 18%; }.farm-world.farm-dense .plot-info { padding: 2px 5px; }.farm-world.farm-dense .plot-info strong { font-size: clamp(9px, 1cqw, 13px); }.farm-world.farm-dense .plot-info > span { font-size: 8px; }.farm-world.farm-dense .plot-actions { top: calc(80% + 35px); }
.farm-world .farm-plot.plot-actions-up .plot-actions { top: auto; bottom: 76%; }
</style>
