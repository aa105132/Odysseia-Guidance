import fs from 'node:fs/promises';
import path from 'node:path';

// 固定上游标准武将依赖的四个技能，按顶层缩进边界提取，避免发布整个扩展包。
export async function addClassicSkills(source, output) {
  const definitions = [];
  const translations = [];
  for (const [pack, names] of [['refresh', ['xunxun', 'wangxi', 'reyicong']], ['sp', ['shenzhi']]]) {
    const skillText = await fs.readFile(path.join(source, `apps/core/character/${pack}/skill.js`), 'utf8');
    const translateText = await fs.readFile(path.join(source, `apps/core/character/${pack}/translate.js`), 'utf8');
    for (const name of names) {
      const matches = [...skillText.matchAll(new RegExp(`^\\t${name}: \\{[\\s\\S]*?^\\t\\},`, 'gm'))];
      if (matches.length !== 1) throw new Error('经典依赖技能提取不唯一：' + name);
      definitions.push(matches[0][0]);
      for (const key of [name, `${name}_info`]) {
        const match = translateText.match(new RegExp(`^\\t${key}: .*,$`, 'm'));
        if (!match) throw new Error('缺少技能翻译：' + key);
        translations.push(match[0]);
      }
    }
  }
  const file = path.join(output, 'character/standard.js');
  let text = await fs.readFile(file, 'utf8');
  const marker = 'game.import("character", function() {';
  if (text.split(marker).length !== 2) throw new Error('标准包注册入口不匹配');
  const additions = `// Classic-only dependencies from upstream refresh/sp, GPL-3.0.\nObject.assign(skills, {\n${definitions.join('\n')}\n});\nObject.assign(translates, {\n${translations.join('\n')}\n});\n`;
  text = text.replace(marker, additions + marker);
  await fs.writeFile(file, text);
  await fs.mkdir(path.join(output, 'integration-source'), { recursive: true });
  await fs.writeFile(path.join(output, 'integration-source/classic-skill-excerpts.js'), additions);
}
