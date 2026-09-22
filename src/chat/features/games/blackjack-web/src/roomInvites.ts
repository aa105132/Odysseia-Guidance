import { tableGameRules, type TableRoomGameType } from './tableGameRules';

export type RoomInvite = { room_id: string; game_type: string; player_count?: number; max_players?: number; bot_count?: number; state?: string };
const games = new Set(['blackjack', 'texas', 'golden_flower', 'landlord', 'mahjong', 'sichuan_mahjong', 'guandan']);

export function parseRoomInvite(value: string | null): RoomInvite | null {
  const match = /^room:([a-z_]+):([A-Z0-9]{6})$/.exec(value ?? '');
  return match && games.has(match[1]!) ? { game_type: match[1]!, room_id: match[2]! } : null;
}

export function roomInviteCode(room: RoomInvite): string {
  const code = `room:${room.game_type}:${room.room_id}`;
  if (!parseRoomInvite(code)) throw new Error('房间信息无效，无法创建邀请');
  return code;
}

export function roomInviteUrl(clientId: string, room: RoomInvite): string {
  if (!/^\d{17,20}$/.test(clientId)) throw new Error('暂时无法获取 Discord 活动信息，请复制房间号邀请');
  return `https://discord.com/activities/${clientId}?custom_id=${encodeURIComponent(roomInviteCode(room))}`;
}

export function roomInviteDescription(room: RoomInvite): string {
  const title = room.game_type === 'blackjack' ? '多人 21 点' : tableGameRules[room.game_type as TableRoomGameType]?.title ?? '桌游';
  const gameplay: Record<string, string> = {
    blackjack: '与月月荷官比点数，接近 21 点且不能爆牌',
    texas: '两张底牌搭配公共牌，下注争夺底池',
    golden_flower: '三张牌比大小，可闷牌、看牌和比牌',
    landlord: '三人斗地主，一名地主对两名农民',
    mahjong: '四人麻将，吃碰杠胡',
    sichuan_mahjong: '四川血战麻将，定缺后血战到底',
    guandan: '四人两队，双副牌对家配合，从 2 打到 A',
  };
  const count = Number.isInteger(room.player_count) && Number.isInteger(room.max_players)
    ? `当前 ${room.player_count}/${room.max_players} 人${room.bot_count ? `（含 ${room.bot_count} 位月月 AI）` : ''}` : '';
  const status = room.state === 'playing' || room.state === 'dealer_turn' ? '对局中，结束后可加入' : room.player_count === room.max_players && room.max_players ? '房间已满' : '等待开局，欢迎入座';
  return [`月月茶楼 · ${title}`, gameplay[room.game_type], `房间号：${room.room_id}`, count, status].filter(Boolean).join('\n');
}
