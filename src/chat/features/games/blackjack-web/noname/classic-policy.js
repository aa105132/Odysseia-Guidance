// 适配层的经典预设：普通身份 + 标准武将 + 标准牌，不修改上游技能/胜负规则。
export function restrictClassicPackage(pack, online) {
  pack.character = { standard: pack.character.standard };
  pack.card = { standard: pack.card.standard };
  pack.mode = { identity: pack.mode.identity, ...(online ? { connect: pack.mode.connect } : {}) };
  pack.submode = { identity: { normal: '经典身份' } };
  pack.play = {};
  pack.background = {};
  pack.music = {};
  pack.font = { xiaozhuan: pack.font?.xiaozhuan || '方正小篆体' };
}

export function isClassicRoom(config) {
  const standardOnly = value => Array.isArray(value) && value.length === 1 && value[0] === 'standard';
  return Boolean(config && config.mode === 'identity' && config.identity_mode === 'normal'
    && standardOnly(config.characterPack) && standardOnly(config.cardPack));
}

export function configureClassic(lib, game, storage = localStorage) {
  // saveConfig 同时更新内存与 IndexedDB；约束菜单写入，重启后也不会恢复扩展预设。
  const saveConfig = game.saveConfig.bind(game);
  game.saveConfig = (key, value, local, callback) => {
    const mode = local === true ? lib.config.mode : local;
    if (!local) {
      if (key === 'characters' || key === 'cards') value = ['standard'];
      if (['connect_characters', 'connect_cards', 'extensions', 'plays'].includes(key)) value = [];
      if (key === 'extension_auto_import') value = false;
      if (key === 'connect_mode') value = 'identity';
      if (key === 'mode' && !['identity', 'connect'].includes(value)) value = 'identity';
    }
    if (mode === 'identity') {
      if (key === 'identity_mode' || key === 'connect_identity_mode') value = 'normal';
      if (key === 'cardpilename') value = '默认牌堆';
    }
    return saveConfig(key, value, local, callback);
  };
  for (const key of ['characters', 'cards', 'connect_characters', 'connect_cards', 'extensions', 'plays', 'extension_auto_import', 'connect_mode']) {
    game.saveConfig(key);
  }
  for (const key of ['identity_mode', 'connect_identity_mode']) game.saveConfig(key, 'normal', 'identity');
  game.saveConfig('cardpilename', '默认牌堆', 'identity');
  // 只清启动/续局标记，不清空数据库中的战绩和音量。
  for (const key of ['directstartmode', 'tmp_owner_roomId', 'tmp_user_roomId', 'continue_name']) game.saveConfig(key);
  // 精简包不带扩展壁纸、背景音乐和额外字体；保留音量及玩家自定义数据库资源。
  if (!String(lib.config.image_background || '').startsWith('custom_')) game.saveConfig('image_background', 'default');
  if (!['music_default', 'music_off'].includes(lib.config.background_music)) game.saveConfig('background_music', 'music_default');
  for (const key of ['name_font', 'identity_font', 'cardtext_font', 'global_font']) game.saveConfig(key, 'default');
  game.saveConfig('card_font', 'xiaozhuan');
  if (!lib.config.card_style) game.saveConfig('card_style', 'default');
  game.saveConfig('skin', {});
  game.saveConfig('connect_avatar', 'caocao');
  game.saveConfig('connect_avatar', 'caocao', 'connect');
  lib.config.all.card_font = ['xiaozhuan'];
  lib.config.all.stockmode = ['identity', 'connect'];
  lib.config.all.sgsmodes = ['identity', 'connect'];
  game.saveConfig('reconnect_info', []);
  game.saveConfig('modeorder', ['identity', 'connect']);
  for (const key of ['directstart', 'playback']) storage.removeItem(lib.configprefix + key);
  storage.setItem(lib.configprefix + 'disable_extension', 'true');
  lib.config.all.stockextension = [];
  lib.config.hiddenModePack = [];
  lib.config.all.sgscharacters = ['standard'];
  lib.config.all.sgscards = ['standard'];
  for (const [section, key] of [['config', 'identity_mode'], ['connect', 'connect_identity_mode']]) {
    const option = lib.mode.identity[section][key];
    option.item = { normal: '经典身份' };
    option.init = 'normal';
    option.intro = '经典身份 · 标准武将与标准牌';
  }
}
