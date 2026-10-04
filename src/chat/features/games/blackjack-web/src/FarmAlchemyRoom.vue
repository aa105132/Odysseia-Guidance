<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import FarmPlant from './FarmPlant.vue';
import SpiritStoneIcon from './SpiritStoneIcon.vue';
import { ALCHEMY_FURNACE, ALCHEMY_ROOM, pillArt } from './farmAtlas';
import { alchemyOdds, alchemyRevenue, alchemyYieldOutcomes } from './farmAlchemy';
import type { CropQuality, FarmAction, FarmActionBody, FarmAlchemyProgress, FarmRecipe, FarmState, FireLevel, PillGrade, RefineOutcome } from './farmTypes';

const props = defineProps<{
  state: FarmState; active: boolean; locked: boolean; busy: boolean; now: number; paused: boolean;
  error: string; notice: string; retryPending: boolean;
  result: { outcome: RefineOutcome; pillId: string | null; grade: PillGrade | null; quantity: number | null; message: string; sequence: number } | null;
}>();
const emit = defineEmits<{
  back: []; retry: [];
  action: [action: FarmAction, fields?: Partial<Omit<FarmActionBody, 'action' | 'request_id'>>];
}>();
type SlotId = 'main' | 'aux' | 'catalyst';
const slots = [{ id: 'main' as const, label: '主药', count: 2 }, { id: 'aux' as const, label: '辅药', count: 1 }, { id: 'catalyst' as const, label: '药引', count: 1 }];
const draft = ref<Record<SlotId, string>>({ main: '', aux: '', catalyst: '' });
const guideId = ref('xisui');
const quality = ref<CropQuality>('normal');
const fire = ref<FireLevel>('wen');
const picker = ref<HTMLDialogElement | null>(null);
const picking = ref<SlotId | null>(null);
const backButton = ref<HTMLButtonElement | null>(null);
const qualityNames: Record<CropQuality, string> = { normal: '凡品', spirit: '灵品', celestial: '仙品' };
const gradeNames: Record<PillGrade, string> = { low: '下品', mid: '中品', high: '上品' };
const outcomeNames: Record<RefineOutcome, string> = { success: '成丹', great_success: '大成功', explode: '炸炉', waste: '废丹' };
const catalog = computed(() => props.state.catalog.alchemy);
// Legacy servers get a level-one reference only, never inferred XP or bonus pills.
const alchemy = computed<FarmAlchemyProgress>(() => props.state.is_owner && props.state.alchemy ? props.state.alchemy : { xp: 0, level: 1, name: '青玉丹炉', level_xp: 0, next_level_xp: null, max_level: 1, duration_percent: 100 });
const bonusPills = computed(() => alchemy.value.bonus_pills ?? 0);
const nextLevel = computed(() => catalog.value?.progression?.levels.find(level => level.level === alchemy.value.level + 1));
const finalLevel = computed(() => catalog.value?.progression?.levels.find(level => level.level === alchemy.value.max_level));
const nextMilestone = computed(() => alchemy.value.bonus_pills === undefined ? undefined : catalog.value?.progression?.levels.find(level => level.level > alchemy.value.level && level.bonus_pills !== undefined && level.bonus_pills > bonusPills.value));
const maxLevel = computed(() => Boolean(props.state.alchemy && (alchemy.value.next_level_xp === null || alchemy.value.level >= alchemy.value.max_level)));
const levelProgress = computed(() => !props.state.alchemy ? 0 : maxLevel.value ? 100 : Math.max(0, Math.min(100, (alchemy.value.xp - alchemy.value.level_xp) / Math.max(1, alchemy.value.next_level_xp! - alchemy.value.level_xp) * 100)));
const xpRemaining = computed(() => Math.max(0, (alchemy.value.next_level_xp ?? alchemy.value.xp) - alchemy.value.xp));
function nextDuration(recipe: FarmRecipe) { return Math.max(1, Math.ceil(recipe.refine_seconds * alchemy.value.duration_percent / 100)); }
function recipeXp(recipe: FarmRecipe | undefined) { return recipe?.alchemy_xp ?? ({ xisui: 10, huanglong: 16, yangqi: 30 }[recipe?.id ?? '']); }
const crops = computed(() => new Map(props.state.catalog.crops.map(crop => [crop.id, crop])));
const pills = computed(() => new Map(catalog.value?.pills.map(pill => [pill.id, pill]) ?? []));
const furnace = computed(() => props.state.is_owner ? props.state.furnace ?? null : null);
const furnaceRecipe = computed(() => catalog.value?.recipes.find(recipe => recipe.id === furnace.value?.recipe_id));
const remaining = computed(() => Math.max(0, (furnace.value?.ready_at ?? props.now) - props.now));
const ready = computed(() => Boolean(furnace.value && (furnace.value.ready || remaining.value <= 0)));
const progress = computed(() => !furnace.value ? 0 : ready.value ? 100 : Math.max(0, Math.min(100, (props.now - furnace.value.started_at) / Math.max(1, furnace.value.ready_at - furnace.value.started_at) * 100)));
function recipeHerb(recipe: FarmRecipe | undefined, count: number) { return Object.entries(recipe?.materials ?? {}).find(([, quantity]) => quantity === count)?.[0] ?? ''; }
const matched = computed(() => catalog.value?.recipes.find(recipe => recipeHerb(recipe, 2) === draft.value.main && recipeHerb(recipe, 1) === draft.value.aux));
const guide = computed(() => catalog.value?.recipes.find(recipe => recipe.id === guideId.value) ?? catalog.value?.recipes[0]);
const shownRecipe = computed(() => furnace.value ? furnaceRecipe.value : matched.value ?? guide.value);
const shownQuality = computed(() => furnace.value?.quality ?? quality.value);
const shownFire = computed(() => furnace.value?.fire ?? fire.value);
const shownSlots = computed<Record<SlotId, string>>(() => furnace.value ? { main: recipeHerb(furnaceRecipe.value, 2), aux: recipeHerb(furnaceRecipe.value, 1), catalyst: furnace.value.catalyst ?? '' } : draft.value);
// Occupied batches use their already-enhanced snapshot, even if bonus_pills is absent.
const shownOutcomes = computed(() => furnace.value ? furnace.value.yield_outcomes : shownRecipe.value ? alchemyYieldOutcomes(shownRecipe.value, bonusPills.value) : undefined);
function yieldRange(quantities: number[]) { const low = Math.min(...quantities), high = Math.max(...quantities); return low === high ? String(low) : `${low}–${high}`; }
const shownYield = computed(() => shownOutcomes.value?.length ? yieldRange(shownOutcomes.value.map(item => item.quantity)) : furnace.value ? furnace.value.yield_quantity ?? 1 : undefined);
const yieldCategories = computed(() => (['success', 'great_success'] as const).flatMap(outcome => {
  const entries = shownOutcomes.value?.filter(item => item.outcome === outcome) ?? [];
  return entries.length ? [{ outcome, range: yieldRange(entries.map(item => item.quantity)), probability: entries.reduce((sum, item) => sum + item.probability, 0), equal: entries.every(item => item.probability === entries[0]!.probability) }] : [];
}));
const yieldEqual = computed(() => yieldCategories.value.every(category => category.equal));
const shownDuration = computed(() => furnace.value ? furnace.value.ready_at - furnace.value.started_at : shownRecipe.value ? nextDuration(shownRecipe.value) : undefined);
const shownXp = computed(() => furnace.value?.alchemy_xp ?? recipeXp(shownRecipe.value));
function recipeDuration(recipe: FarmRecipe) { return furnace.value?.recipe_id === recipe.id ? furnace.value.ready_at - furnace.value.started_at : nextDuration(recipe); }
const editLocked = computed(() => props.locked || !props.state.is_owner || Boolean(furnace.value));
function cropName(id: string) { return crops.value.get(id)?.name ?? id; }
function cropIcon(id: string) { return crops.value.get(id)?.icon_key ?? id; }
function pillName(id: string | null) { return id ? pills.value.get(id)?.name ?? id : ''; }
function pillIcon(id: string | null | undefined) { return pillArt(pills.value.get(id ?? '')?.icon_key ?? 'pill-waste'); }
function countOwned(id: string) { return props.state.inventory.produce.find(item => item.crop_id === id && item.quality === quality.value)?.quantity ?? 0; }
function nature(id: string) { return catalog.value?.herb_natures[id] ?? 0; }
function natureName(id: string) { return catalog.value?.nature_names[String(nature(id))] ?? '平'; }
function fireName(id: string) { return catalog.value?.fires.find(item => item.id === id)?.name ?? id; }
function duration(seconds: number) { const s = Math.max(0, Math.ceil(seconds)); return s % 60 === 0 ? `${s / 60} 分钟` : s < 60 ? `${s}秒` : `${Math.floor(s / 60)}分${s % 60}秒`; }
function timer(seconds: number) { const s = Math.ceil(seconds); return `${Math.floor(s / 60)}分${s % 60}秒`; }
function number(value: number) { return value.toLocaleString('zh-CN'); }
const requirements = computed(() => {
  const result: Record<string, number> = {};
  for (const slot of slots) if (draft.value[slot.id]) result[draft.value[slot.id]] = (result[draft.value[slot.id]] ?? 0) + slot.count;
  return result;
});
const stockReady = computed(() => Object.entries(requirements.value).every(([id, count]) => countOwned(id) >= count));
const startDisabled = computed(() => editLocked.value || !matched.value || matched.value.unlock_level > props.state.farm.level || !stockReady.value);
const startLabel = computed(() => !draft.value.main || !draft.value.aux ? '请添加主药与辅药' : !matched.value ? '未匹配丹方' : matched.value.unlock_level > props.state.farm.level ? `洞天 ${matched.value.unlock_level} 级解锁` : !stockReady.value ? '药材不足' : `开炉炼丹 · ${duration(nextDuration(matched.value))}`);
const odds = computed(() => {
  const c = catalog.value, recipe = furnace.value ? furnaceRecipe.value : matched.value;
  return c && recipe ? alchemyOdds(c, recipe, shownFire.value, shownSlots.value.catalyst) : null;
});
function materialPrice(id: string): number | null {
  const owned = props.state.inventory.produce.find(item => item.crop_id === id && item.quality === quality.value);
  if (owned) return owned.unit_price; // Already includes the server's quality multiplier.
  const multipliers = props.state.catalog.rules.quality_multipliers as Partial<Record<CropQuality, number>> | undefined;
  const base = crops.value.get(id)?.sale_price, factor = multipliers?.[quality.value];
  return base !== undefined && factor !== undefined ? base * factor : null;
}
const economics = computed(() => {
  if (furnace.value || !catalog.value || !matched.value || !stockReady.value || matched.value.unlock_level > props.state.farm.level) return null;
  let cost = 0;
  for (const [id, quantity] of Object.entries(requirements.value)) {
    const price = materialPrice(id);
    if (price === null) return null;
    cost += price * quantity;
  }
  const estimate = alchemyRevenue(catalog.value, matched.value, fire.value, quality.value, draft.value.catalyst, bonusPills.value);
  const profit = estimate.revenue - cost;
  const withoutCatalyst = alchemyRevenue(catalog.value, matched.value, fire.value, quality.value, '', bonusPills.value).revenue - (cost - (draft.value.catalyst ? materialPrice(draft.value.catalyst)! : 0));
  return { ...estimate, cost, profit, catalystLoss: draft.value.catalyst ? Math.max(0, withoutCatalyst - profit) : 0 };
});
function money(value: number) { return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }); }
function signedMoney(value: number) { return `${value >= 0 ? '+' : ''}${money(value)}`; }
const economyWarning = computed(() => !economics.value ? '' : [
  economics.value.profit < 0 ? '预期亏损' : '',
  economics.value.catalystLoss > .005 ? `药引较贵：比不放少赚 ${money(economics.value.catalystLoss)}` : '',
].filter(Boolean).join(' · '));
const economyExplanation = computed(() => !economics.value ? '' : `材料按所选品质的直售价计，含所有格的份数和额外药引。成丹后品阶独立抽取：下品 ${money(economics.value.grades.low * 100)}%、中品 ${money(economics.value.grades.mid * 100)}%、上品 ${money(economics.value.grades.high * 100)}%。预期收入＝成丹概率×加权平均产量（${money(economics.value.expectedYield)} 枚）×各品阶加权售价＋废丹概率×废丹售价；炸炉收入为零。产量分布仅以成功炼制为条件，并非每炉保底。增益＝预期收入－材料直售价。长期平均，不保证本炉收益。${economyWarning.value}`);
const cheapestCatalyst = computed(() => {
  if (furnace.value || !matched.value || !catalog.value) return null;
  return props.state.catalog.crops.filter(crop => Object.hasOwn(catalog.value!.herb_natures, crop.id) && nature(crop.id) + matched.value!.heat === 0)
    .map(crop => ({ id: crop.id, price: materialPrice(crop.id) }))
    .filter((crop): crop is { id: string; price: number } => crop.price !== null)
    .sort((a, b) => a.price - b.price || a.id.localeCompare(b.id))[0] ?? null;
});
const heat = computed(() => !odds.value ? '主辅药定方，药引调和' : odds.value.balance === 0 ? '寒热平衡' : `${odds.value.balance < 0 ? '偏寒' : '偏热'} ${Math.abs(odds.value.balance)}`);
const pickerSlot = computed(() => slots.find(slot => slot.id === picking.value));
const herbOptions = computed(() => props.state.catalog.crops.filter(crop => Object.hasOwn(catalog.value?.herb_natures ?? {}, crop.id)));
function available(id: string) {
  let used = 0;
  for (const slot of slots) if (slot.id !== picking.value && draft.value[slot.id] === id) used += slot.count;
  return Math.max(0, countOwned(id) - used);
}
function clearDraft() { draft.value = { main: '', aux: '', catalyst: '' }; }
function chooseRecipe(recipe: FarmRecipe) { if (editLocked.value) return; guideId.value = recipe.id; fire.value = recipe.fire; clearDraft(); }
function chooseQuality(value: CropQuality) { if (editLocked.value || value === quality.value) return; quality.value = value; clearDraft(); }
function openPicker(slot: SlotId) { if (editLocked.value) return; picking.value = slot; picker.value?.showModal(); }
function closePicker() { picker.value?.close(); picking.value = null; }
function selectHerb(id: string) {
  if (editLocked.value || !picking.value || available(id) < (pickerSlot.value?.count ?? 1)) return;
  draft.value[picking.value] = id;
  closePicker();
}
function removeHerb() { if (editLocked.value || !picking.value) return; draft.value[picking.value] = ''; closePicker(); }
function start() {
  if (startDisabled.value || !matched.value) return;
  emit('action', 'refine', { recipe_id: matched.value.id, fire: fire.value, quality: quality.value, ...(draft.value.catalyst ? { crop_id: draft.value.catalyst } : {}) });
}
watch(() => props.active, async active => { if (active) { await nextTick(); backButton.value?.focus({ preventScroll: true }); } else closePicker(); });
watch(() => furnace.value?.started_at, (value, previous) => { if (value !== undefined && value !== previous) { closePicker(); clearDraft(); } });
watch(() => props.state.owner.user_id, () => { clearDraft(); closePicker(); });
</script>

<template>
  <section class="alchemy-room-view" aria-label="炼丹房" :class="{ 'room-paused': paused || !active }" :style="{ '--room-art': `url(${ALCHEMY_ROOM})` }">
    <header class="room-heading">
      <button ref="backButton" class="room-back" :disabled="busy" @click="emit('back')">〈 返回灵田</button>
      <div class="room-title"><span>一炉草木，静候丹香</span><h2>炼丹房</h2></div>
      <span class="room-wallet"><SpiritStoneIcon :size="20" />灵石 <b>{{ number(state.balance ?? 0) }}</b></span>
    </header>
    <div v-if="error" class="room-alert" role="alert"><span>{{ error }}</span><button v-if="retryPending" :disabled="busy" @click="emit('retry')">核对这次操作</button></div>
    <p v-else-if="notice" class="room-notice" role="status">{{ notice }}</p>
    <p v-if="!state.is_owner || !catalog" class="room-unavailable">{{ !state.is_owner ? '丹房只对主人开放，回到自己的灵田后可开炉炼丹。' : '丹房尚未开放。' }}</p>
    <div v-else class="room-body">
      <main class="room-workspace">
        <div class="recipe-guide">
          <span class="section-caption">丹 方</span>
          <div class="alchemy-recipes" role="radiogroup" aria-label="丹方">
            <button v-for="recipe in catalog.recipes" :key="recipe.id" class="alchemy-recipe" role="radio" :aria-checked="shownRecipe?.id === recipe.id" :class="{ active: shownRecipe?.id === recipe.id, locked: recipe.unlock_level > state.farm.level }" :disabled="editLocked" @click="chooseRecipe(recipe)">
              <img :src="pillIcon(recipe.pill_id)" alt=""><div><strong>{{ recipe.name }}</strong><small>{{ recipe.unlock_level > state.farm.level ? `${recipe.unlock_level} 级解锁` : `${fireName(recipe.fire)} · ${duration(recipeDuration(recipe))}` }}</small></div>
            </button>
          </div>
        </div>
        <div class="room-workbench">
          <div class="alchemy-stage" :class="[result ? `alchemy-${result.outcome}` : '', { 'alchemy-brewing': furnace && !ready, 'alchemy-ready': ready }]">
            <div class="furnace-aura" aria-hidden="true" />
            <img class="alchemy-furnace" :src="ALCHEMY_FURNACE" alt="炼丹炉" draggable="false">
            <div v-if="furnace" class="alchemy-fire" aria-hidden="true"><i /><i /><i /></div>
            <div v-if="furnace" class="alchemy-smoke" aria-hidden="true"><i /><i /><i /></div>
            <div v-if="result" class="alchemy-result" role="status" :data-quantity="result.quantity"><img v-if="result.pillId" :src="pillIcon(result.pillId)" alt=""><strong>{{ outcomeNames[result.outcome] }}<template v-if="result.grade && result.pillId !== 'waste'"> · {{ gradeNames[result.grade] }}</template><template v-if="result.quantity !== null"> · {{ result.quantity }} 枚</template></strong></div>
            <div v-else-if="furnace" class="alchemy-timer" role="status"><strong>{{ furnaceRecipe?.name }} · {{ ready ? '丹成' : '炼制中' }}</strong><span>{{ ready ? '丹香已溢出炉外，可收丹' : `${fireName(furnace.fire)} · 剩余 ${timer(remaining)}` }}</span><div class="alchemy-timer-bar" role="progressbar" aria-label="炼制进度" :aria-valuenow="Math.round(progress)" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${progress}%` }" /></div></div>
            <div v-else class="furnace-idle-caption"><span>{{ alchemy.name }}</span><small>炉火未起 · 等君投药</small></div>
          </div>
          <div class="mixing-desk">
            <div class="desk-heading"><h3>{{ furnace ? '炉中药材' : '投入药材' }}</h3><span>{{ furnace ? '已封炉' : '点击 ＋ 从仓库添加' }}</span></div>
            <div class="herb-slots">
              <div v-for="slot in slots" :key="slot.id" class="herb-slot-wrap">
                <button class="herb-slot" :class="{ filled: shownSlots[slot.id], catalyst: slot.id === 'catalyst' }" :aria-label="slot.label" :disabled="editLocked" @click="openPicker(slot.id)">
                  <template v-if="shownSlots[slot.id]"><FarmPlant :name="cropName(shownSlots[slot.id])" :icon="cropIcon(shownSlots[slot.id])" :quality="shownQuality" :paused="paused || !active" /><b class="herb-quantity">×{{ slot.count }}</b><span class="herb-name">{{ cropName(shownSlots[slot.id]) }}</span></template>
                  <template v-else><span class="herb-plus" aria-hidden="true">＋</span><small>{{ slot.id === 'catalyst' ? '可不放' : '添加药材' }}</small></template>
                </button>
                <span class="slot-label">{{ slot.label }}<small>×{{ slot.count }}</small></span>
              </div>
            </div>
            <div class="quality-row"><span>药材品质</span><div class="alchemy-chips" role="radiogroup" aria-label="药材品质"><button v-for="(label, id) in qualityNames" :key="id" role="radio" :aria-checked="shownQuality === id" :class="{ active: shownQuality === id }" :disabled="editLocked" @click="chooseQuality(id as CropQuality)">{{ label }}</button></div></div>
            <div class="recipe-reading" aria-live="polite">
              <strong>{{ furnace ? furnaceRecipe?.name : matched ? `丹方 · ${matched.name}` : draft.main && draft.aux ? '未匹配丹方' : `丹方提示 · ${guide?.name ?? ''}` }}</strong>
              <span v-if="shownRecipe">{{ cropName(recipeHerb(shownRecipe, 2)) }} ×2 ＋ {{ cropName(recipeHerb(shownRecipe, 1)) }} ×1</span>
              <small>{{ heat }}<template v-if="shownRecipe"> · 宜{{ fireName(shownRecipe.fire) }}</template></small>
            </div>
            <div class="fire-row"><span>火候</span><div class="alchemy-chips" role="radiogroup" aria-label="火候"><button v-for="item in catalog.fires" :key="item.id" role="radio" :aria-checked="shownFire === item.id" :class="{ active: shownFire === item.id }" :disabled="editLocked" :title="item.description" @click="fire = item.id">{{ item.name }}</button></div></div>
            <div class="alchemy-odds" aria-label="开炉概率"><span>成丹 <b>{{ odds ? `${Math.round(odds.success * 100)}%` : '—' }}</b></span><span>炸炉 <b>{{ odds ? `${Math.round(odds.explode * 100)}%` : '—' }}</b></span><span>废丹 <b>{{ odds ? `${Math.round(odds.waste * 100)}%` : '—' }}</b></span></div>
            <button v-if="furnace" class="alchemy-collect room-primary" :disabled="locked || !ready" @click="emit('action', 'collect_pill')">{{ ready ? '收丹' : `静候丹成 · ${timer(remaining)}` }}</button>
            <button v-else class="alchemy-start room-primary" :disabled="startDisabled" @click="start">{{ startLabel }}</button>
            <div class="alchemy-economics" aria-label="炼丹收益比较" aria-live="polite" :title="economyExplanation" :data-valid="Boolean(economics)">
              <small v-if="shownYield !== undefined" class="alchemy-yield" aria-label="成功产量" :data-yield="shownYield">{{ furnace ? '本炉' : matched ? '' : '丹方参考 · ' }}成功{{ shownOutcomes ? '可能' : '固定' }} {{ shownYield }} 枚 · {{ duration(shownDuration ?? 0) }}<template v-if="shownOutcomes"> · {{ yieldEqual ? '档内等概率' : '档内按权重' }}</template></small>
              <small v-if="shownOutcomes" class="alchemy-yield-distribution" aria-label="成功条件下的产量分布">仅成丹后：<template v-for="(category, index) in yieldCategories" :key="category.outcome">{{ index ? ' · ' : '' }}{{ category.outcome === 'success' ? '普通成功' : '大成功' }}{{ category.range }}枚 {{ money(category.probability * 100) }}%</template></small>
              <template v-if="economics">
                <div class="economy-values"><span>材料直售 <b data-economy="cost">{{ money(economics.cost) }}</b></span><span>预期收入 <b data-economy="revenue">{{ money(economics.revenue) }}</b></span><span>增益 <b data-economy="profit">{{ signedMoney(economics.profit) }}</b></span></div>
                <small>灵石／炉 · 含各品阶与废丹 · 长期平均，非保证</small>
                <small v-if="economyWarning" class="economy-warning" data-testid="alchemy-cost-warning">{{ economyWarning }}（仍可开炉）</small>
              </template>
              <template v-else><small>{{ furnace ? '产量为封炉记录，非本炉结果' : '选齐有效主辅药后比较收益，丹方提示不代投药' }}</small><small>{{ furnace ? '炸炉 0 枚 · 废丹 1 枚 · 收丹才揭晓' : '药引额外消耗 1 份，可在选择时比较直售价' }}</small></template>
              <small v-if="furnace" class="alchemy-batch-bonus">{{ furnace.bonus_pills === undefined ? '本炉增丹未记录 · 不推测加成' : `封炉成功固定 +${furnace.bonus_pills} 枚 · 已计入产量` }}</small>
            </div>
            <p class="desk-note">{{ furnace ? '离开丹房也会继续炼制' : '开炉才扣药材 · 炼成可出售' }}</p>
          </div>
        </div>
        <p class="room-rule">药引额外消耗 1 份，调平寒热更易成丹。炸炉损失本炉药材，废丹可低价出售。</p>
      </main>
      <aside class="pill-chest" aria-label="丹匣">
        <section class="alchemy-progression" aria-label="炼丹熟练度" :title="catalog.progression?.note">
          <h3>炼丹熟练度</h3>
          <p v-if="!state.alchemy" role="status">服务端尚未提供熟练度，以下为默认参考；经验结算以服务端更新后为准。</p>
          <div class="progression-level"><strong>丹炉 Lv.{{ alchemy.level }}</strong><span>{{ alchemy.name }}</span></div>
          <p class="progression-total">累计 {{ number(alchemy.xp) }}<template v-if="!maxLevel && alchemy.next_level_xp !== null"> / {{ number(alchemy.next_level_xp) }}</template> 经验<template v-if="maxLevel"> · 已满级</template></p>
          <p v-if="state.alchemy && finalLevel" class="progression-goal">共 {{ alchemy.max_level }} 级 · 满级累计 {{ number(finalLevel.required_xp) }} 经验</p>
          <div class="progression-bar" role="progressbar" aria-label="炼丹熟练度进度" :aria-valuenow="Math.round(levelProgress)" aria-valuemin="0" aria-valuemax="100" :aria-valuetext="!state.alchemy ? '一级零经验参考，等待服务端熟练度' : maxLevel ? `已满级，累计 ${alchemy.xp} 经验` : `本级 ${alchemy.xp - alchemy.level_xp} / ${alchemy.next_level_xp! - alchemy.level_xp} 经验，累计 ${alchemy.xp} 经验`"><i :style="{ width: `${levelProgress}%` }" /></div>
          <p>当前用时 {{ alchemy.duration_percent }}% · 仅影响新开炉</p>
          <p class="progression-bonus">{{ alchemy.bonus_pills === undefined ? '服务端未提供增丹加成 · 不推测加成' : `当前成功固定 +${bonusPills} 枚 · 仅影响新开炉` }}</p>
          <p v-if="state.alchemy && !maxLevel" class="progression-next">下级 {{ nextLevel?.name ?? `丹炉 Lv.${alchemy.level + 1}` }}<template v-if="nextLevel"> · 用时 {{ nextLevel.duration_percent }}%<template v-if="alchemy.bonus_pills !== undefined && nextLevel.bonus_pills !== undefined"> · 成功固定 +{{ nextLevel.bonus_pills }} 枚</template></template><br>还需 {{ number(xpRemaining) }} 经验</p>
          <p v-else-if="maxLevel" class="progression-next">已达最高等级 · 仍可积累经验</p>
          <p v-if="nextMilestone" class="progression-milestone">下次增丹 Lv.{{ nextMilestone.level }} {{ nextMilestone.name }} · 成功固定 +{{ nextMilestone.bonus_pills }} 枚<br>累计 {{ number(nextMilestone.required_xp) }} 经验 · 还需 {{ number(Math.max(0, nextMilestone.required_xp - alchemy.xp)) }} 经验</p>
          <p class="progression-source">收丹获得经验·自动升级·不消耗灵石</p>
          <p>经验无每日上限。废丹、炸炉也获得经验；每炉仅结算一次。<br>等级不改变成丹概率或材料用量。</p>
          <p v-if="alchemy.bonus_pills !== undefined">每 5 级成功固定多 1 枚，普通成功与大成功均适用，整炉同品阶。废丹 1 枚、炸炉 0 枚不加成。</p>
          <p v-if="shownXp !== undefined" class="alchemy-batch-xp" aria-label="本炉经验">{{ !state.alchemy ? '丹方参考 · 每炉' : furnace ? '本炉收丹' : matched ? '预计每炉' : '丹方参考 · 每炉' }} +{{ shownXp }} 经验<small v-if="furnace">{{ furnace.furnace_level === undefined ? '旧炉 · 等级未记录' : `封炉丹炉 Lv.${furnace.furnace_level}` }} · {{ furnace.alchemy_xp === undefined ? '经验按丹方' : '经验为封炉记录' }}</small><small>整炉固定经验，与成败、品阶和枚数无关</small></p>
        </section>
        <div class="chest-heading"><h3>丹匣</h3><span>{{ (state.inventory.pills ?? []).reduce((sum, pill) => sum + pill.quantity, 0) }} 枚</span></div>
        <p class="chest-subtitle">炼得一炉香，换得几两金</p>
        <div class="pill-list"><p v-if="!state.inventory.pills?.length" class="chest-empty"><img :src="pillIcon('xisui')" alt="">丹匣还空着<br><small>炼成的丹药会收在这里</small></p>
          <article v-for="pill in state.inventory.pills ?? []" :key="`${pill.pill_id}-${pill.grade}`" class="pill-item">
            <div class="pill-title"><img :src="pillIcon(pill.pill_id)" :alt="pillName(pill.pill_id)"><div><h4>{{ pillName(pill.pill_id) }} <small v-if="pill.pill_id !== 'waste'">{{ gradeNames[pill.grade] }}</small></h4><p>{{ pill.quantity }} 枚 · 每枚 {{ number(pill.unit_price) }} 灵石</p></div></div>
            <button :disabled="locked" @click="emit('action', 'sell_pill', { pill_id: pill.pill_id, grade: pill.grade, quantity: 1 })">出售 1 枚 · {{ number(pill.unit_price) }} 灵石</button>
            <button v-if="pill.quantity > 1" :disabled="locked" @click="emit('action', 'sell_pill', { pill_id: pill.pill_id, grade: pill.grade, quantity: pill.quantity })">全部出售 · {{ number(pill.unit_price * pill.quantity) }} 灵石</button>
          </article>
        </div>
        <span class="chest-footer">药铺常年收购 · 价格随品阶而定</span>
      </aside>
    </div>
    <dialog ref="picker" class="herb-picker" aria-labelledby="herb-picker-title" @close="picking = null" @cancel="picking = null">
      <header><div><small>{{ qualityNames[quality] }}药材 · 本格需要 {{ pickerSlot?.count ?? 1 }} 份</small><h3 id="herb-picker-title">选择{{ pickerSlot?.label }}</h3></div><button aria-label="关闭药材选择" @click="closePicker">关闭</button></header>
      <p>从仓库选择，开炉时才扣除。可用数量已扣除其他格的待投药材。</p>
      <p v-if="picking === 'catalyst'" data-testid="catalyst-recommendation">{{ cheapestCatalyst ? `调平寒热最低价：${cropName(cheapestCatalyst.id)}，${money(cheapestCatalyst.price)} 灵石／份（${qualityNames[quality]}）。` : '先选齐主辅药，再比较调和药引。' }}仅建议，不自动投药；贵药引可能抵消收益，可不放。</p>
      <div class="herb-options"><button v-for="crop in herbOptions" :key="crop.id" class="herb-option" :data-crop-id="crop.id" :aria-label="`${crop.name}，持有 ${countOwned(crop.id)} 份`" :disabled="editLocked || available(crop.id) < (pickerSlot?.count ?? 1)" @click="selectHerb(crop.id)"><FarmPlant :name="crop.name" :icon="crop.icon_key" :quality="quality" /><strong>{{ crop.name }}</strong><span>{{ natureName(crop.id) }}性 · 持有 {{ countOwned(crop.id) }} 份</span><small>可用 {{ available(crop.id) }} 份<template v-if="materialPrice(crop.id) !== null"> · 直售 {{ money(materialPrice(crop.id)!) }}／份</template></small></button></div>
      <footer><button v-if="picking && draft[picking]" :disabled="editLocked" @click="removeHerb">移除药材</button><span>没有足够药材？先回灵田收获。</span></footer>
    </dialog>
  </section>
</template>

<style scoped>
.alchemy-room-view { --room-ink:#483729;--room-muted:#79634e;--room-line:#bba17b;--room-paper:#f2e7d4;--room-accent:#8c432c;position:absolute;inset:0;z-index:60;display:flex;flex-direction:column;overflow:hidden;color:var(--room-ink);background:#392b21;isolation:isolate;color-scheme:light;font-size:13px; }
.alchemy-room-view::before { content:'';position:absolute;inset:0;z-index:-2;background:var(--room-art) center/cover no-repeat; }
.alchemy-room-view::after { content:'';position:absolute;inset:0;z-index:-1;background:linear-gradient(180deg,#271b13c9 0,transparent 28%,#35231955 100%);pointer-events:none; }
button { font:inherit;cursor:pointer;transition:background .15s,border-color .15s; }
button:disabled { cursor:default;opacity:.6; }button:focus-visible{outline:2px solid #bf713f;outline-offset:3px}
.room-heading { display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px 30px;flex:none;border-bottom:1px solid #ddc8a557;background:#2d2019a6; }
.room-back { color:#f2e3cb;background:#38271dce;border:1px solid #b3956f;padding:9px 16px;border-radius:4px; }.room-back:hover{background:#5b3d29}
.room-title{text-align:center}.room-title>span{font-size:10px;letter-spacing:3px;color:#d6c4a9}.room-title h2{font:32px KaiTi,STKaiti,serif;letter-spacing:8px;margin:2px 0 0;color:#f6e8ce}
.room-wallet{display:flex;align-items:center;gap:6px;font-size:12px;white-space:nowrap;color:#e4d5be}.room-wallet b{font-size:20px;color:#f7e3bb;font-weight:500;font-variant-numeric:tabular-nums}
.room-body{display:grid;grid-template-columns:minmax(0,1fr) 246px;gap:24px;padding:24px 30px;flex:1;min-height:0;overflow:auto;scrollbar-width:thin;scrollbar-color:#ac8b60 #423024}
.room-workspace{min-width:0;display:flex;flex-direction:column}.recipe-guide{display:flex;align-items:center;gap:20px;padding:12px 16px;background:#35261ded;border-block:1px solid #ae885c;flex:none}.section-caption{font:19px KaiTi,serif;color:#e2c59a;white-space:nowrap;writing-mode:vertical-rl;letter-spacing:4px}
.alchemy-recipes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;flex:1;min-width:0}.alchemy-recipe{min-width:0;display:flex;align-items:center;justify-content:center;gap:7px;padding:8px 6px;border:1px solid #a78b66;color:#e7d7be;background:#503b2c;border-radius:3px}.alchemy-recipe img{width:44px;height:44px;object-fit:contain}.alchemy-recipe strong{display:block;font:19px KaiTi,serif}.alchemy-recipe small{display:block;font-size:10px;color:#d2bfa1;margin-top:4px}.alchemy-recipe.active{border-color:#dbb179;background:#88462f;box-shadow:inset 0 0 0 2px #f6dab52b}.alchemy-recipe.active strong{color:#fff1d9}.alchemy-recipe.locked{opacity:.6}.alchemy-recipe:hover:not(:disabled){border-color:#f0d3a3;background:#684a34}.alchemy-recipe.active:hover:not(:disabled){background:#9b5236}
.room-workbench{display:grid;grid-template-columns:minmax(180px,1.15fr) minmax(270px,1fr);align-items:center;gap:12px;flex:1;min-height:340px;padding-block:24px}
.alchemy-stage{position:relative;isolation:isolate;min-width:0;height:100%;max-height:560px;min-height:300px;display:grid;place-items:center;pointer-events:none}.furnace-aura{position:absolute;bottom:9%;left:9%;right:9%;height:22%;border-radius:50%;background:radial-gradient(ellipse,#e8b56466,#cf9b5622 40%,transparent 70%);filter:blur(8px)}
.alchemy-furnace{position:absolute;left:50%;bottom:6%;width:100%;max-width:460px;height:90%;object-fit:contain;transform:translateX(-50%);filter:drop-shadow(0 15px 14px #26180e99)}
.furnace-idle-caption{position:absolute;bottom:0;text-align:center;text-shadow:0 2px 5px #24170f}.furnace-idle-caption span{display:block;font:20px KaiTi,serif;letter-spacing:5px;color:#f8e5c3}.furnace-idle-caption small{font-size:10px;color:#ddccb3}
.mixing-desk{min-width:0;width:100%;max-width:460px;justify-self:center;padding:20px 22px 12px;background:var(--room-paper);border:1px solid var(--room-line);box-shadow:0 12px 35px #21170f4d,inset 0 0 0 4px #c7a77833;border-radius:4px}.desk-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:14px}.desk-heading h3{font:22px KaiTi,serif;margin:0;letter-spacing:3px}.desk-heading>span{font-size:10px;color:var(--room-muted)}
.herb-slots{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.herb-slot-wrap{min-width:0;text-align:center}.herb-slot{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;aspect-ratio:1;width:100%;padding:4px;border:1px solid #a78a60;background:#e7d8bc;border-radius:3px;box-shadow:inset 0 0 0 4px #faf3e44d;color:#83542f;overflow:hidden}.herb-slot::after{content:'';position:absolute;inset:5px;border:1px solid #ac8d5d59;pointer-events:none}.herb-slot:hover:not(:disabled){background:#f7e9ce;border-color:#96502e}.herb-slot.filled{background:radial-gradient(ellipse,#faf0d9,#ddc49d);border-color:#a87a43}.herb-slot:disabled{opacity:.85}.herb-plus{font:38px/1 KaiTi,serif;font-weight:200;color:#986c42}.herb-slot small{font-size:10px;color:#79634b;margin-top:4px}.herb-slot :deep(.farm-plant){position:absolute;width:94%;height:85%;top:-1%;left:3%}.herb-quantity{position:absolute;top:4px;right:7px;font-size:12px;font-weight:600;color:#694027;text-shadow:0 1px 3px #fff6e1}.herb-name{position:absolute;bottom:5px;left:0;right:0;font-size:11px;color:#4a3020;text-shadow:0 1px 2px #fff9e6;background:#f5e6cde6}.slot-label{display:block;font:15px KaiTi,serif;letter-spacing:2px;margin-top:7px;color:#63452d}.slot-label small{font:10px system-ui;letter-spacing:0;color:#856b4e;margin-left:3px}
.quality-row,.fire-row{display:flex;align-items:center;justify-content:space-between;gap:9px;margin-top:14px}.quality-row>span,.fire-row>span{font-size:11px;color:var(--room-muted);white-space:nowrap}.alchemy-chips{display:flex;gap:5px}.alchemy-chips button{border:1px solid #bca17b;background:#fbf2e1;color:#715238;border-radius:3px;padding:5px 12px;font-size:11px;min-height:29px}.alchemy-chips button.active{color:#fff2dc;background:#8c432c;border-color:#763a28}.fire-row .alchemy-chips{flex:1}.fire-row .alchemy-chips button{flex:1}
.recipe-reading{display:flex;flex-wrap:wrap;gap:5px 10px;margin-top:14px;padding:12px 0;border-block:1px solid #b89b7366}.recipe-reading strong{font:17px KaiTi,serif;color:#7c4129;width:100%}.recipe-reading>span{font-size:11px;color:#594231}.recipe-reading small{font-size:10px;color:var(--room-muted)}.alchemy-odds{display:flex;justify-content:space-between;gap:6px;margin:14px 0 12px}.alchemy-odds span{font-size:11px;color:var(--room-muted)}.alchemy-odds b{font-weight:500;font-size:16px;color:#8b5a21;margin-left:4px}.alchemy-odds span:nth-child(2) b{color:#a0432e}.alchemy-odds span:nth-child(3) b{color:#77695b}
.room-primary{width:100%;min-height:44px;border:1px solid #74391f;border-radius:3px;background:#91492e;color:#fff0d5;box-shadow:inset 0 0 0 3px #ffe4b51c,0 3px 9px #3e25172e;font:18px KaiTi,serif;letter-spacing:2px}.room-primary:hover:not(:disabled){background:#a45a36}.room-primary:disabled{background:#d5c5ad;border-color:#b59b76;color:#75634f;opacity:1}.desk-note{text-align:center;font-size:10px;color:var(--room-muted);margin:8px 0 0}.room-rule{text-align:center;font-size:11px;line-height:1.7;margin:0;color:#eadbc3;text-shadow:0 1px 3px #2b1b10}
.alchemy-economics{min-height:42px;margin-top:6px;font-size:10px;line-height:14px;color:var(--room-muted)}.economy-values{display:flex;flex-wrap:wrap;justify-content:space-between;gap:0 5px}.economy-values b{font-weight:600;color:var(--room-ink);font-variant-numeric:tabular-nums}.alchemy-economics>small{display:block;font-size:9px}.alchemy-economics .economy-warning{color:var(--room-accent)}
.pill-chest{display:flex;flex-direction:column;min-width:0;min-height:0;border:1px solid var(--room-line);background:#ede0c9;padding:20px 16px 14px;box-shadow:inset 0 0 0 4px #c7a77833;border-radius:4px}.chest-heading{display:flex;align-items:baseline;justify-content:space-between;border-bottom:1px solid #b79a7066;padding-bottom:10px}.chest-heading h3{font:23px KaiTi,serif;letter-spacing:5px;margin:0}.chest-heading span{font-size:11px;color:#8a6236}.chest-subtitle{font-size:10px;color:var(--room-muted);margin:10px 0 16px}.pill-list{overflow:auto;min-height:0;flex:1;scrollbar-width:thin}.pill-item{padding-bottom:16px;margin-bottom:16px;border-bottom:1px solid #ac8d6755}.pill-title{display:flex;align-items:center;gap:6px}.pill-title img{height:48px;width:48px;object-fit:contain;flex:none}.pill-title h4{font:18px KaiTi,serif;color:#65412a;margin:0}.pill-title h4 small{font:10px system-ui;color:#8c6945}.pill-title p{font-size:10px;color:var(--room-muted);margin:6px 0}.pill-item button{display:block;width:100%;margin-top:8px;padding:7px 3px;font-size:11px;border:1px solid #b18c5e;border-radius:3px;background:#f8edd7;color:#774529}.pill-item button:hover:not(:disabled){background:#e7cda5}.chest-footer{font-size:9px;line-height:1.8;color:var(--room-muted);padding-top:12px}.chest-empty{text-align:center;font:18px/1.8 KaiTi,serif;color:#896b47;margin-top:45px}.chest-empty img{display:block;width:64px;height:64px;object-fit:contain;opacity:.5;margin:0 auto 12px}.chest-empty small{font:11px system-ui;color:var(--room-muted)}
.alchemy-progression{flex:none;min-width:0;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid #b79a7088;font-size:10px;line-height:1.6;overflow-wrap:anywhere}.alchemy-progression h3{font:20px KaiTi,STKaiti,serif;letter-spacing:2px;margin:0 0 10px;color:#65412a}.progression-level{display:flex;flex-wrap:wrap;justify-content:space-between;gap:2px 6px;align-items:baseline}.progression-level strong{font-size:13px;color:#75452a}.alchemy-progression p{margin:5px 0;color:var(--room-muted)}.alchemy-progression .progression-total{font-variant-numeric:tabular-nums;color:var(--room-ink)}.progression-bar{height:6px;background:#d6c3a4;border-radius:3px;overflow:hidden}.progression-bar i{display:block;height:100%;background:#648267}.alchemy-progression .progression-next{color:#65412a}.alchemy-progression .progression-source{color:#704629}.alchemy-progression .alchemy-batch-xp{padding-top:6px;margin-top:8px;border-top:1px solid #b79a7055;color:#75452a;font-weight:600}.alchemy-batch-xp small{display:block;font-size:9px;font-weight:400;color:var(--room-muted)}
/* Scroll the whole chest on short screens; progression must not squeeze pills to zero height. */
.pill-chest{overflow:auto;scrollbar-width:thin}.pill-chest>*{flex-shrink:0}.pill-list{flex-basis:auto;overflow:visible}
/* 始终保留同一张炉身原画，只动画炉火、烟气和光影，避免切换图集时改变尺寸或颜色。 */
.alchemy-fire{position:absolute;z-index:1;left:35%;bottom:7%;width:30%;height:12%;pointer-events:none}
.alchemy-fire i{position:absolute;bottom:0;width:36%;height:100%;border-radius:50%;background:radial-gradient(ellipse at 50% 82%,#fff5b8 0 16%,#ffb347 40%,#e2512a 66%,transparent 72%);filter:blur(2px);mix-blend-mode:screen;transform-origin:50% 100%;animation:flame .9s ease-in-out infinite alternate}
.alchemy-fire i:nth-child(1){left:0;animation-delay:-.3s}.alchemy-fire i:nth-child(2){left:32%;height:120%}.alchemy-fire i:nth-child(3){right:0;animation-delay:-.6s}
.alchemy-smoke{position:absolute;z-index:1;left:35%;top:2%;width:30%;height:35%}
.alchemy-smoke i{position:absolute;bottom:0;left:30%;width:35%;aspect-ratio:1;border-radius:50%;background:radial-gradient(circle,#f0e5d5b0,transparent 70%);animation:smoke 3.6s ease-out infinite}
.alchemy-smoke i:nth-child(2){animation-delay:-1.2s;left:10%}.alchemy-smoke i:nth-child(3){animation-delay:-2.4s;left:50%}
.alchemy-brewing .alchemy-furnace{animation:breathe 2.4s infinite}
.alchemy-ready .alchemy-furnace,.alchemy-success .alchemy-furnace,.alchemy-great_success .alchemy-furnace{filter:drop-shadow(0 0 20px #e7bc72aa)}
.alchemy-ready .alchemy-fire{opacity:.35}
.alchemy-explode .alchemy-furnace{animation:shake .4s 2;filter:drop-shadow(0 0 16px #ee773d)}
.alchemy-waste .alchemy-furnace{filter:drop-shadow(0 10px 12px #291b12)}
.alchemy-timer,.alchemy-result{position:absolute;z-index:4;top:0;left:50%;transform:translateX(-50%);width:max-content;max-width:100%;padding:9px 16px;background:#38271ef2;border:1px solid #c4a272;border-radius:4px;text-align:center;box-shadow:0 4px 20px #291a1255}.alchemy-timer strong{display:block;font:18px KaiTi,serif;color:#f7e2bc}.alchemy-timer span{font-size:11px;color:#ddc9a9;white-space:nowrap}.alchemy-timer-bar{height:4px;background:#211810;margin-top:6px}.alchemy-timer-bar i{display:block;height:100%;background:#d1a05e;transition:width 1s linear}.alchemy-result{display:flex;align-items:center;gap:8px;font:20px KaiTi,serif;color:#ffe5bb}.alchemy-result img{width:42px;height:42px;object-fit:contain}
.alchemy-great_success .alchemy-result{color:#fff0bf;border-color:#efc16c;background:#674321f2;box-shadow:0 0 20px #e7bc7266}
.room-alert,.room-notice{position:absolute;z-index:10;left:50%;top:82px;transform:translateX(-50%);max-width:85%;padding:10px 16px;background:#f5e7cef5;border:1px solid #b57d4c;color:#5f3e25;font-size:12px;box-shadow:0 4px 15px #2c1d1255;border-radius:4px;margin:0}.room-alert{display:flex;gap:12px;align-items:center}.room-alert button{color:#fff0d3;background:#8c432c;border:1px solid #743921;padding:6px 10px;white-space:nowrap}.room-notice{pointer-events:none;background:#4e3324f5;color:#f5e1be}.room-unavailable{margin:auto;padding:30px;text-align:center;background:var(--room-paper);line-height:1.8}
.herb-picker{width:560px;max-width:calc(100% - 28px);max-height:calc(100% - 28px);padding:22px;border:1px solid #ad8859;border-radius:6px;background:#f2e7d4;color:var(--room-ink);box-shadow:0 20px 80px #26170faa;overflow:auto}.herb-picker::backdrop{background:#20150fb8;backdrop-filter:blur(4px)}.herb-picker header{display:flex;align-items:center;justify-content:space-between;gap:12px}.herb-picker h3{font:25px KaiTi,serif;margin:4px 0;letter-spacing:3px}.herb-picker header small{font-size:10px;color:var(--room-muted)}.herb-picker header button,.herb-picker footer button{padding:7px 12px;border:1px solid #b7976e;background:#e4cfaa;color:#6b4127;border-radius:3px}.herb-picker>p{font-size:11px;line-height:1.8;color:var(--room-muted)}.herb-options{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:16px}.herb-option{min-width:0;display:flex;flex-direction:column;align-items:center;padding:8px;border:1px solid #b69a72;background:#faf0dc;color:#63412a;border-radius:3px}.herb-option :deep(.farm-plant){width:90px;max-width:100%;height:78px}.herb-option strong{font:17px KaiTi,serif}.herb-option span,.herb-option small{font-size:10px;color:var(--room-muted);margin-top:4px}.herb-option:not(:disabled):hover{background:#ead3af;border-color:#975833}.herb-picker footer{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:18px}.herb-picker footer>span{font-size:10px;color:var(--room-muted)}
@keyframes flame{to{transform:scaleY(1.15) scaleX(.9);opacity:.8}}@keyframes smoke{0%{opacity:0;transform:translateY(0) scale(.6)}25%{opacity:.8}100%{opacity:0;transform:translateY(-160%) scale(1.9)}}@keyframes breathe{50%{filter:drop-shadow(0 0 14px #f3b756aa)}}@keyframes shake{25%{transform:translateX(calc(-50% - 5px)) rotate(-1deg)}75%{transform:translateX(calc(-50% + 5px)) rotate(1deg)}}
@container (max-width:1100px){.room-heading{padding:13px 20px}.room-title h2{font-size:27px}.room-body{grid-template-columns:minmax(0,1fr) 195px;gap:14px;padding:18px 20px}.recipe-guide{padding:8px;gap:8px}.alchemy-recipes{gap:6px}.alchemy-recipe img{width:34px;height:34px}.alchemy-recipe strong{font-size:17px}.room-workbench{grid-template-columns:minmax(155px,1fr) minmax(260px,1.2fr);gap:8px;padding-block:16px}.mixing-desk{padding:14px 14px 10px}.herb-slots{gap:8px}.pill-chest{padding:14px 10px}.pill-title{gap:2px}.pill-title img{width:34px;height:34px}.pill-title h4{font-size:16px}.pill-title p{font-size:9px}.alchemy-stage{min-height:260px}.desk-heading h3{font-size:19px}.alchemy-chips button{padding:4px 10px}}
@container (max-width:700px){.room-body{grid-template-columns:minmax(0,1fr);padding:14px;gap:16px}.pill-chest{min-height:170px}.pill-list{overflow:visible}.room-workbench{grid-template-columns:minmax(150px,.85fr) minmax(235px,1fr)}.chest-subtitle{margin-bottom:5px}.chest-empty{margin:8px}.chest-empty img{display:none}.chest-footer{padding-top:0}.room-heading{padding:12px}.room-title>span{display:none}.room-title h2{font-size:24px;letter-spacing:4px}.room-back{padding:7px 9px;font-size:11px}.room-wallet{font-size:10px;gap:3px}.room-wallet b{font-size:16px}.room-alert,.room-notice{top:60px}.room-rule{font-size:10px}}
@container (max-width:480px){.room-body{padding:10px 12px}.recipe-guide{gap:5px;padding:7px 4px}.section-caption{display:none}.alchemy-recipes{gap:5px}.alchemy-recipe{gap:1px;padding:6px 2px}.alchemy-recipe img{width:26px;height:26px}.alchemy-recipe strong{font-size:16px}.alchemy-recipe small{font-size:8px;margin-top:2px}.room-workbench{grid-template-columns:1fr;padding:8px 0 12px;gap:8px;min-height:0}.alchemy-stage{height:180px;min-height:180px;max-height:180px}.alchemy-furnace{width:210px;height:95%;bottom:3%}.furnace-aura{left:20%;right:20%}.furnace-idle-caption{left:0;bottom:20%;text-align:left;width:80px}.furnace-idle-caption span{font-size:15px;letter-spacing:2px}.furnace-idle-caption small{font-size:9px}.mixing-desk{padding:12px 16px 9px;max-width:100%}.desk-heading{margin-bottom:10px}.herb-slots{max-width:280px;margin:auto;gap:14px}.quality-row,.fire-row{margin-top:10px}.recipe-reading{margin-top:10px;padding:9px 0}.alchemy-odds{margin:10px 0}.room-primary{min-height:40px;font-size:16px}.alchemy-timer{top:0;left:0;transform:none;padding:7px;max-width:155px}.alchemy-timer strong{font-size:15px}.alchemy-timer span{font-size:9px}.alchemy-result{top:0;left:0;transform:none;font-size:16px;padding:5px}.alchemy-result img{width:30px;height:30px}.alchemy-fire{left:42%;width:20%;height:15%}.alchemy-smoke{left:40%;width:20%}.herb-options{grid-template-columns:repeat(2,minmax(0,1fr))}.herb-picker{padding:18px}}
@media(max-height:700px) and (min-width:701px){.room-body{padding:12px 18px}.room-workbench{padding-block:8px;min-height:0}.mixing-desk{padding:10px 14px}.desk-heading{margin-bottom:8px}.herb-slots{max-width:225px;margin:auto}.quality-row,.fire-row{margin-top:8px}.recipe-reading{margin-top:8px;padding-block:7px}.alchemy-odds{margin:8px 0}.room-primary{min-height:36px}.alchemy-stage{min-height:240px;max-height:360px}.room-rule{font-size:10px}}
@media(max-height:500px) and (min-width:701px){.room-heading{padding:7px 15px}.room-title>span{display:none}.room-title h2{font-size:23px}.room-back{padding:6px 9px}.room-body{grid-template-columns:minmax(0,1fr) 170px;padding:8px 14px;gap:12px}.recipe-guide{padding:5px 8px}.section-caption{font-size:15px}.alchemy-recipe{padding:3px}.alchemy-recipe img{width:26px;height:26px}.alchemy-recipe strong{font-size:15px}.alchemy-recipe small{font-size:9px;margin-top:0}.room-workbench{grid-template-columns:minmax(140px,1fr) minmax(260px,1.25fr);min-height:245px;padding:7px 0;gap:6px}.alchemy-stage{min-height:220px}.mixing-desk{padding:8px 12px}.desk-heading{margin-bottom:6px}.desk-heading h3{font-size:16px}.desk-heading>span{font-size:9px}.herb-slots{max-width:224px;margin:auto;gap:12px}.herb-slot{max-height:62px}.herb-plus{font-size:28px}.herb-slot small{font-size:8px;margin-top:0}.slot-label{font-size:12px;margin-top:2px}.quality-row,.fire-row{margin-top:5px}.alchemy-chips button{min-height:24px;padding:2px 10px;font-size:10px}.recipe-reading{margin-top:5px;padding:5px 0;gap:3px 8px}.recipe-reading strong{font-size:14px;width:auto}.recipe-reading>span{font-size:9px}.recipe-reading small{font-size:9px}.alchemy-odds{margin:5px 0}.alchemy-odds span{font-size:10px}.alchemy-odds b{font-size:13px}.room-primary{min-height:31px;font-size:15px}.desk-note{margin-top:4px;font-size:8px}.room-rule{display:none}.alchemy-timer{padding:6px 8px}.alchemy-timer strong{font-size:15px}.alchemy-timer span{font-size:10px}.pill-chest{padding:10px}.chest-heading h3{font-size:20px}.chest-subtitle{font-size:9px;margin:7px 0}.room-alert,.room-notice{top:50px}}
/* 横屏也以宽度决定方格边长，避免 max-height 把药材格压成长方形。 */
@media(max-height:500px) and (min-width:701px){.herb-slots{max-width:192px}.herb-slot{max-height:none}.room-workbench{padding-block:3px;min-height:0}.mixing-desk{padding:6px 10px}.desk-note{display:none}.alchemy-stage{height:240px;min-height:240px;max-height:240px}.recipe-reading{padding-block:3px}.quality-row,.fire-row{margin-top:3px}}
@media(max-height:500px) and (min-width:701px){.alchemy-economics{margin-top:3px;font-size:9px;line-height:12px;min-height:36px}.alchemy-economics>small{font-size:8px}.alchemy-yield{white-space:nowrap}}
.room-paused :deep(*){animation-play-state:paused!important}@media(prefers-reduced-motion:reduce){.alchemy-room-view :deep(*){animation:none!important;transition:none!important}}
</style>
