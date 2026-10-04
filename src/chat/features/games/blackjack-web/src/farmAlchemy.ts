import type { CropQuality, FarmAlchemyCatalog, FarmRecipe, FireLevel, PillGrade } from './farmTypes';

const roundOdds = (value: number) => Math.round(value * 10000) / 10000;

// Catalog multipliers are decimal ratios. Integer remainder comparison preserves
// Python's round-to-even price contract (Math.round would round .5 upward).
export function pillSalePrice(catalog: FarmAlchemyCatalog, pillId: string, grade: PillGrade): number {
  const pill = catalog.pills.find(item => item.id === pillId);
  const multiplier = catalog.grades.find(item => item.id === grade)?.multiplier;
  if (!pill || multiplier === undefined) return 0;
  const [whole, fraction = ''] = String(multiplier).split('.');
  const denominator = 10 ** fraction.length;
  const product = pill.sale_price * Number(whole + fraction);
  const floor = Math.floor(product / denominator), remainder = product % denominator;
  return Math.max(1, floor + Number(remainder * 2 > denominator || (remainder * 2 === denominator && floor % 2 === 1)));
}

export function alchemyOdds(catalog: FarmAlchemyCatalog, recipe: FarmRecipe, fire: FireLevel, catalyst: string) {
  const rank = (id: FireLevel) => catalog.fires.find(item => item.id === id)?.rank ?? 0;
  const base = catalog.rules.fire_odds[Math.min(Math.abs(rank(fire) - rank(recipe.fire)), catalog.rules.fire_odds.length - 1)]!;
  const balance = recipe.heat + (catalog.herb_natures[catalyst] ?? 0), gap = Math.abs(balance);
  const success = roundOdds(Math.min(.95, Math.max(.05, base.success + (gap === 0 ? catalog.rules.balanced_success_bonus : -catalog.rules.imbalance_success_penalty * gap))));
  const explode = roundOdds(Math.min(1 - success, Math.min(.9, Math.max(.01, base.explode + catalog.rules.imbalance_explode_penalty * gap))));
  return { success, explode, waste: roundOdds(1 - success - explode), balance };
}

export function alchemyGradeOdds(catalog: FarmAlchemyCatalog, quality: CropQuality, balanced: boolean) {
  const base = catalog.rules.grade_odds[quality];
  const high = roundOdds(Math.min(1, base.high + (balanced ? catalog.rules.balanced_high_bonus[quality] : 0)));
  const mid = roundOdds(Math.min(base.mid, 1 - high));
  return { high, mid, low: roundOdds(1 - high - mid) };
}

// Recipe outcomes are base quantities. Active furnaces already contain this bonus:
// render their sealed snapshot directly instead of passing it through this helper.
export function alchemyYieldOutcomes(recipe: FarmRecipe, bonusPills = 0) {
  return recipe.yield_outcomes.map(item => ({ ...item, quantity: item.quantity + bonusPills }));
}

// Expected sale proceeds, never an assertion about the sealed batch outcome.
export function alchemyRevenue(catalog: FarmAlchemyCatalog, recipe: FarmRecipe, fire: FireLevel, quality: CropQuality, catalyst: string, bonusPills = 0) {
  const odds = alchemyOdds(catalog, recipe, fire, catalyst);
  const grades = alchemyGradeOdds(catalog, quality, odds.balance === 0);
  const successfulSale = (['low', 'mid', 'high'] as const).reduce((sum, grade) => sum + grades[grade] * pillSalePrice(catalog, recipe.pill_id, grade), 0);
  const expectedYield = alchemyYieldOutcomes(recipe, bonusPills).reduce((sum, item) => sum + item.quantity * item.probability, 0);
  return { odds, grades, expectedYield, revenue: odds.success * expectedYield * successfulSale + odds.waste * pillSalePrice(catalog, 'waste', 'low') };
}
