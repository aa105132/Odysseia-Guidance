import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const require = createRequire(import.meta.url);
const {Battle, Dex, TeamValidator} = require('pokemon-showdown');
const FORMAT = 'gen9customgame';
const STATS = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
const allowed = x => x.exists && (!x.isNonstandard || x.isNonstandard === 'Past');
const species = new Map(Dex.species.all().filter(s => allowed(s) && !s.forme && s.name === s.baseSpecies).map(s => [s.num, s]));
const moves = new Map(Dex.moves.all().filter(m => allowed(m) && !m.isZ && !m.isMax && !m.realMove && m.num > 0).map(m => [m.num, m]));
const abilities = new Map(Dex.abilities.all().filter(a => allowed(a) && a.num > 0).map(a => [a.num, a]));
const integer = (n, lo, hi) => Number.isInteger(n) && n >= lo && n <= hi;
function invalid(message) { const e = new Error(message); e.invalid = true; throw e; }
function keys(value, expected) {
  return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === expected.split(',').sort().join(',');
}
export function validateSnapshot(snapshot) {
  if (!keys(snapshot, 'version,team') || snapshot.version !== 1 || !Array.isArray(snapshot.team) || !integer(snapshot.team.length, 1, 6)) invalid('Invalid team snapshot');
  const team = snapshot.team.map(p => {
    if (!keys(p, 'species,level,moves,ability,nature,ivs,gender,shiny')) invalid('Unsupported Pokemon fields');
    const s = species.get(p.species), a = abilities.get(p.ability), n = Dex.natures.get(p.nature);
    if (!s || !a || !n.exists || n.name !== p.nature) invalid('Unsupported species, ability or English nature');
    if (!integer(p.level, 1, 100) || typeof p.shiny !== 'boolean' || !Array.isArray(p.ivs) || p.ivs.length !== 6 || !p.ivs.every(v => integer(v, 0, 31))) invalid('Invalid level, shiny or IVs');
    if (!['M', 'F', 'N'].includes(p.gender) || (s.gender ? p.gender !== s.gender : p.gender === 'N')) invalid('Gender does not match species');
    if (!Array.isArray(p.moves) || !integer(p.moves.length, 1, 4) || new Set(p.moves).size !== p.moves.length || !p.moves.every(m => Number.isInteger(m) && moves.has(m))) invalid('Unsupported or duplicate moves');
    return {name: s.name, species: s.name, level: p.level, moves: p.moves.map(m => moves.get(m).name), ability: a.name,
      nature: n.name, ivs: Object.fromEntries(STATS.map((k, i) => [k, p.ivs[i]])), evs: Object.fromEntries(STATS.map(k => [k, 0])),
      gender: p.gender, shiny: p.shiny, item: '', happiness: 255};
  });
  const problems = new TeamValidator(FORMAT).validateTeam(structuredClone(team));
  if (problems) invalid(problems.join('; ').slice(0, 300));
  return team;
}
function publicLog(lines) {
  const result = [];
  const labels = new Set(['turn', 'move', 'switch', 'drag', 'faint', 'win', 'tie', '-damage', '-heal', '-status', '-curestatus', '-boost', '-unboost', '-clearboost', '-clearallboost', '-supereffective', '-resisted', '-crit', '-miss', '-fail', '-immune', '-ability', '-weather', '-start', '-end', '-activate', '-sidestart', '-sideend', '-fieldstart', '-fieldend', '-mustrecharge', '-prepare', '-cant', 'cant']);
  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];
    // A split is secret/exact HP followed by the shared/percentage version.
    if (line.startsWith('|split|')) { line = lines[i + 2] || ''; i += 2; }
    // Custom Game may expose exact HP even in shared protocol lines.
    line = line.replace(/\b(\d+)\/(\d+)\b/g, (_, hp, max) => `${Number(hp) ? Math.max(1, Math.ceil(100 * Number(hp) / Number(max))) : 0}/100`);
    if (labels.has(line.split('|')[1])) result.push(line.replace(/[\x00-\x1f\x7f<>]/g, '').slice(0, 400));
  }
  return result.slice(-20);
}
function view(battle, seat) {
  const side = battle.sides[seat], foe = battle.sides[1 - seat].active[0];
  const req = side.activeRequest || {wait: true};
  const active = req.active?.[0];
  const force = !!req.forceSwitch?.[0];
  const waiting = battle.ended || !!req.wait || side.isChoiceDone();
  const own = side.pokemon.map(p => ({species: p.baseSpecies.num, name: p.baseSpecies.name, hp: p.hp, maxhp: p.maxhp,
    status: p.status || '', active: p.isActive, fainted: p.fainted}));
  const canSwitch = !battle.ended && !req.wait && (force || !(active?.trapped || side.active[0]?.trapped)) ? side.pokemon.flatMap((p, i) => !p.fainted && !p.isActive ? [i + 1] : []) : [];
  return {own, opponent: foe ? {species: foe.species.num, name: foe.species.name, hp: foe.hp ? Math.max(1, Math.ceil(100 * foe.hp / foe.maxhp)) : 0,
    maxhp: 100, status: foe.status || ''} : null,
    moves: !battle.ended && active ? active.moves.map((m, i) => ({slot: i + 1, name: m.move, pp: m.pp ?? 1, maxpp: m.maxpp ?? 1, disabled: !!m.disabled})) : [],
    canSwitch, forceSwitch: force, waiting};
}
export function execute(input) {
  if (input.op === 'validate') { validateSnapshot(input.snapshot); return {ok: true}; }
  let battle;
  if (input.op === 'start') {
    battle = new Battle({formatid: FORMAT, seed: input.seed});
    for (let i = 0; i < 2; i++) battle.setPlayer(`p${i + 1}`, {name: `Seat ${i}`, team: validateSnapshot(input.snapshots[i])});
    if (battle.requestState === 'teampreview') battle.makeChoices('team 123456'.slice(0, 5 + input.snapshots[0].team.length), 'team 123456'.slice(0, 5 + input.snapshots[1].team.length));
  } else {
    battle = Battle.fromJSON(input.state);
  }
  let progressed = false;
  if (input.op === 'choose') {
    if (![0, 1].includes(input.seat) || !/^(move [1-4]|switch [1-6])$/.test(input.choice)) invalid('Invalid choice');
    const side = battle.sides[input.seat];
    if (battle.ended || !side.requestState || side.isChoiceDone()) invalid('No decision requested');
    const commit = battle.commitChoices.bind(battle);
    battle.commitChoices = () => { progressed = true; return commit(); };
    if (!battle.choose(`p${input.seat + 1}`, input.choice)) invalid(side.choice.error || 'Illegal choice');
    delete battle.commitChoices;
  }
  const log = publicLog(battle.log);
  // Keep Showdown's internal log indices intact; only the public view is trimmed.
  // Server turn/epoch limits bound history, with an additional hard byte ceiling.
  if (battle.log.length > 20000 || battle.inputLog.length > 2000) throw new Error('Simulator history limit exceeded');
  const result = {state: battle.toJSON(), views: [view(battle, 0), view(battle, 1)], log,
    turn: battle.turn, progressed, ended: !!battle.ended, winner: battle.winner === 'Seat 0' ? 0 : battle.winner === 'Seat 1' ? 1 : null};
  if (JSON.stringify(result).length > 1500000) throw new Error('Simulator state limit exceeded');
  return result;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let size = 0; const chunks = [];
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > 2000000) process.exit(2);
    chunks.push(chunk);
  }
  try { process.stdout.write(JSON.stringify(execute(JSON.parse(Buffer.concat(chunks).toString('utf8'))))); }
  catch (error) { process.stdout.write(JSON.stringify({error: error.invalid ? String(error.message).slice(0, 300) : 'Simulator failure', invalid: !!error.invalid})); process.exitCode = 1; }
}
