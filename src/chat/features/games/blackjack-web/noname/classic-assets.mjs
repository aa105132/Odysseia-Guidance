import fs from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';

export const CARD_NAMES = 'sha shan tao juedou shunshou guohe wuxie nanman wanjian wuzhong taoyuan wugu jiedao lebu shandian bagua cixiong qinggang qinglong zhangba guanshi fangtian qilin hanbing renwang zhuge chitu dawan dilu jueying zhuahuang zixin'.split(' ');
const SKILL_AUDIO = 'jianxiong hujia fankui guicai tuxi luoyi tiandu yiji luoshen qingguo rende wusheng paoxiao guanxing tieji jizhi zhiheng jiuyuan qixi keji kurou yingzi fanjian guose liuli qianxun lianying xiaoji jieyin qingnang jijiu lijian biyue yaowu xinfu_jijie xinfu_jiyuan xunxun wangxi shenzhi ganglie xiaoguo shushen xinkuangfu wangzun tongji jijiang1 kongcheng1 longdan_sha yicong'.split(' ');

export async function standardCharacters(source) {
  const text = await fs.readFile(path.join(source, 'apps/core/character/standard/character.js'), 'utf8');
  return vm.runInNewContext(text.replace('export default characters;', '') + '\ncharacters;', {});
}

export async function copyClassicAssets(source, output) {
  const core = path.join(source, 'apps/core');
  const characters = await standardCharacters(source);
  const files = new Set();
  const add = file => files.add(file);
  for (const [name, character] of Object.entries(characters)) {
    add(character.img || `image/character/${name}.jpg`);
    for (const die of character.dieAudios || [name]) add(`audio/die/${die}.mp3`);
  }
  for (const sex of ['male', 'female', 'double']) add(`image/character/default_silhouette_${sex}.jpg`);
  for (const card of CARD_NAMES) {
    add(`image/card/${card}.png`);
    // 上游桃使用恢复音效，无独立 tao.mp3。
    if (card !== 'tao') for (const sex of ['male', 'female']) add(`audio/card/${sex}/${card}.mp3`);
  }
  add('audio/card/default.mp3');
  for (const skill of SKILL_AUDIO) for (const number of [1, 2]) add(`audio/skill/${skill}${number}.mp3`);
  for (const name of 'bagua cixiong qinggang qinglong zhangba guanshi fangtian qilin hanbing renwang zhuge'.split(' ')) add(`audio/skill/${name}_skill.mp3`);
  for (const name of ['zhu', 'zhong', 'fan', 'nei']) add(`image/card/identity_${name}.jpg`);
  for (const name of ['shield', 'charge', 'zhanfa', 'danqi_hufu', 'expandedSlots', 'cisha', 'tiesuo_mark']) add(`image/card/${name}.png`);
  for (const file of await fs.readdir(path.join(core, 'audio/effect'))) {
    if (/^(?:damage\w*|hujia_damage\w*|die_(?:male|female)|drawx?|discard|judge|link(?:_clear)?|lose(?:Hp|MaxHp)?|recover|tie|win|equip[1-5]|coin(?:_cost)?|throw_(?:egg|flower|shoe|wine)[12])\.mp3$/.test(file)) add(`audio/effect/${file}`);
  }
  add('audio/background/music_default.mp3');
  add('image/background/ol_bg.jpg');
  add('image/card/handcard.png');
  for (const group of ['wei', 'shu', 'wu', 'qun']) add(`image/card/group_${group}.png`);
  await fs.mkdir(path.join(output, 'image/emotion'), { recursive: true });
  await fs.writeFile(path.join(output, 'image/emotion/.keep'), '');
  for (const file of await fs.readdir(path.join(core, 'image/mode/identity/mark'))) if (/^sixiang_.*\.jpg$/.test(file)) add(`image/mode/identity/mark/${file}`);
  for (const file of ['image/splash/style1.jpg', 'image/splash/style1/identity.jpg', 'image/splash/style1/connect.jpg']) add(file);
  for (const file of [...files].sort()) {
    await fs.mkdir(path.dirname(path.join(output, file)), { recursive: true });
    await fs.copyFile(path.join(core, file), path.join(output, file));
  }
  await fs.mkdir(path.join(output, 'game'), { recursive: true });
  await fs.writeFile(path.join(output, 'game/asset.json'), JSON.stringify([...files].sort()));
  return [...files];
}
