import { gameVoiceIds } from './gameVoiceLines';

export type TableVoiceAction = {
  user_id?: unknown; action?: unknown; bid?: unknown; cards?: unknown; tile?: unknown;
  kind?: unknown; combo?: unknown;
};
type PlayedCards = { user_id?: unknown; cards?: unknown; kind?: unknown };
export type TableVoiceState = {
  last_play?: PlayedCards | string[] | null;
  win_events?: { user_id: string; kind: string }[];
};

const rankKeys = ['3', '4', '5', '6', '7', '8', '9', '10', 'j', 'q', 'k', 'a', '2', 'joker_small', 'joker_big'];
const kindVoices: Record<string, string> = {
  pair: 'cards_pair', triple: 'cards_triple', triple_single: 'cards_triple_single',
  triple_pair: 'cards_triple_pair', full_house: 'cards_triple_pair', straight: 'cards_straight',
  pair_straight: 'cards_pair_straight', airplane: 'cards_airplane',
  airplane_single: 'cards_airplane_single', airplane_pair: 'cards_airplane_pair',
  four_two_single: 'cards_four_two_single', four_two_pair: 'cards_four_two_pair',
  triple_straight: 'cards_steel_plate', straight_flush: 'cards_straight_flush', bomb: 'bomb', rocket: 'rocket',
};

function cardRank(card: unknown): string | null {
  if (typeof card !== 'string') return null;
  // 掼蛋只接受两副牌的公开副本标记，避免把任意字符串当作牌名。
  const face = card.replace(/#[01]$/, '');
  if (face === 'JokerSmall') return 'joker_small';
  if (face === 'JokerBig') return 'joker_big';
  return /^(?:Club|Diamond|Heart|Spade)([2-9]|10|J|Q|K|A)$/.exec(face)?.[1]?.toLowerCase() ?? null;
}

export function samePlayedCards(left: unknown, right: unknown): boolean {
  if (!Array.isArray(left) || !Array.isArray(right) || !left.length || left.length !== right.length
    || !left.every(card => typeof card === 'string') || !right.every(card => typeof card === 'string')) return false;
  const sortedRight = [...right].sort();
  return [...left].sort().every((card, index) => card === sortedRight[index]);
}

function landlordKind(ranks: string[]): string | undefined {
  const counts = new Map<number, number>();
  for (const rank of ranks) {
    const value = rankKeys.indexOf(rank) + 3;
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  const values = [...counts.keys()].sort((a, b) => a - b);
  const sizes = [...counts.values()].sort((a, b) => a - b);
  const size = ranks.length;
  const consecutive = values[values.length - 1]! <= 14 && values.every((value, index) => value === values[0]! + index);
  if (size === 2 && values.join(',') === '16,17') return 'rocket';
  if (counts.size === 1) return ({ 1: 'single', 2: 'pair', 3: 'triple', 4: 'bomb' } as Record<number, string>)[size];
  if (size === 4 && sizes.join(',') === '1,3') return 'triple_single';
  if (size === 5 && sizes.join(',') === '2,3') return 'triple_pair';
  if (size >= 5 && consecutive && sizes.every(count => count === 1)) return 'straight';
  if (size >= 6 && consecutive && sizes.every(count => count === 2)) return 'pair_straight';
  if (size >= 6 && consecutive && sizes.every(count => count === 3)) return 'airplane';
  for (const [unit, wings, kind] of [[4, 1, 'airplane_single'], [5, 2, 'airplane_pair']] as const) {
    const chain = size / unit;
    if (!Number.isInteger(chain) || chain < 2) continue;
    for (let low = 3; low + chain - 1 <= 14; low++) {
      const core = Array.from({ length: chain }, (_, index) => low + index);
      const rest = [...counts].filter(([value]) => !core.includes(value));
      if (core.every(value => counts.get(value) === 3) && rest.length === chain && rest.every(([, count]) => count === wings)) return kind;
    }
  }
  if (size === 6 && sizes.includes(4)) return 'four_two_single';
  if (size === 8 && sizes.join(',') === '2,2,4') return 'four_two_pair';
  return undefined;
}

function knownVoice(id: string): string | null {
  return gameVoiceIds.has(id) ? id : null;
}

/** 只读取公开动作，或服务端确认成功的本人动作；不读取任何玩家手牌。 */
export function tableActionVoice(gameType: string, action: TableVoiceAction, state?: TableVoiceState | null): string | null {
  const actionName = typeof action.action === 'string' ? action.action : '';
  if (actionName === 'bid') return action.bid === 0 ? 'no_bid' : 'bid';
  if (gameType === 'mahjong' || gameType === 'sichuan_mahjong') {
    if (actionName === 'discard') {
      return typeof action.tile === 'string' && /^(?:[mps][1-9]|z[1-7])$/.test(action.tile)
        ? knownVoice(`tile_${action.tile}`) : 'discard';
    }
    // 血战每位玩家只能胡一次，当前公开胡牌事件能够明确区分自摸。
    if (actionName === 'win' && state?.win_events?.some(event => String(event.user_id) === String(action.user_id) && event.kind === 'self_draw')) return 'self_draw';
    return knownVoice(actionName);
  }
  if (actionName !== 'play' || !['landlord', 'guandan'].includes(gameType)) return knownVoice(actionName);
  const cards = action.cards;
  if (!Array.isArray(cards) || !cards.length || !cards.every(card => typeof card === 'string') || new Set(cards).size !== cards.length) return 'play';
  const parsed = cards.map(cardRank);
  if (parsed.some(rank => rank === null)) return 'play';
  const ranks = parsed as string[];
  if (cards.length === 1) return knownVoice(`card_${ranks[0]}`) ?? 'play';
  const last = state?.last_play;
  // 同一玩家上一轮的 last_play 不能用于本次动作，必须连出牌内容也匹配。
  const matchingLast = last && !Array.isArray(last) && String(last.user_id) === String(action.user_id)
    && samePlayedCards(last.cards, cards) ? last : null;
  const comboKind = typeof action.combo === 'string' ? /^([a-z_]+):\d+:\d+:/.exec(action.combo)?.[1] : undefined;
  let kind = typeof action.kind === 'string' ? action.kind : comboKind ?? matchingLast?.kind;
  if (typeof kind !== 'string') {
    if (gameType === 'landlord') kind = landlordKind(ranks);
    else if (ranks.every(rank => rank === ranks[0])) kind = cards.length === 2 ? 'pair' : cards.length === 3 ? 'triple' : cards.length >= 4 ? 'bomb' : undefined;
    else if (cards.length === 4 && ranks.filter(rank => rank === 'joker_small').length === 2 && ranks.filter(rank => rank === 'joker_big').length === 2) kind = 'rocket';
    // 成功提交的两张混合点数只能确定是通配对子，不擅自猜测声明点数。
    else if (cards.length === 2) kind = 'pair';
  }
  if (kind === 'pair' && cards.length === 2 && ranks[0] === ranks[1]) return knownVoice(`pair_${ranks[0]}`) ?? 'cards_pair';
  if (gameType === 'guandan' && kind === 'pair_straight') return 'cards_three_pairs';
  if (gameType === 'guandan' && kind === 'rocket') return 'cards_four_jokers';
  return typeof kind === 'string' ? knownVoice(kindVoices[kind] ?? 'play') : 'play';
}
