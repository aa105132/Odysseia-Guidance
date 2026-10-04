export type FarmApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
export type FarmProfile = { user_id: string; username: string; avatar_url: string; balance: number };
export type CropQuality = 'normal' | 'spirit' | 'celestial';
export type FarmCrop = {
  id: string; name: string; tier: string | number; unlock_level: number; grow_seconds: number;
  seed_price: number; sale_price: number; base_yield: number; xp: number;
  description: string; source_url: string; icon_key: string;
};
export type FarmPlot = {
  plot_id: number; status: 'empty' | 'growing' | 'mature'; crop_id: string | null; crop_name: string | null;
  planted_at: number | null; mature_at: number | null; progress: number; watered: boolean;
  has_pest: boolean; pest_cleared: boolean; quality: CropQuality | null; yield_total: number;
  yield_remaining: number; stolen_count: number; can_steal: boolean; mutation_chance: number;
  dew_used?: boolean; ward_used?: boolean; ward_until?: number | null; ward_active?: boolean; theft_attempted?: boolean;
};
export type FarmProduce = { crop_id: string; quality: CropQuality; quantity: number; unit_price: number };
export type FarmPet = { id: string; name: string; price: number; unlock_level: number; guard_chance: number; water_bonus: number; icon_key: string; description: string };
export type FarmItem = { id: string; name: string; price: number; unlock_level: number; icon_key: string; description: string };
export type OwnedFarmPet = { pet_id: string; equipped: boolean; guard_until: number; remaining_seconds: number; active: boolean };
export type FarmGuardian = { pet_id: string; name: string; guard_chance: number; water_bonus: number; active: boolean; guard_until: number; remaining_seconds: number };
export type FarmAction = 'buy_seed' | 'plant' | 'water' | 'pest' | 'harvest' | 'sell' | 'expand' | 'upgrade_aura' | 'steal' | 'buy_pet' | 'equip_pet' | 'buy_item' | 'use_item' | 'refine' | 'collect_pill' | 'sell_pill';
export type PillGrade = 'low' | 'mid' | 'high';
export type FireLevel = 'wen' | 'zhong' | 'wu';
export type RefineOutcome = 'success' | 'great_success' | 'explode' | 'waste';
// Probabilities are conditional on successful refining; grade is drawn independently.
export type FarmYieldOutcome = { outcome: 'success' | 'great_success'; quantity: number; probability: number };
export type FarmRecipe = { id: string; pill_id: string; name: string; unlock_level: number; fire: FireLevel; materials: Record<string, number>; heat: number; refine_seconds: number; alchemy_xp?: number; yield_outcomes: FarmYieldOutcome[]; note: string };
export type FarmPill = { id: string; name: string; icon_key: string; sale_price: number; description: string };
export type FarmAlchemyRules = {
  fire_odds: { success: number; explode: number }[]; balanced_success_bonus: number; imbalance_success_penalty: number;
  imbalance_explode_penalty: number; grade_odds: Record<CropQuality, { high: number; mid: number }>; balanced_high_bonus: Record<CropQuality, number>;
  note: string;
};
export type FarmAlchemyLevel = { level: number; name: string; required_xp: number; duration_percent: number; bonus_pills?: number };
export type FarmAlchemyProgress = { xp: number; level: number; name: string; level_xp: number; next_level_xp: number | null; max_level: number; duration_percent: number; bonus_pills?: number };
export type FarmAlchemyCatalog = {
  recipes: FarmRecipe[]; pills: FarmPill[]; fires: { id: FireLevel; name: string; rank: number; description: string }[];
  grades: { id: PillGrade; name: string; multiplier: number }[]; herb_natures: Record<string, number>;
  nature_names: Record<string, string>; rules: FarmAlchemyRules;
  progression?: { levels: FarmAlchemyLevel[]; note: string };
};
export type OwnedPill = { pill_id: string; grade: PillGrade; quantity: number; unit_price: number };
// New batches expose their already-enhanced snapshotted distribution, never the sealed result.
// Without that distribution, use the legacy fixed capacity (missing means one); never add today's bonus.
export type FarmFurnace = { recipe_id: string; fire: FireLevel; quality: CropQuality; catalyst: string | null; started_at: number; ready_at: number; remaining_seconds: number; ready: boolean; furnace_level?: number; alchemy_xp?: number; bonus_pills?: number } & (
  { yield_outcomes: FarmYieldOutcome[]; yield_quantity?: never } | { yield_outcomes?: never; yield_quantity?: number }
);
export type FarmActionBody = {
  action: FarmAction; request_id: string; plot_id?: number; crop_id?: string; quantity?: number;
  quality?: CropQuality; target_user_id?: string; pet_id?: string; item_id?: string;
  recipe_id?: string; fire?: FireLevel; pill_id?: string; grade?: PillGrade;
};
export type FarmVisit = { user_id: string; username: string; avatar_url: string; level: number; mature_plots: number; protected_until?: number; stats?: { guarded: number; caught: number; [key: string]: number } | null };
export type FarmState = {
  server_time: number; is_owner: boolean; owner: { user_id: string; username: string; avatar_url: string };
  farm: { level: number; xp: number; next_level_xp: number | null; unlocked_plots: number; aura_level: number; growth_multiplier: number; mutation_bonus: number; created_at: number; protected_until?: number; stats?: { guarded: number; caught: number; [key: string]: number } | null };
  balance: number | null; plots: FarmPlot[]; guardian?: FarmGuardian | null; pets?: OwnedFarmPet[]; furnace?: FarmFurnace | null;
  alchemy?: FarmAlchemyProgress | null;
  inventory: { seeds: { crop_id: string; quantity: number }[]; produce: FarmProduce[]; items?: { item_id: string; quantity: number }[]; pills?: OwnedPill[] };
  catalog: {
    crops: FarmCrop[]; pets?: FarmPet[]; items?: FarmItem[]; alchemy?: FarmAlchemyCatalog;
    land_levels: { plot_count: number; required_level: number; cost: number }[];
    aura_levels: { level: number; required_level: number; cost: number; growth_multiplier: number; mutation_bonus: number }[];
    rules: Record<string, unknown>;
  };
  activity: { action: string; message?: string; created_at?: number; timestamp?: number }[];
  result?: {
    action: FarmAction; message: string; balance?: number; quality?: CropQuality; caught?: boolean; pet_id?: string; item_id?: string; quantity?: number; crop_id?: string;
    outcome?: RefineOutcome; pill_id?: string; grade?: PillGrade; heat?: number; xp_gained?: number; coins?: number; replayed?: boolean;
    ready_at?: number; refine_seconds?: number; yield_outcomes?: FarmYieldOutcome[];
    furnace_level?: number; alchemy_xp?: number;
    alchemy_xp_gained?: number; alchemy_level_before?: number; alchemy_level?: number; alchemy_leveled_up?: boolean;
  };
};
