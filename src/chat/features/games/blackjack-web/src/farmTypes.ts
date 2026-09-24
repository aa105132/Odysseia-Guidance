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
};
export type FarmProduce = { crop_id: string; quality: CropQuality; quantity: number; unit_price: number };
export type FarmAction = 'buy_seed' | 'plant' | 'water' | 'pest' | 'harvest' | 'sell' | 'expand' | 'upgrade_aura' | 'steal';
export type FarmActionBody = {
  action: FarmAction; request_id: string; plot_id?: number; crop_id?: string; quantity?: number;
  quality?: CropQuality; target_user_id?: string;
};
export type FarmVisit = { user_id: string; username: string; avatar_url: string; level: number; mature_plots: number; protected_until?: number };
export type FarmState = {
  server_time: number; is_owner: boolean; owner: { user_id: string; username: string; avatar_url: string };
  farm: { level: number; xp: number; next_level_xp: number | null; unlocked_plots: number; aura_level: number; growth_multiplier: number; mutation_bonus: number; created_at: number; protected_until?: number };
  balance: number | null; plots: FarmPlot[];
  inventory: { seeds: { crop_id: string; quantity: number }[]; produce: FarmProduce[] };
  catalog: {
    crops: FarmCrop[];
    land_levels: { plot_count: number; required_level: number; cost: number }[];
    aura_levels: { level: number; required_level: number; cost: number; growth_multiplier: number; mutation_bonus: number }[];
    rules: Record<string, unknown>;
  };
  activity: { action: string; message?: string; created_at?: number; timestamp?: number }[];
  result?: { action: FarmAction; message: string; balance?: number; quality?: CropQuality };
};
