import assert from 'node:assert/strict';
import test from 'node:test';
import { configureClassic, restrictClassicPackage, isClassicRoom } from './classic-policy.js';

function fixture() {
  const lib = {
    configprefix: 'noname_',
    config: {
      mode: 'guozhan', characters: ['extra'], cards: ['extra'], extensions: ['custom'], plays: ['boss'],
      mode_config: { identity: { identity_mode: 'purple', connect_identity_mode: 'stratagem' } },
      all: { stockextension: ['boss'], sgscharacters: ['extra'], sgscards: ['extra'] },
      gameRecord: { identity: { data: [1, 2] } }, volumn_audio: 3, image_background: 'custom_kept',
    },
    mode: { identity: { config: { identity_mode: { item: { normal: '标准', purple: '3v3v2' } } }, connect: { connect_identity_mode: { item: { normal: '标准', stratagem: '谋攻' } } } } },
  };
  const writes = [];
  const game = { saveConfig(key, value, local, callback) {
    const target = local ? (lib.config.mode_config[local === true ? lib.config.mode : local] ??= {}) : lib.config;
    if (value === undefined) delete target[key]; else target[key] = value;
    writes.push([key, value, local]); callback?.();
  } };
  const storage = new Map([['noname_directstart', 'true'], ['noname_playback', 'old'], ['unrelated', 'kept']]);
  configureClassic(lib, game, { removeItem: key => storage.delete(key), setItem: (key, value) => storage.set(key, value) });
  return { lib, game, storage, writes };
}

test('old expanded settings normalize without clearing unrelated records or preferences', () => {
  const { lib, storage } = fixture();
  assert.deepEqual(lib.config.characters, ['standard']);
  assert.deepEqual(lib.config.cards, ['standard']);
  assert.deepEqual(lib.config.extensions, []);
  assert.deepEqual(lib.config.plays, []);
  assert.equal(lib.config.mode_config.identity.identity_mode, 'normal');
  assert.equal(lib.config.mode_config.identity.connect_identity_mode, 'normal');
  assert.equal(lib.config.mode_config.identity.cardpilename, '默认牌堆');
  assert.deepEqual(lib.mode.identity.config.identity_mode.item, { normal: '经典身份' });
  assert.deepEqual(lib.mode.identity.connect.connect_identity_mode.item, { normal: '经典身份' });
  assert.deepEqual(lib.config.gameRecord.identity.data, [1, 2]);
  assert.equal(lib.config.volumn_audio, 3);
  assert.equal(lib.config.image_background, 'custom_kept');
  assert.deepEqual([...storage], [['unrelated', 'kept'], ['noname_disable_extension', 'true']]);
});

test('menu writes cannot restore expanded packs or submodes; ordinary options still save', () => {
  const { lib, game } = fixture();
  game.saveConfig('mode', 'identity');
  game.saveConfig('identity_mode', 'zhong', true);
  game.saveConfig('connect_identity_mode', 'purple', 'identity');
  game.saveConfig('characters', ['extra']);
  game.saveConfig('cards', ['standard', 'extra']);
  game.saveConfig('connect_cards', ['standard']);
  game.saveConfig('extensions', ['custom']);
  game.saveConfig('mode', 'boss');
  let completed = false;
  game.saveConfig('player_number', '4', 'identity', () => { completed = true; });
  assert.equal(completed, true);
  assert.equal(lib.config.mode, 'identity');
  assert.deepEqual(lib.config.characters, ['standard']);
  assert.deepEqual(lib.config.cards, ['standard']);
  assert.deepEqual(lib.config.connect_cards, []);
  assert.deepEqual(lib.config.extensions, []);
  assert.deepEqual(lib.config.mode_config.identity, { identity_mode: 'normal', connect_identity_mode: 'normal', cardpilename: '默认牌堆', player_number: '4' });
  game.saveConfig('mode', 'connect');
  assert.equal(lib.config.mode, 'connect');
});

for (const online of [false, true]) test(`only standard packages and identity${online ? '/connect transport' : ''} load`, () => {
  const pack = { character: { standard: '标准', extra: '神将' }, card: { standard: '标准', extra: '军争' }, mode: { identity: '身份', connect: '联机', guozhan: '国战', boss: '挑战' }, submode: { identity: { purple: '3v3v2' } }, play: { boss: '挑战' }, background: { test: '保留' } };
  restrictClassicPackage(pack, online);
  assert.deepEqual(pack.character, { standard: '标准' });
  assert.deepEqual(pack.card, { standard: '标准' });
  assert.deepEqual(Object.keys(pack.mode), online ? ['identity', 'connect'] : ['identity']);
  assert.deepEqual(pack.background, {});
  assert.deepEqual(pack.music, {});
  assert.deepEqual(pack.font, { xiaozhuan: '方正小篆体' });
  assert.deepEqual(pack.play, {});
  assert.deepEqual(pack.submode, { identity: { normal: '经典身份' } });
});

test('room list/join policy rejects other modes, expanded and unconfigured rooms', () => {
  const classic = { mode: 'identity', identity_mode: 'normal', characterPack: ['standard'], cardPack: ['standard'] };
  assert.equal(isClassicRoom(classic), true);
  for (const config of [null, {}, { ...classic, mode: 'guozhan' }, { ...classic, identity_mode: 'purple' }, { ...classic, characterPack: ['standard', 'extra'] }, { ...classic, cardPack: ['extra'] }, { ...classic, cardPack: [] }, { ...classic, characterPack: 'standard' }]) {
    assert.equal(isClassicRoom(config), false);
  }
});
