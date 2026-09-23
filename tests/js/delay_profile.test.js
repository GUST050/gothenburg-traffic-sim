// Executable checks for the delay-profile chart's variant semantics.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(ROOT, 'web', 'app.js'), 'utf8');

function extractFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notStrictEqual(start, -1, `${name} must remain executable UI logic`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`unterminated function ${name}`);
}

const context = vm.createContext({
  console,
  DELAY_SERIES: ['blue', 'orange'],
});
vm.runInContext([
  extractFunction('delayCandidateLabel'),
  extractFunction('delayProfileSeries'),
  extractFunction('delayProfileVisibleBinCount'),
  extractFunction('delayProfileUncertaintyLabel'),
].join('\n'), context, { filename: 'app.js:delay-profile' });

const seriesFor = vm.runInContext('delayProfileSeries', context);
const uncertaintyLabel = vm.runInContext(
  'delayProfileUncertaintyLabel', context);
const visibleBinCount = vm.runInContext(
  'delayProfileVisibleBinCount', context);

function candidate(variants) {
  return {
    schedule_id: 'closure-a',
    first_work_date: '2027-12-24',
    period_end: '2027-12-26',
    daily_start: '22:00',
    daily_end: '04:00',
    day_count: 3,
    closure_cost: { added_vehicle_hours: 2.5 },
    variants,
  };
}

const q50 = {
  vehicles: [4, 2, 1],
  vehicles_affected: 7,
  vehicles_no_detour: 0,
  added_vehicle_hours: 1.25,
};
const q50Only = {
  variants: ['q50'],
  direction_sensitivity_evaluated: false,
  candidates: [candidate({ q50 })],
};
const one = seriesFor(q50Only)[0];
assert.strictEqual(one.hasDirectionBand, false,
  'q50-only evidence must not be presented as an uncertainty band');
assert.deepStrictEqual(Array.from(one.low), [4, 2, 1]);
assert.deepStrictEqual(Array.from(one.high), [4, 2, 1]);
assert.strictEqual(one.hours, 1.25,
  'the q50 curve must use its own hours, not a worst-variant ranked total');
assert.match(uncertaintyLabel(q50Only), /endast q50/);
assert.match(uncertaintyLabel(q50Only), /inte utvärderad/);

const stress = {
  variants: ['q10', 'q50', 'q90'],
  direction_sensitivity_evaluated: true,
  candidates: [candidate({
    q10: { ...q50, vehicles: [2, 1, 0] },
    q50,
    q90: { ...q50, vehicles: [7, 3, 2] },
  })],
};
const band = seriesFor(stress)[0];
assert.strictEqual(band.hasDirectionBand, true);
assert.deepStrictEqual(Array.from(band.low), [2, 1, 0]);
assert.deepStrictEqual(Array.from(band.high), [7, 3, 2]);
assert.match(uncertaintyLabel(stress), /q10–q90/);

const minuteBins = [
  { from_s: 0, to_s: 0, label: '0 s' },
  { from_s: 0, to_s: 15, label: '0–15 s' },
  { from_s: 15, to_s: 30, label: '15–30 s' },
  { from_s: 30, to_s: 45, label: '30–45 s' },
  { from_s: 45, to_s: 60, label: '45–60 s' },
  { from_s: 300, to_s: null, label: '> 5 min' },
];
assert.strictEqual(visibleBinCount(
  { bins: minuteBins }, [{ high: [0, 3743, 2936, 0, 0, 0] }]), 5,
'an empty five-minute tail should end at the next whole minute');
assert.strictEqual(visibleBinCount(
  { bins: minuteBins }, [{ high: [0, 1, 0, 0, 0, 2] }]), 6,
'a non-empty overflow bucket must keep the full axis');

console.log('11/11 delay profile checks passed');
