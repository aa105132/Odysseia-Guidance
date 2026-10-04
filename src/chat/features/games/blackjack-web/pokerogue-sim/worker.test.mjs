import test from 'node:test';
import assert from 'node:assert/strict';
import {execute, validateSnapshot} from './worker.mjs';
const mon = (extra = {}) => ({species: 25, level: 100, moves: [85], ability: 9, nature: 'Hardy', ivs: [31,31,31,31,31,31], gender: 'M', shiny: false, ...extra});
const snap = (...team) => ({version: 1, team});
const copy = value => JSON.parse(JSON.stringify(value));
const start = (a, b) => execute({op: 'start', seed: [1,2,3,4], snapshots: [a,b]});
const choose = (s, seat, choice) => execute({op: 'choose', state: copy(s.state), seat, choice});

test('real Showdown serialization preserves private pending actions, RNG, forced switches and victory', () => {
  let s = start(snap(mon()), snap(mon({species: 129, ability: 33, moves: [150], level: 1}), mon({species: 129, ability: 33, moves: [150], level: 1})));
  assert.equal(s.turn, 1);
  const before = copy(s.views[1]);
  s = choose(s, 0, 'move 1');
  assert.equal(s.progressed, false);
  assert.equal(s.views[0].waiting, true);
  assert.deepEqual(s.views[1], before);
  const first = choose(s, 1, 'move 1');
  const comparable = value => { const result = copy(value); result.state.log = result.state.log.map(line => line.startsWith('|t:|') ? '|t:|CLOCK' : line); return result; };
  assert.deepEqual(comparable(first), comparable(choose(s, 1, 'move 1')), 'restoring RNG gives identical damage and state except wall-clock log timestamps');
  s = first;
  assert.equal(s.progressed, true);
  assert.equal(s.views[1].forceSwitch, true);
  assert.equal(s.views[0].waiting, true);
  assert.equal(s.views[1].own[0].fainted, true);
  assert.throws(() => choose(s, 1, 'move 1'));
  s = choose(s, 1, 'switch 2');
  assert.equal(s.progressed, true);
  assert.equal(s.turn, 2);
  s = choose(s, 0, 'move 1');
  s = choose(s, 1, 'move 1');
  assert.equal(s.ended, true);
  assert.equal(s.winner, 0);
  assert.ok(s.log.some(l => l === '|win|Seat 0'));
  assert.ok(s.log.every(l => !/\/(?!100\b)\d+/.test(l)));
});

test('zero EVs, preserved IVs/nature/gender, full PP, legacy moves and abilities', () => {
  const p = mon({moves: [237, 216], ability: 1, nature: 'Modest', ivs: [1,2,3,4,5,6]});
  const team = validateSnapshot(snap(p));
  assert.deepEqual(Object.values(team[0].evs), [0,0,0,0,0,0]);
  assert.equal(team[0].moves[0], 'Hidden Power');
  assert.deepEqual(Object.values(team[0].ivs), p.ivs);
  const s = start(snap(p), snap(mon()));
  assert.ok(s.views[0].own.every(p => p.hp === p.maxhp));
  assert.ok(s.views[0].moves.every(m => m.pp === m.maxpp));
});

test('voluntary switch and trapping are decided by Showdown', () => {
  let s = start(snap(mon(), mon({species: 1, ability: 65})), snap(mon({species: 202, ability: 23, moves: [150]})));
  assert.deepEqual(s.views[0].canSwitch, []);
  assert.throws(() => choose(s, 0, 'switch 2'), /trapped/i);
  s = start(snap(mon(), mon({species: 1, ability: 65})), snap(mon({moves: [150]})));
  assert.deepEqual(s.views[0].canSwitch, [2]);
  s = choose(s, 0, 'switch 2');
  s = choose(s, 1, 'move 1');
  assert.equal(s.views[0].own.find(p => p.active).species, 1);
  assert.equal(s.turn, 2);
});

test('Struggle after PP depletion, disabled move legality and bounded public log', () => {
  let s = start(snap(mon({moves: [150], ability: 23})), snap(mon({species: 202, ability: 23, moves: [150]})));
  for (let i = 0; i < 64; i++) { s = choose(s, 0, 'move 1'); s = choose(s, 1, 'move 1'); }
  assert.equal(s.turn, 65);
  assert.equal(s.views[0].moves[0].name, 'Struggle');
  assert.ok(s.log.length <= 20);
  s = choose(s, 0, 'move 1'); s = choose(s, 1, 'move 1');
  assert.ok(s.log.some(l => l.includes('|Struggle|')));
  assert.ok(s.views[0].own[0].hp < s.views[0].own[0].maxhp);
  console.log(`Showdown evidence: ${s.turn - 1} completed turns, PP exhausted, Struggle executed`);
});

test('strict unsupported snapshot and choice rejection', () => {
  for (const extra of [{species: 9999}, {ability: 999}, {moves: [999]}, {moves: [85,85]}, {nature: 'hardy'}, {gender: 'N'}, {ivs: [32,0,0,0,0,0]}, {evs: [252]}]) {
    assert.throws(() => validateSnapshot(snap(mon(extra))));
  }
  const s = start(snap(mon()), snap(mon()));
  assert.throws(() => choose(s, 0, 'move 4'));
  assert.throws(() => choose(s, 0, 'switch 1'));
  assert.throws(() => choose(s, 0, 'move 1 terastallize'));
});

test('Disable marks the actual request slot and rejects the disabled move', () => {
  let s = start(snap(mon({moves: [50,150]})), snap(mon({species: 129, ability: 33, moves: [150,33]})));
  s = choose(s, 0, 'move 2'); s = choose(s, 1, 'move 1');
  s = choose(s, 0, 'move 1'); s = choose(s, 1, 'move 2');
  assert.equal(s.views[1].moves[0].disabled, true);
  assert.throws(() => choose(s, 1, 'move 1'), /disabled/i);
  assert.equal(s.views[1].moves[1].disabled, false);
});

test('six-member auto order and all six faints reach a real winner', () => {
  let s = start(snap(mon()), snap(...Array.from({length: 6}, () => mon({species: 129, ability: 33, level: 1, moves: [150]}))));
  let epochs = 0;
  while (!s.ended && epochs < 20) {
    if (s.views[1].forceSwitch) s = choose(s, 1, `switch ${s.views[1].canSwitch[0]}`);
    else { s = choose(s, 0, 'move 1'); s = choose(s, 1, 'move 1'); }
    epochs++;
  }
  assert.equal(s.winner, 0);
  assert.equal(s.turn, 6);
  assert.equal(epochs, 11);
  assert.ok(s.views[1].own.every(p => p.fainted));
  console.log('Showdown evidence: six-Pokemon team defeated in 6 attack turns / 11 decision epochs');
});
