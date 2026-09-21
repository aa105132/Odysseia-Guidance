export type RoomInvite = { room_id: string; game_type: string };
const games = new Set(['blackjack', 'texas', 'golden_flower', 'landlord', 'mahjong', 'sichuan_mahjong']);

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
