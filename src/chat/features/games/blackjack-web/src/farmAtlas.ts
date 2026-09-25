import type { FarmAction } from './farmTypes';

export const FARM_ART = '/ui/farm-v2';
export type FarmAnimation = 'water' | 'growth' | 'harvest' | 'fox-idle' | 'hound-idle' | 'crane-idle';
export const FARM_ATLASES: Record<FarmAnimation, { src: string; frames: number; duration: number }> = {
  water: { src: `${FARM_ART}/animations/water.webp`, frames: 8, duration: 1800 },
  growth: { src: `${FARM_ART}/animations/growth.webp`, frames: 8, duration: 1900 },
  harvest: { src: `${FARM_ART}/animations/harvest.webp`, frames: 8, duration: 1700 },
  'fox-idle': { src: `${FARM_ART}/animations/fox-idle.webp`, frames: 8, duration: 1800 },
  'hound-idle': { src: `${FARM_ART}/animations/hound-idle.webp`, frames: 8, duration: 2100 },
  'crane-idle': { src: `${FARM_ART}/animations/crane-idle.webp`, frames: 8, duration: 2400 },
};
export function petArt(id: string) { return `${FARM_ART}/pets/${id}.webp`; }
export function itemArt(id: string) { return `${FARM_ART}/items/${id}.webp`; }
export function petAnimation(id: string): FarmAnimation {
  return id === 'mountain_hound' ? 'hound-idle' : id === 'dew_crane' ? 'crane-idle' : 'fox-idle';
}
export function actionAnimation(action: FarmAction): FarmAnimation | undefined {
  if (action === 'water') return 'water';
  if (action === 'plant') return 'growth';
  if (action === 'harvest' || action === 'steal') return 'harvest';
  return undefined;
}
