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
export type FarmAction = 'buy_seed' | 'plant' | 'water' | 'pest' | 'harvest' | 'sell' | 'expand' | 'upgrade_aura' | 'steal' | 'buy_pet' | 'equip_pet' | 'buy_item' | 'use_item';
export type FarmActionBody = {
  action: FarmAction; request_id: string; plot_id?: number; crop_id?: string; quantity?: number;
  quality?: CropQuality; target_user_id?: string; pet_id?: string; item_id?: string;
};
export type FarmVisit = { user_id: string; username: string; avatar_url: string; level: number; mature_plots: number; protected_until?: number; stats?: { guarded: number; caught: number; [key: string]: number } | null };
export type FarmState = {
  server_time: number; is_owner: boolean; owner: { user_id: string; username: string; avatar_url: string };
  farm: { level: number; xp: number; next_level_xp: number | null; unlocked_plots: number; aura_level: number; growth_multiplier: number; mutation_bonus: number; created_at: number; protected_until?: number; stats?: { guarded: number; caught: number; [key: string]: number } | null };
  balance: number | null; plots: FarmPlot[]; guardian?: FarmGuardian | null; pets?: OwnedFarmPet[];
  inventory: { seeds: { crop_id: string; quantity: number }[]; produce: FarmProduce[]; items?: { item_id: string; quantity: number }[] };
  catalog: {
    crops: FarmCrop[]; pets?: FarmPet[]; items?: FarmItem[];
    land_levels: { plot_count: number; required_level: number; cost: number }[];
    aura_levels: { level: number; required_level: number; cost: number; growth_multiplier: number; mutation_bonus: number }[];
    rules: Record<string, unknown>;
  };
  activity: { action: string; message?: string; created_at?: number; timestamp?: number }[];
  result?: { action: FarmAction; message: string; balance?: number; quality?: CropQuality; caught?: boolean; pet_id?: string; item_id?: string; quantity?: number; crop_id?: string };
};
